"""Report specialist agent.

Assembles the final report from pipeline data, saves the markdown file,
and displays the terminal output.  This specialist is procedural — its
tools are called in a fixed order without LLM reasoning.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog
from langchain_core.tools import tool as langchain_tool

from agent.agents.base import AgentContext, BaseSpecialist
from agent.reporting.formatter import format_markdown, format_terminal
from agent.reporting.generator import generate_report

logger = structlog.get_logger(__name__)


class ReportSpecialist(BaseSpecialist):
    """Assembles, saves, and displays the final advisory report."""

    @property
    def name(self) -> str:
        return "report"

    @property
    def description(self) -> str:
        return (
            "Assembles the final advisory report from all pipeline data, "
            "saves it as a markdown file, and displays it in the terminal. "
            "Call this last, after commentary is complete."
        )

    def get_system_prompt(self) -> str:
        return (
            "You are the report assembly specialist for a trading portfolio agent. "
            "Your job is to:\n"
            "1. Assemble the report using assemble_report\n"
            "2. Save the report to a markdown file using save_report\n"
            "3. Display the report in the terminal using display_report\n\n"
            "Call these tools in this exact order. All three must be called."
        )

    def create_tools(self, ctx: AgentContext) -> list[Any]:

        @langchain_tool
        def assemble_report(
            instruments_processed: int,
            instruments_failed: int,
            analyses_created: int,
            has_commentary: bool,
        ) -> str:
            """Assemble the final report from pipeline data stored in the database.

            Args:
                instruments_processed: Number of instruments successfully processed.
                instruments_failed: Number of instruments that failed.
                analyses_created: Number of analysis records created.
                has_commentary: Whether LLM commentary was generated.

            Returns a summary of the assembled report.
            """
            # Build the pipeline summary dict that generate_report expects
            pipeline_summary: dict[str, Any] = {
                "run_id": ctx.run_id,
                "run_type": ctx.run_type,
                "snapshot_id": "",
                "instruments_processed": instruments_processed,
                "instruments_failed": instruments_failed,
                "candle_counts": {},
                "analyses_created": analyses_created,
                "commentary": None,
                "errors": [],
            }

            # Pull commentary from stored state if available
            commentary_response = getattr(self, "_commentary_dict", None)
            if has_commentary and commentary_response:
                pipeline_summary["commentary"] = commentary_response

            report = generate_report(pipeline_summary, ctx.db)
            self._report = report

            rec_count = 0
            if report.commentary:
                rec_count = len(report.commentary.recommendations)

            return (
                f"Report assembled: {report.run_type}\n"
                f"Positions: {report.snapshot.open_positions}\n"
                f"Analyses: {len(report.analyses)}\n"
                f"Recommendations: {rec_count}\n"
                f"Has diff: {report.diff is not None}"
            )

        @langchain_tool
        def save_report(verbose: bool = False) -> str:
            """Save the assembled report as a markdown file.

            Args:
                verbose: Whether to include verbose debug tables.

            Returns the file path where the report was saved.
            """
            report = getattr(self, "_report", None)
            if report is None:
                return "ERROR: Must call assemble_report first."

            markdown = format_markdown(report, verbose=verbose, currency_symbol=self.get_currency_symbol(ctx))

            ts = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d_%H%M%S")
            filename = f"{ts}_{report.run_type}_pipeline.md"
            report_dir = Path("reports")
            report_dir.mkdir(exist_ok=True)
            report_path = report_dir / filename
            report_path.write_text(markdown, encoding="utf-8")

            self._report_path = str(report_path)
            self._markdown = markdown

            logger.info("report_saved", path=str(report_path))
            return f"Report saved to: {report_path}"

        @langchain_tool
        def display_report(verbose: bool = False) -> str:
            """Display the report in the terminal using rich formatting.

            Args:
                verbose: Whether to include verbose debug tables.
            """
            report = getattr(self, "_report", None)
            if report is None:
                return "ERROR: Must call assemble_report first."

            format_terminal(report, verbose=verbose, currency_symbol=self.get_currency_symbol(ctx))
            return "Report displayed in terminal."

        return [assemble_report, save_report, display_report]

    def run_procedural(
        self,
        state: dict[str, Any],
        ctx: AgentContext,
    ) -> None:
        """Assemble, save, and display the report (procedural)."""
        self._commentary_dict = state.get("commentary")

        candle_counts = state.get("candle_counts", {})
        instruments_processed = sum(
            1 for c in candle_counts.values() if c > 0
        )

        pipeline_summary: dict[str, Any] = {
            "run_id": ctx.run_id,
            "run_type": ctx.run_type,
            "snapshot_id": state.get("snapshot_id", ""),
            "instruments_processed": instruments_processed,
            "instruments_failed": len(state.get("errors", [])),
            "candle_counts": candle_counts,
            "analyses_created": state.get("analyses_created", 0),
            "commentary": state.get("commentary"),
            "errors": state.get("errors", []),
        }

        report = generate_report(pipeline_summary, ctx.db)
        self._report = report

        cs = self.get_currency_symbol(ctx)
        markdown = format_markdown(report, verbose=False, currency_symbol=cs)

        ts = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d_%H%M%S")
        filename = f"{ts}_{report.run_type}_pipeline.md"
        report_dir = Path("reports")
        report_dir.mkdir(exist_ok=True)
        report_path = report_dir / filename
        report_path.write_text(markdown, encoding="utf-8")
        self._report_path = str(report_path)

        format_terminal(report, verbose=False, currency_symbol=cs)

    def process_results(
        self,
        state: dict[str, Any],
        ctx: AgentContext,
    ) -> dict[str, Any]:
        """Return the assembled report and file path."""
        report = getattr(self, "_report", None)
        report_path = getattr(self, "_report_path", None)

        # Clean up
        self._report = None
        self._report_path = None
        self._markdown = None
        self._commentary_dict = None

        return {
            "report": report,
            "report_path": report_path,
        }
