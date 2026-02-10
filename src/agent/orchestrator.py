"""Core orchestrator for the eToro trading agent data pipeline.

The orchestrator wires together the eToro API client and SurrealDB
data layer to execute the data-fetch portion of each agent run:

1. **Init** — generate a unique run ID
2. **Fetch portfolio** — get current positions, save snapshot to DB
3. **Fetch market data** — for each instrument in the portfolio,
   fetch candles and upsert instrument metadata

This module implements steps 1–3 of the 6-step run pipeline.
Steps 4–6 (analysis, LLM, report) will be added in later roadmap steps.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

import structlog
from pydantic import ValidationError
from surrealdb.connections.sync_template import SyncTemplate

from agent.config import Settings
from agent.db.candles import bulk_insert_candles
from agent.db.connection import get_connection
from agent.db.instruments import upsert_instrument
from agent.db.schema import apply_schema
from agent.db.snapshots import create_snapshot
from agent.etoro.client import EToroClient, EToroError
from agent.etoro.market_data import get_candles
from agent.etoro.models import Instrument, InstrumentSearchResponse
from agent.etoro.portfolio import get_portfolio

logger = structlog.get_logger(__name__)


# Valid run types for the agent pipeline
RunType = Literal["market_open", "market_close"]


class PipelineError(Exception):
    """Raised when the data pipeline fails fatally (e.g. portfolio fetch fails)."""


class Orchestrator:
    """Coordinates the data pipeline: eToro API → SurrealDB.

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
                "Orchestrator has no client — use as a context manager "
                "or pass client= to the constructor"
            )
        return self._client

    @property
    def db(self) -> SyncTemplate:
        """Return the SurrealDB connection (raises if not initialised)."""
        if self._db is None:
            raise RuntimeError(
                "Orchestrator has no DB connection — use as a context manager "
                "or pass db= to the constructor"
            )
        return self._db

    # ------------------------------------------------------------------
    # Data pipeline
    # ------------------------------------------------------------------

    def run_data_pipeline(self, run_type: RunType) -> dict[str, Any]:
        """Execute steps 1–3 of the agent run pipeline.

        1. **Init** — generate ``run_id``
        2. **Fetch portfolio** — save snapshot, extract instrument IDs
        3. **Fetch market data** — resolve instruments, fetch candles

        Args:
            run_type: ``"market_open"`` or ``"market_close"``.

        Returns:
            A summary dict with keys: ``run_id``, ``run_type``,
            ``snapshot_id``, ``instruments_processed``,
            ``instruments_failed``, ``candle_counts``, ``errors``.

        Raises:
            PipelineError: If the portfolio fetch fails (fatal).
            ValueError: If ``run_type`` is not a valid value.
        """
        # Validate run_type at runtime
        if run_type not in ("market_open", "market_close"):
            raise ValueError(
                f"Invalid run_type: {run_type!r}. "
                'Must be "market_open" or "market_close".'
            )

        # ---- Step 1: Init ----
        run_id = str(uuid.uuid4())
        logger.info("pipeline_start", run_id=run_id, run_type=run_type)

        errors: list[dict[str, Any]] = []

        # ---- Step 2: Fetch portfolio ----
        try:
            portfolio_resp = get_portfolio(self.client)
        except EToroError as exc:
            logger.error("portfolio_fetch_failed", error=str(exc))
            raise PipelineError(f"Portfolio fetch failed: {exc}") from exc

        portfolio = portfolio_resp.client_portfolio
        snapshot = create_snapshot(self.db, portfolio, run_type)
        snapshot_id = str(snapshot.get("id", ""))

        logger.info(
            "portfolio_snapshot_created",
            snapshot_id=snapshot_id,
            positions=len(portfolio.positions),
        )

        # Extract unique instrument IDs from open positions
        instrument_ids = sorted({pos.instrument_id for pos in portfolio.positions})

        if not instrument_ids:
            logger.warning("no_instruments_in_portfolio")
            return {
                "run_id": run_id,
                "run_type": run_type,
                "snapshot_id": snapshot_id,
                "instruments_processed": 0,
                "instruments_failed": 0,
                "candle_counts": {},
                "errors": [],
            }

        # ---- Step 3: Fetch market data ----
        # Resolve instrument metadata (single API call for the full catalog)
        instrument_map = self._resolve_instruments(instrument_ids)

        instruments_processed: list[int] = []
        candle_counts: dict[int, int] = {}

        for iid in instrument_ids:
            try:
                # Upsert instrument metadata if we resolved it
                if iid in instrument_map:
                    upsert_instrument(self.db, instrument_map[iid])
                else:
                    logger.warning(
                        "instrument_metadata_not_found", instrument_id=iid
                    )

                # Fetch and store candles
                candles = get_candles(self.client, iid)
                inserted = bulk_insert_candles(self.db, candles, iid, "1d")
                candle_counts[iid] = len(inserted)
                instruments_processed.append(iid)

                logger.info(
                    "instrument_processed",
                    instrument_id=iid,
                    symbol=instrument_map.get(iid, None)
                    and instrument_map[iid].symbol,
                    candles_inserted=len(inserted),
                )
            except Exception as exc:
                logger.warning(
                    "instrument_fetch_failed",
                    instrument_id=iid,
                    error=str(exc),
                )
                errors.append(
                    {"instrument_id": iid, "error": str(exc)}
                )

        summary: dict[str, Any] = {
            "run_id": run_id,
            "run_type": run_type,
            "snapshot_id": snapshot_id,
            "instruments_processed": len(instruments_processed),
            "instruments_failed": len(errors),
            "candle_counts": candle_counts,
            "errors": errors,
        }

        logger.info("pipeline_complete", **summary)
        return summary

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_instruments(
        self, instrument_ids: list[int]
    ) -> dict[int, Instrument]:
        """Fetch the full instrument catalog and return the subset we need.

        Makes a single GET to ``/market-data/instruments``, parses every
        item, and returns a dict keyed by ``instrument_id`` for only those
        IDs present in *instrument_ids*.

        If the request fails, an empty dict is returned so the pipeline can
        continue (candle fetches only need the instrument ID, not metadata).
        """
        try:
            response = self.client.get("/market-data/instruments")
            parsed = InstrumentSearchResponse.model_validate(response.json())

            wanted = set(instrument_ids)
            result: dict[int, Instrument] = {}

            for item in parsed.items:
                iid = item.get("instrumentID")
                if iid in wanted:
                    try:
                        result[iid] = Instrument.model_validate(item)
                    except ValidationError:
                        logger.warning(
                            "instrument_parse_failed", instrument_id=iid
                        )

            logger.info(
                "instruments_resolved",
                wanted=len(wanted),
                found=len(result),
            )
            return result
        except EToroError as exc:
            logger.warning(
                "instrument_resolution_failed", error=str(exc)
            )
            return {}
