"""Analysis specialist agent.

Responsible for running technical analysis (price action indicators
and sector grouping) on each instrument in the portfolio and persisting
results to the database.
"""

from __future__ import annotations

from typing import Any

import structlog
from langchain_core.tools import tool as langchain_tool

from agent.agents.base import AgentContext, BaseSpecialist
from agent.analysis.price_action import analyse_price_action
from agent.analysis.sector import analyse_sector
from agent.analysis.types import AnalysisResult
from agent.db.analysis import create_analysis, get_analyses_by_run_id
from agent.db.candles import query_candles

logger = structlog.get_logger(__name__)


class AnalysisSpecialist(BaseSpecialist):
    """Runs price action and sector analysis on portfolio instruments."""

    @property
    def name(self) -> str:
        return "analysis"

    @property
    def description(self) -> str:
        return (
            "Runs technical analysis (trend detection, support/resistance, "
            "momentum, sector grouping) on each instrument and persists "
            "results. Call after data collection is complete."
        )

    def get_system_prompt(self) -> str:
        return (
            "You are the technical analysis specialist for a trading portfolio agent. "
            "Your job is to:\n"
            "1. First run sector analysis using run_sector_analysis to group "
            "instruments by exchange/sector\n"
            "2. Then run price action analysis for each instrument using "
            "run_price_action_analysis\n\n"
            "Run sector analysis first because it provides context used by "
            "the price action analysis. Then call run_price_action_analysis "
            "for every instrument ID provided.\n\n"
            "If an instrument has very few candles, still run analysis — the "
            "indicators will handle sparse data gracefully."
        )

    def create_tools(self, ctx: AgentContext) -> list[Any]:

        @langchain_tool
        def run_sector_analysis(instrument_ids: str, instrument_metadata: str) -> str:
            """Run sector/exchange grouping analysis across all instruments.

            Args:
                instrument_ids: Comma-separated instrument IDs (e.g. "1010,1191")
                instrument_metadata: Pipe-separated entries of id:symbol:exchange
                    (e.g. "1010:BA:1|1191:LAR:1|2009:AV.L:2")

            Returns sector grouping results with best/worst performing sectors.
            """
            try:
                ids = [int(x.strip()) for x in instrument_ids.split(",") if x.strip()]
            except ValueError:
                return "ERROR: instrument_ids must be comma-separated integers"

            # Parse metadata
            instruments_for_sector: list[dict[str, Any]] = []
            meta_entries = instrument_metadata.split("|") if instrument_metadata else []
            meta_by_id: dict[int, dict[str, str]] = {}
            for entry in meta_entries:
                parts = entry.strip().split(":")
                if len(parts) >= 2:
                    try:
                        eid = int(parts[0])
                        meta_by_id[eid] = {
                            "symbol": parts[1],
                            "exchange": parts[2] if len(parts) > 2 else None,
                        }
                    except ValueError:
                        continue

            for iid in ids:
                meta = meta_by_id.get(iid, {"symbol": f"ID:{iid}", "exchange": None})
                instruments_for_sector.append({
                    "etoro_id": iid,
                    "symbol": meta["symbol"],
                    "exchange": meta.get("exchange"),
                })

            # Build candle map
            candle_map: dict[int, list[dict[str, Any]]] = {}
            for iid in ids:
                try:
                    candle_map[iid] = query_candles(ctx.db, iid, "1d")
                except Exception:
                    candle_map[iid] = []

            sector_result = analyse_sector(instruments_for_sector, candle_map)

            # Store sector result in a format the price action tool can use
            # by storing it in the tool's closure
            self._last_sector_result = sector_result

            lines = [f"Sector analysis complete:"]
            lines.append(f"Best sector: {sector_result.best_group}")
            lines.append(f"Worst sector: {sector_result.worst_group}")
            for name, group in sector_result.groups.items():
                lines.append(
                    f"  {name}: {group.instrument_count} instruments, "
                    f"avg return {group.avg_return_pct:+.2f}%"
                )
            return "\n".join(lines)

        @langchain_tool
        def run_price_action_analysis(instrument_id: int) -> str:
            """Run technical analysis on a single instrument and persist results.

            Args:
                instrument_id: The eToro instrument ID to analyse.

            Returns analysis summary with trend, strength, support, resistance.
            """
            try:
                candles = query_candles(ctx.db, instrument_id, "1d")
            except Exception as exc:
                return f"ERROR: Failed to query candles for {instrument_id}: {exc}"

            try:
                pa_result = analyse_price_action(candles)
            except Exception as exc:
                return f"ERROR: Price action analysis failed for {instrument_id}: {exc}"

            # Get sector context if available
            sector_result = getattr(self, "_last_sector_result", None)
            sector_ctx = None
            if sector_result is not None:
                # Find which group this instrument belongs to
                for group_name, group in sector_result.groups.items():
                    for etoro_id, _symbol, _ret in group.instruments:
                        if etoro_id == instrument_id:
                            sector_ctx = group
                            break
                    if sector_ctx is not None:
                        break

            analysis = AnalysisResult(
                instrument_etoro_id=instrument_id,
                price_action=pa_result,
                sector_context=sector_ctx,
            )

            try:
                db_fields = analysis.to_db_fields()
                create_analysis(
                    ctx.db,
                    instrument_etoro_id=instrument_id,
                    run_id=ctx.run_id,
                    **db_fields,
                )
            except Exception as exc:
                return f"ERROR: Failed to persist analysis for {instrument_id}: {exc}"

            return (
                f"Analysis for {instrument_id}: "
                f"trend={pa_result.trend}, "
                f"strength={pa_result.trend_strength:.2f}, "
                f"support={pa_result.support}, "
                f"resistance={pa_result.resistance}, "
                f"momentum={pa_result.momentum_signal}"
            )

        return [run_sector_analysis, run_price_action_analysis]

    def run_procedural(
        self,
        state: dict[str, Any],
        ctx: AgentContext,
    ) -> None:
        """Run sector and price action analysis (procedural)."""
        instrument_ids = state.get("instrument_ids", [])
        instrument_map = state.get("instrument_map", {})
        candle_counts = state.get("candle_counts", {})

        ids_with_candles = [
            iid for iid in instrument_ids if candle_counts.get(iid, 0) > 0
        ]
        if not ids_with_candles:
            return

        # Build data for sector analysis
        instruments_for_sector: list[dict[str, Any]] = []
        for iid in ids_with_candles:
            inst = instrument_map.get(iid, {})
            instruments_for_sector.append({
                "etoro_id": iid,
                "symbol": inst.get("symbol", f"ID:{iid}"),
                "exchange": inst.get("exchange_id", inst.get("exchange")),
            })

        candle_map: dict[int, list[dict[str, Any]]] = {}
        for iid in ids_with_candles:
            try:
                candle_map[iid] = query_candles(ctx.db, iid, "1d")
            except Exception:
                candle_map[iid] = []

        sector_result = None
        if instruments_for_sector:
            try:
                sector_result = analyse_sector(
                    instruments_for_sector, candle_map
                )
            except Exception as exc:
                logger.warning("sector_analysis_failed", error=str(exc))

        # Price action per instrument
        for iid in ids_with_candles:
            try:
                candles = candle_map.get(iid) or query_candles(
                    ctx.db, iid, "1d"
                )
                pa_result = analyse_price_action(candles)

                sector_ctx = None
                if sector_result:
                    for group in sector_result.groups.values():
                        for eid, _sym, _ret in group.instruments:
                            if eid == iid:
                                sector_ctx = group
                                break
                        if sector_ctx:
                            break

                analysis = AnalysisResult(
                    instrument_etoro_id=iid,
                    price_action=pa_result,
                    sector_context=sector_ctx,
                )
                db_fields = analysis.to_db_fields()
                create_analysis(
                    ctx.db,
                    instrument_etoro_id=iid,
                    run_id=ctx.run_id,
                    **db_fields,
                )
            except Exception as exc:
                logger.warning(
                    "analysis_failed", instrument_id=iid, error=str(exc)
                )

    def process_results(
        self,
        state: dict[str, Any],
        ctx: AgentContext,
    ) -> dict[str, Any]:
        """Count analyses created for this run."""
        analyses = get_analyses_by_run_id(ctx.db, ctx.run_id)
        # Clean up sector result from closure
        self._last_sector_result = None
        return {"analyses_created": len(analyses)}
