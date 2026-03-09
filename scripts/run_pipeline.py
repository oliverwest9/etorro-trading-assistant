#!/usr/bin/env python
"""Run the data pipeline with real API credentials.

Usage::

    python scripts/run_pipeline.py [market_open|market_close] [--verbose]

Loads settings from ``.env``, connects to SurrealDB, ensures the schema
is applied, then runs the full data pipeline (portfolio fetch → instrument
resolution → candle fetch → analysis → LLM commentary → report).

Prints a ``rich`` formatted report to the terminal and saves a timestamped
markdown file to ``reports/``.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

from agent.config import get_settings
from agent.orchestrator import Orchestrator
from agent.reporting import format_markdown, format_terminal


def main() -> None:
    # Parse arguments (simple — proper CLI comes in Step 11)
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    verbose = "--verbose" in flags

    run_type = args[0] if args else "market_open"
    if run_type not in ("market_open", "market_close"):
        print(f"Invalid run_type: {run_type!r}. Use 'market_open' or 'market_close'.")
        sys.exit(1)

    settings = get_settings()
    ts_label = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d_%H%M%S")

    with Orchestrator(settings) as orch:
        summary = orch.run_data_pipeline(run_type)
        report = summary["report"]

        # Rich terminal output
        format_terminal(report, verbose=verbose)

        # Save markdown report to file
        md = format_markdown(report, verbose=verbose)
        reports_dir = Path("reports")
        reports_dir.mkdir(exist_ok=True)
        report_path = reports_dir / f"{ts_label}_{run_type}_pipeline.md"
        report_path.write_text(md, encoding="utf-8")
        print(f"\n📄 Report saved to: {report_path}")


if __name__ == "__main__":
    main()
