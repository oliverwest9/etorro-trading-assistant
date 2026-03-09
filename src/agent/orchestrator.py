"""Core orchestrator for the eToro trading agent data pipeline.

The orchestrator wires together the eToro API client and SurrealDB
data layer to execute the data-fetch and analysis portion of each
agent run:

1. **Init** — generate a unique run ID
2. **Fetch portfolio** — get current positions, save snapshot to DB
3. **Fetch market data** — for each instrument in the portfolio,
   fetch candles and upsert instrument metadata
4. **Analyse** — run price-action indicators and sector grouping,
   persist results to the ``analysis`` table
5. **Commentary** — send portfolio + analysis data to the LLM,
   persist the report and recommendations to the DB
6. **Report** — assemble a ``Report`` object from pipeline data
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from pydantic import ValidationError
from surrealdb.connections.sync_template import SyncTemplate

from agent.analysis.price_action import analyse_price_action
from agent.analysis.sector import analyse_sector
from agent.analysis.types import AnalysisResult
from agent.config import Settings
from agent.db.analysis import create_analysis, get_analyses_by_run_id
from agent.db.candles import bulk_insert_candles, query_candles
from agent.db.connection import get_connection
from agent.db.instruments import list_instruments, upsert_instrument
from agent.db.reports import create_report, create_recommendation
from agent.db.schema import apply_schema
from agent.db.snapshots import create_snapshot
from agent.etoro.client import EToroClient, EToroError
from agent.etoro.market_data import get_candles
from agent.etoro.models import Instrument, InstrumentSearchResponse
from agent.etoro.portfolio import get_portfolio
from agent.reporting.generator import Report, generate_report
from agent.reporting.llm import (
    CommentaryResponse,
    build_commentary_request,
    format_prompt,
    generate_commentary,
)
from agent.types import RunType

logger = structlog.get_logger(__name__)


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

    def run_data_pipeline(self, run_type: str) -> dict[str, Any]:
        """Execute steps 1–6 of the agent run pipeline.

        1. **Init** — generate ``run_id``
        2. **Fetch portfolio** — save snapshot, extract instrument IDs
        3. **Fetch market data** — resolve instruments, fetch candles
        4. **Analyse** — run indicators, sector grouping, persist results
        5. **Commentary** — generate LLM commentary, persist report
        6. **Report** — assemble a ``Report`` object from pipeline data

        Args:
            run_type: ``"market_open"`` or ``"market_close"``.

        Returns:
            A summary dict with keys: ``run_id``, ``run_type``,
            ``snapshot_id``, ``instruments_processed``,
            ``instruments_failed``, ``candle_counts``,
            ``analyses_created``, ``commentary``, ``report``,
            ``errors``.

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
            empty_summary: dict[str, Any] = {
                "run_id": run_id,
                "run_type": run_type,
                "snapshot_id": snapshot_id,
                "instruments_processed": 0,
                "instruments_failed": 0,
                "candle_counts": {},
                "analyses_created": 0,
                "commentary": None,
                "errors": [],
            }
            empty_summary["report"] = generate_report(empty_summary, self.db)
            return empty_summary

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

        # ---- Step 4: Analyse ----
        analyses_created = self._run_analysis(
            run_id=run_id,
            instrument_ids=instruments_processed,
            instrument_map=instrument_map,
            errors=errors,
        )

        # ---- Step 5: LLM Commentary ----
        commentary_result = self._run_commentary(
            run_id=run_id,
            run_type=run_type,
            snapshot_id=snapshot_id,
            snapshot=snapshot,
            instrument_map=instrument_map,
            errors=errors,
        )

        summary: dict[str, Any] = {
            "run_id": run_id,
            "run_type": run_type,
            "snapshot_id": snapshot_id,
            "instruments_processed": len(instruments_processed),
            "instruments_failed": len(errors),
            "candle_counts": candle_counts,
            "analyses_created": analyses_created,
            "commentary": commentary_result,
            "errors": errors,
        }

        # ---- Step 6: Assemble Report ----
        summary["report"] = generate_report(summary, self.db)

        logger.info("pipeline_complete", **summary)
        return summary

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _run_analysis(
        self,
        run_id: str,
        instrument_ids: list[int],
        instrument_map: dict[int, Instrument],
        errors: list[dict[str, Any]],
    ) -> int:
        """Step 4: analyse each instrument and persist results.

        Runs price-action indicators on each instrument's candle data,
        then performs a sector/exchange grouping analysis.  Results are
        persisted to the ``analysis`` table.

        Args:
            run_id: Current pipeline run ID.
            instrument_ids: Successfully processed instrument IDs.
            instrument_map: Resolved instrument metadata.
            errors: Mutable list to append non-fatal errors to.

        Returns:
            Number of analysis records created.
        """
        if not instrument_ids:
            return 0

        logger.info("analysis_start", instrument_count=len(instrument_ids))

        # Build candle map for all instruments
        candle_map: dict[int, list[dict[str, Any]]] = {}
        for iid in instrument_ids:
            try:
                candle_map[iid] = query_candles(self.db, iid, "1d")
            except Exception as exc:
                logger.warning(
                    "analysis_candle_query_failed",
                    instrument_id=iid,
                    error=str(exc),
                )
                candle_map[iid] = []

        # Build an instruments list for sector analysis
        instruments_for_sector: list[dict[str, Any]] = []
        for iid in instrument_ids:
            inst = instrument_map.get(iid)
            if inst is not None:
                instruments_for_sector.append({
                    "etoro_id": inst.instrument_id,
                    "symbol": inst.symbol,
                    "exchange": str(inst.exchange_id) if inst.exchange_id is not None else None,
                })
            else:
                instruments_for_sector.append({
                    "etoro_id": iid,
                    "symbol": f"ID:{iid}",
                    "exchange": None,
                })

        # Run sector analysis (pure function)
        sector_result = analyse_sector(instruments_for_sector, candle_map)

        # Map instrument → sector group for context
        instrument_group: dict[int, str] = {}
        for group_name, group in sector_result.groups.items():
            for etoro_id, _symbol, _ret in group.instruments:
                instrument_group[etoro_id] = group_name

        # Analyse each instrument and persist
        analyses_created = 0
        for iid in instrument_ids:
            try:
                candles = candle_map.get(iid, [])
                pa_result = analyse_price_action(candles)

                # Build AnalysisResult with sector context
                group_name = instrument_group.get(iid)
                sector_ctx = (
                    sector_result.groups[group_name]
                    if group_name and group_name in sector_result.groups
                    else None
                )
                analysis = AnalysisResult(
                    instrument_etoro_id=iid,
                    price_action=pa_result,
                    sector_context=sector_ctx,
                )

                # Persist to DB
                db_fields = analysis.to_db_fields()
                create_analysis(
                    self.db,
                    instrument_etoro_id=iid,
                    run_id=run_id,
                    **db_fields,
                )
                analyses_created += 1

                logger.debug(
                    "instrument_analysed",
                    instrument_id=iid,
                    trend=pa_result.trend,
                    trend_strength=pa_result.trend_strength,
                )
            except Exception as exc:
                logger.warning(
                    "analysis_failed",
                    instrument_id=iid,
                    error=str(exc),
                )
                errors.append(
                    {"instrument_id": iid, "error": f"analysis: {exc}"}
                )

        logger.info(
            "analysis_complete",
            analyses_created=analyses_created,
            total_instruments=len(instrument_ids),
        )
        return analyses_created

    def _run_commentary(
        self,
        run_id: str,
        run_type: str,
        snapshot_id: str,
        snapshot: dict[str, Any],
        instrument_map: dict[int, Instrument],
        errors: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """Step 5: generate LLM commentary and persist to the DB.

        Fetches the analyses created in step 4, builds the commentary
        request, calls Gemini, persists the report and individual
        recommendation records.

        If the LLM API key is not configured or the call fails, the
        error is logged and ``None`` is returned so the pipeline can
        still complete.

        Returns:
            A dict with ``report_id``, ``summary``, ``market_context``,
            ``position_commentaries``, and ``recommendations`` keys,
            or ``None`` if commentary generation was skipped/failed.
        """
        if not self._settings.llm_api_key:
            logger.warning("llm_skipped_no_api_key")
            return None

        logger.info("commentary_start", run_id=run_id)

        # Fetch analyses from this run
        analyses = get_analyses_by_run_id(self.db, run_id)
        if not analyses:
            logger.warning("commentary_skipped_no_analyses")
            return None

        # Build instrument map as plain dicts for the LLM module
        inst_map_plain: dict[int, dict[str, Any]] = {}
        for iid, inst in instrument_map.items():
            inst_map_plain[iid] = {
                "etoro_id": inst.instrument_id,
                "symbol": inst.symbol,
                "name": inst.name,
            }

        # Enrich analyses with instrument_etoro_id for build_commentary_request
        enriched_analyses: list[dict[str, Any]] = []
        for a in analyses:
            enriched = dict(a)
            # The DB stores instrument as RecordID; extract the numeric ID
            inst_ref = a.get("instrument")
            if hasattr(inst_ref, "id"):
                enriched["instrument_etoro_id"] = inst_ref.id
            elif isinstance(inst_ref, str) and ":" in inst_ref:
                try:
                    enriched["instrument_etoro_id"] = int(inst_ref.split(":", 1)[1])
                except (ValueError, IndexError):
                    pass
            enriched_analyses.append(enriched)

        try:
            request = build_commentary_request(
                run_type=run_type,
                snapshot=snapshot,
                analyses=enriched_analyses,
                instrument_map=inst_map_plain,
            )
            response = generate_commentary(request, self._settings)
        except Exception as exc:
            logger.error("commentary_failed", error=str(exc))
            errors.append({"step": "commentary", "error": str(exc)})
            return None

        # Persist report
        report_markdown = self._render_commentary_markdown(response)
        try:
            report_record = create_report(
                self.db,
                run_id=run_id,
                run_type=run_type,
                snapshot_id=snapshot_id,
                commentary=response.market_context,
                summary=response.summary,
                report_markdown=report_markdown,
                recommendations=[
                    {
                        "symbol": r.symbol,
                        "action": r.action,
                        "conviction": r.conviction,
                        "reasoning": r.reasoning,
                    }
                    for r in response.recommendations
                ],
            )
            report_id = str(report_record.get("id", ""))
        except Exception as exc:
            logger.error("report_persist_failed", error=str(exc))
            errors.append({"step": "report_persist", "error": str(exc)})
            return None

        # Persist individual recommendation records
        # Build a lookup from instrument_etoro_id -> analysis record id
        analysis_id_by_instrument: dict[int, str] = {}
        for a in analyses:
            inst_ref = a.get("instrument")
            etoro_id = None
            if hasattr(inst_ref, "id"):
                etoro_id = inst_ref.id
            elif isinstance(inst_ref, str) and ":" in inst_ref:
                try:
                    etoro_id = int(inst_ref.split(":", 1)[1])
                except (ValueError, IndexError):
                    pass
            if etoro_id is not None:
                analysis_id_by_instrument[etoro_id] = str(a.get("id", ""))

        for rec in response.recommendations:
            try:
                analysis_id = analysis_id_by_instrument.get(
                    rec.instrument_id, ""
                )
                if analysis_id:
                    create_recommendation(
                        self.db,
                        report_id=report_id,
                        instrument_etoro_id=rec.instrument_id,
                        action=rec.action,
                        conviction=rec.conviction,
                        reasoning=rec.reasoning,
                        analysis_id=analysis_id,
                    )
            except Exception as exc:
                logger.warning(
                    "recommendation_persist_failed",
                    symbol=rec.symbol,
                    error=str(exc),
                )

        logger.info(
            "commentary_complete",
            report_id=report_id,
            recommendations=len(response.recommendations),
        )

        return {
            "report_id": report_id,
            "summary": response.summary,
            "market_context": response.market_context,
            "position_commentaries": [
                {"symbol": pc.symbol, "commentary": pc.commentary}
                for pc in response.position_commentaries
            ],
            "recommendations": [
                {
                    "symbol": r.symbol,
                    "action": r.action,
                    "conviction": r.conviction,
                    "reasoning": r.reasoning,
                }
                for r in response.recommendations
            ],
        }

    @staticmethod
    def _render_commentary_markdown(response: CommentaryResponse) -> str:
        """Render a ``CommentaryResponse`` as a markdown string."""
        lines: list[str] = []
        lines.append("## LLM Commentary\n")
        lines.append(f"**{response.summary}**\n")
        lines.append(response.market_context)
        lines.append("")

        if response.position_commentaries:
            lines.append("### Position Analysis\n")
            for pc in response.position_commentaries:
                lines.append(f"**{pc.symbol}**: {pc.commentary}\n")

        if response.recommendations:
            lines.append("### Recommendations\n")
            lines.append("| Symbol | Action | Conviction | Reasoning |")
            lines.append("|---|---|---|---|")
            for r in response.recommendations:
                lines.append(
                    f"| {r.symbol} | {r.action.upper()} | {r.conviction} | {r.reasoning} |"
                )
            lines.append("")

        return "\n".join(lines)

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
