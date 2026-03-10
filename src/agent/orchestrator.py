"""Core orchestrator for the eToro trading agent data pipeline.

The orchestrator coordinates a LangGraph multi-agent pipeline where
an LLM-powered orchestrator routes between specialist agents (data,
analysis, commentary, report) to execute the trading agent run.

The ``Orchestrator`` class preserves the same public API as the
procedural pipeline it replaces -- callers use it identically.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

import structlog
from langchain_google_genai import ChatGoogleGenerativeAI
from surrealdb.connections.sync_template import SyncTemplate

from agent.agents.base import AgentContext
from agent.agents.graph import build_pipeline_graph
from agent.config import Settings
from agent.db.connection import get_connection
from agent.db.run_log import complete_run_log, create_run_log, fail_run_log
from agent.db.schema import apply_schema
from agent.etoro.client import EToroClient
from agent.reporting.generator import Report
from agent.reporting.llm import generate_commentary

logger = structlog.get_logger(__name__)


def _create_routing_model(settings: Settings) -> ChatGoogleGenerativeAI | None:
    """Create the LLM model for orchestrator routing, or ``None``."""
    if not settings.llm_api_key:
        return None
    return ChatGoogleGenerativeAI(
        model=settings.llm_model,
        google_api_key=settings.llm_api_key,
    )


class PipelineError(Exception):
    """Raised when the data pipeline fails fatally (e.g. portfolio fetch fails)."""


class Orchestrator:
    """Coordinates the data pipeline: eToro API -> SurrealDB.

    Usage::

        with Orchestrator(settings) as orch:
            summary = orch.run_data_pipeline("market_open")
            print(summary)

    For testing, pre-built ``client`` and ``db`` handles can be injected
    so that HTTP calls are interceptable and the database is shared with
    test assertions::

        orch = Orchestrator(settings, client=mock_client, db=test_db)
        summary = orch.run_data_pipeline("market_open")
    """

    def __init__(
        self,
        settings: Settings,
        *,
        client: EToroClient | None = None,
        db: SyncTemplate | None = None,
    ) -> None:
        self._settings = settings
        self._client = client
        self._db = db
        self._owns_client = client is None
        self._owns_db = db is None
        self._db_ctx: Any = None

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> Orchestrator:
        if self._owns_client:
            self._client = EToroClient(self._settings)
            self._client.__enter__()
        if self._owns_db:
            self._db_ctx = get_connection(self._settings)
            self._db = self._db_ctx.__enter__()
            apply_schema(self._db)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        if self._owns_client and self._client is not None:
            self._client.__exit__(exc_type, exc_val, exc_tb)
            self._client = None
        if self._owns_db and self._db_ctx is not None:
            self._db_ctx.__exit__(exc_type, exc_val, exc_tb)
            self._db = None
            self._db_ctx = None

    # ------------------------------------------------------------------
    # Property accessors (guard against use outside context manager)
    # ------------------------------------------------------------------

    @property
    def client(self) -> EToroClient:
        """Return the eToro API client (raises if not initialised)."""
        if self._client is None:
            raise RuntimeError(
                "Orchestrator has no client -- use as a context manager "
                "or pass client= to the constructor"
            )
        return self._client

    @property
    def db(self) -> SyncTemplate:
        """Return the SurrealDB connection (raises if not initialised)."""
        if self._db is None:
            raise RuntimeError(
                "Orchestrator has no DB connection -- use as a context manager "
                "or pass db= to the constructor"
            )
        return self._db

    # ------------------------------------------------------------------
    # Data pipeline
    # ------------------------------------------------------------------

    def run_data_pipeline(self, run_type: str) -> dict[str, Any]:
        """Execute the agent pipeline via LangGraph multi-agent routing.

        An LLM-powered orchestrator routes between specialist agents
        (data, analysis, commentary, report) to execute the pipeline.

        Args:
            run_type: ``"market_open"`` or ``"market_close"``.

        Returns:
            A summary dict with keys: ``run_id``, ``run_type``,
            ``snapshot_id``, ``instruments_processed``,
            ``instruments_failed``, ``candle_counts``,
            ``analyses_created``, ``commentary``, ``report``,
            ``errors``.

        Raises:
            PipelineError: If the pipeline fails fatally.
            ValueError: If ``run_type`` is not a valid value.
        """
        if run_type not in ("market_open", "market_close"):
            raise ValueError(
                f"Invalid run_type: {run_type!r}. "
                'Must be "market_open" or "market_close".'
            )

        # ---- Init ----
        run_id = str(uuid.uuid4())
        logger.info("pipeline_start", run_id=run_id, run_type=run_type)
        t0 = time.perf_counter()

        create_run_log(self.db, run_id=run_id, run_type=run_type)

        # ---- Build agent context and graph ----
        ctx = AgentContext(
            db=self.db,
            client=self.client,
            settings=self._settings,
            run_id=run_id,
            run_type=run_type,
            generate_fn=generate_commentary,
        )

        # Use LLM-powered routing when an API key is available,
        # otherwise fall back to deterministic ordering.
        model = _create_routing_model(self._settings)

        # Import specialists (triggers auto-registration on first import)
        import agent.agents.specialists  # noqa: F401
        from agent.agents.registry import get_all_specialists

        specialists = get_all_specialists()
        graph = build_pipeline_graph(specialists, model, ctx)

        # ---- Execute the graph ----
        initial_state: dict[str, Any] = {
            "run_id": run_id,
            "run_type": run_type,
            "next_specialist": "",
            "completed_stages": [],
            "instrument_ids": [],
            "instrument_map": {},
            "candle_counts": {},
            "analyses_created": 0,
            "news_context": None,
            "commentary": None,
            "report": None,
            "report_path": None,
            "errors": [],
            "start_time": t0,
        }

        try:
            result = graph.invoke(initial_state)
        except Exception as exc:
            logger.error("pipeline_graph_failed", error=str(exc))
            duration_ms = int((time.perf_counter() - t0) * 1000)
            fail_run_log(
                self.db,
                run_id=run_id,
                errors=[{"step": "graph", "error": str(exc)}],
                duration_ms=duration_ms,
            )
            raise PipelineError(f"Pipeline failed: {exc}") from exc

        # ---- Build summary ----
        duration_ms = int((time.perf_counter() - t0) * 1000)
        errors = result.get("errors", [])
        commentary = result.get("commentary")
        candle_counts = result.get("candle_counts", {})
        instruments_processed = sum(
            1 for c in candle_counts.values() if c > 0
        )
        instrument_ids = result.get("instrument_ids", [])
        instruments_failed = len(instrument_ids) - instruments_processed

        recommendations_made = 0
        if commentary and "recommendations" in commentary:
            recommendations_made = len(commentary["recommendations"])

        complete_run_log(
            self.db,
            run_id=run_id,
            instruments_analysed=instruments_processed,
            recommendations_made=recommendations_made,
            duration_ms=duration_ms,
        )

        summary: dict[str, Any] = {
            "run_id": run_id,
            "run_type": run_type,
            "snapshot_id": result.get("snapshot_id", ""),
            "instruments_processed": instruments_processed,
            "instruments_failed": instruments_failed,
            "candle_counts": candle_counts,
            "analyses_created": result.get("analyses_created", 0),
            "commentary": commentary,
            "report": result.get("report"),
            "errors": errors,
            "duration_ms": duration_ms,
        }

        logger.info("pipeline_complete", **summary)
        return summary
