"""CLI entry point for the eToro trading agent.

Usage::

    python -m agent.main --run-type market_open
    python -m agent.main --run-type market_close --verbose
    python -m agent.main --run-type market_open --text-logs

Or via the installed console script::

    etoro-agent --run-type market_open
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

import structlog

from agent.config import get_settings
from agent.orchestrator import Orchestrator, PipelineError
from agent.reporting import format_markdown, format_terminal
from agent.utils.logging import configure_logging

logger = structlog.get_logger(__name__)


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the CLI."""
    parser = argparse.ArgumentParser(
        prog="etoro-agent",
        description="eToro advisory trading agent — generates market reports.",
    )
    parser.add_argument(
        "--run-type",
        required=True,
        choices=["market_open", "market_close"],
        help="Type of run: market_open or market_close.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        default=False,
        help="Show the full report including all recommendations.",
    )
    parser.add_argument(
        "--text-logs",
        action="store_true",
        default=False,
        help="Use human-readable text logs instead of JSON.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the agent pipeline and output a report.

    Args:
        argv: Command-line arguments (defaults to ``sys.argv[1:]``).

    Returns:
        Exit code: 0 on success, 1 on failure.
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    configure_logging(json=not args.text_logs)

    settings = get_settings()
    ts_label = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d_%H%M%S")

    try:
        with Orchestrator(settings) as orch:
            summary = orch.run_data_pipeline(args.run_type)
    except PipelineError as exc:
        logger.error("pipeline_fatal", error=str(exc))
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        logger.error("unexpected_error", error=str(exc), exc_info=True)
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    report = summary["report"]
    cs = summary.get("currency_symbol", settings.currency_symbol)

    # Rich terminal output
    format_terminal(report, verbose=args.verbose, currency_symbol=cs)

    # Save markdown report to file
    md = format_markdown(report, verbose=args.verbose, currency_symbol=cs)
    reports_dir = Path("reports")
    reports_dir.mkdir(exist_ok=True)
    report_path = reports_dir / f"{ts_label}_{args.run_type}_pipeline.md"
    report_path.write_text(md, encoding="utf-8")
    print(f"\nReport saved to: {report_path}")

    duration = summary.get("duration_ms")
    if duration is not None:
        print(f"Completed in {duration}ms")

    return 0


def cli() -> None:
    """Console-script entry point (calls ``sys.exit``)."""
    sys.exit(main())


if __name__ == "__main__":
    cli()
