#!/usr/bin/env python3

"""Test Telegram message formats against cached trading reports.

This script allows rapid iteration on Telegram message formatting without
re-running the full trading pipeline. It loads cached report JSON files and
renders different message formats for preview.

Usage::

    python scripts/test_telegram_format.py                    # List available reports
    python scripts/test_telegram_format.py <run_id>           # Show message for a report
    python scripts/test_telegram_format.py <run_id> --raw     # Show raw report data
    python scripts/test_telegram_format.py <run_id> --compare # Compare multiple formats

Examples::

    python scripts/test_telegram_format.py market_open_2026_03_19_044449
    python scripts/test_telegram_format.py market_open_2026_03_19_044449 --raw
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Add src/ to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from agent.main import _build_telegram_summary
from agent.reporting.cache import list_cached_reports, load_cached_report
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax

console = Console()


def list_reports() -> None:
    """List all available cached reports."""
    reports = list_cached_reports()
    if not reports:
        console.print("[yellow]No cached reports found in reports/cache/[/yellow]")
        console.print(
            "Run the agent with: "
            "[bold]python -m agent.main --run-type market_open --cache-report[/bold]"
        )
        return

    console.print(f"\n[bold green]Available cached reports ({len(reports)}):[/bold green]\n")
    for run_id in sorted(reports):
        console.print(f"  • {run_id}")
    console.print()


def show_report(run_id: str, raw: bool = False, compare: bool = False) -> None:
    """Load and display a cached report in various formats."""
    report = load_cached_report(run_id)
    if report is None:
        console.print(f"[red]Report not found: {run_id}[/red]")
        console.print(f"[yellow]Run 'python scripts/test_telegram_format.py' to see available reports[/yellow]")
        return

    console.print()

    # Show report metadata
    console.print(
        Panel(
            f"Run ID: [bold]{report.run_id}[/bold]\n"
            f"Type: {report.run_type}\n"
            f"Generated: {report.generated_at}\n"
            f"Positions: {len(report.snapshot.positions)}\n"
            f"Instruments: {len(report.instruments)}\n"
            f"Analyses: {len(report.analyses)}\n"
            f"Recommendations: {len(report.commentary.recommendations) if report.commentary else 0}",
            title="Report Metadata",
        )
    )

    if raw:
        # Show raw report data as JSON
        from dataclasses import asdict

        report_dict = asdict(report)

        def json_serializer(obj):
            """Handle non-JSON-serializable types."""
            if hasattr(obj, "isoformat"):  # datetime
                return obj.isoformat()
            return str(obj)

        json_str = json.dumps(report_dict, indent=2, default=json_serializer)
        syntax = Syntax(json_str, "json", theme="monokai", line_numbers=True, word_wrap=True)
        console.print(Panel(syntax, title="Raw Report Data", expand=False))
    else:
        # Build and show Telegram message
        message = _build_telegram_summary(report)

        console.print(Panel(message, title="Telegram Message", border_style="green", expand=False))

        # Show action counts breakdown
        if report.commentary:
            action_counts = {}
            for rec in report.commentary.recommendations:
                action = rec.action.upper()
                if action == "HOLD":
                    continue
                action_counts[action] = action_counts.get(action, 0) + 1

            console.print("\n[bold]Action Breakdown (Actionable):[/bold]")
            if not action_counts:
                console.print("  No actionable recommendations")
            else:
                for action in sorted(action_counts.keys()):
                    count = action_counts[action]
                    console.print(f"  {action}: {count}")

            # Show top recommendations
            console.print("\n[bold]Top Actionable Recommendations:[/bold]")
            actionable = [
                rec for rec in report.commentary.recommendations if rec.action.upper() != "HOLD"
            ]
            if not actionable:
                console.print("  No actionable recommendations")
            else:
                for rec in actionable[:5]:
                    console.print(f"  • [bold]{rec.symbol}[/bold] - {rec.action} ({rec.conviction})")

    console.print()


def main() -> int:
    """Main entrypoint."""
    parser = argparse.ArgumentParser(
        description="Test Telegram message formats with cached trading reports"
    )
    parser.add_argument(
        "run_id",
        nargs="?",
        help="Run ID of cached report to display (leave empty to list available reports)",
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help="Show raw report data as JSON instead of formatted message",
    )
    parser.add_argument(
        "--compare",
        action="store_true",
        help="Compare multiple message format implementations (future)",
    )

    args = parser.parse_args()

    if not args.run_id:
        list_reports()
        return 0

    show_report(args.run_id, raw=args.raw, compare=args.compare)
    return 0


if __name__ == "__main__":
    sys.exit(main())
