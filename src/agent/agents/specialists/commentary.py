"""Commentary specialist agent.

Wraps the existing LLM commentary generation pipeline (build request,
call Gemini, persist report and recommendations) as LangChain tools.
"""

from __future__ import annotations

from typing import Any

import structlog
from langchain_core.tools import tool as langchain_tool

from agent.agents.base import AgentContext, BaseSpecialist
from agent.db.analysis import get_analyses_by_run_id
from agent.db.reports import create_recommendation, create_report
from agent.db.snapshots import get_latest_snapshot
from agent.reporting.llm import (
    CommentaryResponse,
    build_commentary_request,
    generate_commentary,
)

logger = structlog.get_logger(__name__)


class CommentarySpecialist(BaseSpecialist):
    """Generates LLM market commentary and persists report + recommendations."""

    @property
    def name(self) -> str:
        return "commentary"

    @property
    def description(self) -> str:
        return (
            "Generates LLM-powered market commentary, per-position assessments, "
            "and actionable recommendations. Persists the report to the database. "
            "Call after analysis is complete."
        )

    def get_system_prompt(self) -> str:
        return (
            "You are the market commentary specialist for a trading portfolio agent. "
            "Your job is to:\n"
            "1. Build the commentary request from portfolio and analysis data "
            "using build_commentary_request\n"
            "2. Generate LLM commentary using generate_llm_commentary\n"
            "3. Persist the report and recommendations using persist_report\n\n"
            "Call these tools in order. If the LLM API key is not configured, "
            "build_commentary_request will indicate this — you can skip the "
            "remaining steps in that case."
        )

    def create_tools(self, ctx: AgentContext) -> list[Any]:

        @langchain_tool
        def build_commentary_request_tool() -> str:
            """Build the LLM commentary request from portfolio snapshot and analysis data.

            Reads the latest snapshot and analyses from the database,
            assembles them into the CommentaryRequest structure.

            Returns a summary of the request data or an error.
            """
            if not ctx.settings.llm_api_key:
                return "SKIP: No LLM API key configured. Commentary will be skipped."

            analyses = get_analyses_by_run_id(ctx.db, ctx.run_id)
            if not analyses:
                return "SKIP: No analyses found for this run. Cannot generate commentary."

            snapshot = get_latest_snapshot(ctx.db)
            if snapshot is None:
                return "SKIP: No portfolio snapshot found."

            # Enrich analyses with instrument_etoro_id
            enriched_analyses: list[dict[str, Any]] = []
            for a in analyses:
                enriched = dict(a)
                inst_ref = a.get("instrument")
                if hasattr(inst_ref, "id"):
                    enriched["instrument_etoro_id"] = inst_ref.id
                elif isinstance(inst_ref, str) and ":" in inst_ref:
                    try:
                        enriched["instrument_etoro_id"] = int(
                            inst_ref.split(":", 1)[1]
                        )
                    except (ValueError, IndexError):
                        pass
                enriched_analyses.append(enriched)

            # Build instrument map from DB
            from agent.db.instruments import list_instruments

            db_instruments = list_instruments(ctx.db)
            inst_map_plain: dict[int, dict[str, Any]] = {}
            for inst in db_instruments:
                eid = inst.get("etoro_id")
                if eid is not None:
                    inst_map_plain[int(eid)] = {
                        "etoro_id": int(eid),
                        "symbol": inst.get("symbol", f"ID:{eid}"),
                        "name": inst.get("name", "Unknown"),
                    }

            request = build_commentary_request(
                run_type=ctx.run_type,
                snapshot=snapshot,
                analyses=enriched_analyses,
                instrument_map=inst_map_plain,
            )

            # Store on instance for the next tool to use
            self._commentary_request = request

            return (
                f"Commentary request built: {len(request.positions)} positions, "
                f"{len(request.sectors)} sectors, "
                f"total_value=${request.total_value:,.2f}"
            )

        @langchain_tool
        def generate_llm_commentary() -> str:
            """Generate LLM market commentary using the assembled request.

            Must be called after build_commentary_request_tool.
            Sends the request to Gemini and returns the structured response.
            """
            request = getattr(self, "_commentary_request", None)
            if request is None:
                return "ERROR: Must call build_commentary_request_tool first."

            try:
                gen_fn = ctx.generate_fn or generate_commentary
                response = gen_fn(request, ctx.settings)
                self._commentary_response = response
                return (
                    f"Commentary generated:\n"
                    f"Summary: {response.summary}\n"
                    f"Recommendations: {len(response.recommendations)}\n"
                    f"Position commentaries: {len(response.position_commentaries)}"
                )
            except Exception as exc:
                logger.error("commentary_generation_failed", error=str(exc))
                return f"ERROR: LLM commentary generation failed: {exc}"

        @langchain_tool
        def persist_report(snapshot_id: str) -> str:
            """Persist the LLM commentary report and recommendations to the database.

            Args:
                snapshot_id: The portfolio snapshot ID to link the report to.

            Must be called after generate_llm_commentary.
            """
            response: CommentaryResponse | None = getattr(
                self, "_commentary_response", None
            )
            if response is None:
                return "ERROR: Must call generate_llm_commentary first."

            # Render markdown
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
                        f"| {r.symbol} | {r.action.upper()} | {r.conviction} "
                        f"| {r.reasoning} |"
                    )
                lines.append("")
            report_markdown = "\n".join(lines)

            try:
                report_record = create_report(
                    ctx.db,
                    run_id=ctx.run_id,
                    run_type=ctx.run_type,
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
                return f"ERROR: Failed to persist report: {exc}"

            # Persist individual recommendations
            analyses = get_analyses_by_run_id(ctx.db, ctx.run_id)
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
                            ctx.db,
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
                "commentary_persisted",
                report_id=report_id,
                recommendations=len(response.recommendations),
            )
            self._report_id = report_id
            return f"Report persisted: {report_id} with {len(response.recommendations)} recommendations"

        return [
            build_commentary_request_tool,
            generate_llm_commentary,
            persist_report,
        ]

    def run_procedural(
        self,
        state: dict[str, Any],
        ctx: AgentContext,
    ) -> None:
        """Generate commentary and persist report (procedural)."""
        if not ctx.settings.llm_api_key:
            return

        analyses = get_analyses_by_run_id(ctx.db, ctx.run_id)
        if not analyses:
            return

        snapshot = get_latest_snapshot(ctx.db)
        if snapshot is None:
            return

        # Enrich analyses with instrument_etoro_id
        enriched: list[dict[str, Any]] = []
        for a in analyses:
            ea = dict(a)
            inst_ref = a.get("instrument")
            if hasattr(inst_ref, "id"):
                ea["instrument_etoro_id"] = inst_ref.id
            elif isinstance(inst_ref, str) and ":" in inst_ref:
                try:
                    ea["instrument_etoro_id"] = int(
                        inst_ref.split(":", 1)[1]
                    )
                except (ValueError, IndexError):
                    pass
            enriched.append(ea)

        # Build instrument map
        from agent.db.instruments import list_instruments

        db_instruments = list_instruments(ctx.db)
        inst_map: dict[int, dict[str, Any]] = {}
        for inst in db_instruments:
            eid = inst.get("etoro_id")
            if eid is not None:
                inst_map[int(eid)] = {
                    "etoro_id": int(eid),
                    "symbol": inst.get("symbol", f"ID:{eid}"),
                    "name": inst.get("name", "Unknown"),
                }

        request = build_commentary_request(
            run_type=ctx.run_type,
            snapshot=snapshot,
            analyses=enriched,
            instrument_map=inst_map,
        )

        # Generate commentary via ctx.generate_fn (patchable in tests)
        gen_fn = ctx.generate_fn or generate_commentary
        try:
            response = gen_fn(request, ctx.settings)
        except Exception as exc:
            logger.warning("commentary_failed", error=str(exc))
            state.setdefault("errors", []).append({
                "step": "commentary",
                "error": str(exc),
            })
            return

        self._commentary_response = response

        # Persist report
        snapshot_id = state.get("snapshot_id", "")
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
                    f"| {r.symbol} | {r.action.upper()} | {r.conviction} "
                    f"| {r.reasoning} |"
                )
            lines.append("")
        report_markdown = "\n".join(lines)

        try:
            report_record = create_report(
                ctx.db,
                run_id=ctx.run_id,
                run_type=ctx.run_type,
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
            self._report_id = str(report_record.get("id", ""))
        except Exception as exc:
            logger.error("report_persist_failed", error=str(exc))
            return

        # Persist individual recommendations
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
                        ctx.db,
                        report_id=self._report_id,
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

    def process_results(
        self,
        state: dict[str, Any],
        ctx: AgentContext,
    ) -> dict[str, Any]:
        """Build the commentary dict from the stored response."""
        response: CommentaryResponse | None = getattr(
            self, "_commentary_response", None
        )
        report_id: str | None = getattr(self, "_report_id", None)

        # Clean up
        self._commentary_request = None
        self._commentary_response = None
        self._report_id = None

        if response is None:
            return {"commentary": None}

        commentary: dict[str, Any] = {
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
        if report_id:
            commentary["report_id"] = report_id

        return {"commentary": commentary}
