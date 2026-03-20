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
from agent.reporting import Report, format_markdown, format_terminal
from agent.reporting.cache import cache_report
from agent.telegram import TelegramClient, TelegramRequestError
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
    parser.add_argument(
        "--send-telegram",
        action="store_true",
        default=False,
        help="Send a summary of the run report to Telegram.",
    )
    parser.add_argument(
        "--cache-report",
        action="store_true",
        default=False,
        help="Cache the generated report JSON for local iteration tooling.",
    )
    return parser


def _build_telegram_summary(report: Report) -> str:
    """Build a conversational Telegram summary with market context and recommendations."""
    def _to_float(value: object, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    snapshot = getattr(report, "snapshot", None)
    commentary = getattr(report, "commentary", None)
    recommendations = commentary.recommendations if commentary else []

    action_counts: dict[str, int] = {}
    for rec in recommendations:
        action = rec.action.upper()
        action_counts[action] = action_counts.get(action, 0) + 1

    preferred_action_order = ["SELL", "REDUCE", "HOLD", "ACCUMULATE", "INCREASE"]
    ordered_actions = [
        action for action in preferred_action_order if action in action_counts
    ]
    ordered_actions.extend(
        sorted(action for action in action_counts if action not in preferred_action_order)
    )

    # Skip HOLD actions - they're not actionable, only show SELL/REDUCE/ACCUMULATE/INCREASE
    action_overview_lines = [
        f"- {action}: {action_counts[action]}" for action in ordered_actions if action != "HOLD"
    ]
    if not action_overview_lines:
        action_overview_lines = ["- No recommendations were generated in this run."]

    top_recommendation_lines = [
        f"- {rec.symbol}: {rec.action.upper()} ({rec.conviction})"
        for rec in recommendations if rec.action.upper() != "HOLD"
    ][:5]
    if not top_recommendation_lines:
        top_recommendation_lines = ["- No actionable recommendations"]

    run_label = report.run_type.replace("_", " ").title()

    open_positions = int(_to_float(getattr(snapshot, "open_positions", 0), 0.0))
    total_value = _to_float(getattr(snapshot, "total_value", 0.0), 0.0)
    cash_available = _to_float(getattr(snapshot, "cash_available", 0.0), 0.0)
    total_pnl = _to_float(getattr(snapshot, "total_pnl", 0.0), 0.0)

    # Format timestamp
    generated_at = getattr(report, "generated_at", None)
    timestamp_str = ""
    if generated_at:
        timestamp_str = f"\n⏰ {generated_at.strftime('%Y-%m-%d %H:%M')} UTC"

    # Portfolio overview with emoji
    portfolio_section = (
        f"📊 Portfolio Snapshot ({run_label}){timestamp_str}\n"
        f"Positions: {open_positions}  •  Value: ${total_value:,.2f}\n"
        f"Cash: ${cash_available:,.2f}  •  P&L: ${total_pnl:,.2f}"
    )

    # Market overview and portfolio impact - split into separate sections
    market_overview_section = ""
    portfolio_impact_section = ""
    if commentary:
        summary_text = getattr(commentary, "summary", "")
        market_context = getattr(commentary, "market_context", "")
        
        if summary_text:
            market_overview_section = f"\n\n🌍 Market Overview\n{summary_text}"
        
        if market_context:
            portfolio_impact_section = f"\n\n💼 Portfolio Impact\n{market_context}"

    # Recommended actions with emoji
    actions_section = (
        "\n\n📈 Recommended Actions\n"
        f"{chr(10).join(action_overview_lines)}"
    )

    # Top recommendations with emoji
    top_section = (
        "\n\n🎯 Top Actions\n"
        f"{chr(10).join(top_recommendation_lines)}"
    )

    return portfolio_section + market_overview_section + portfolio_impact_section + actions_section + top_section


def _maybe_send_telegram_summary(
    *,
    send_requested: bool,
    bot_token: str,
    chat_id: str,
    message: str,
) -> None:
    """Send Telegram summary if requested and configured."""
    if not send_requested:
        return

    if not bot_token or not chat_id:
        logger.warning(
            "telegram_not_configured",
            reason="missing_bot_token_or_chat_id",
        )
        return

    try:
        with TelegramClient(bot_token) as telegram_client:
            telegram_client.send_message(chat_id=chat_id, text=message)
        logger.info("telegram_send_success", chat_id=chat_id)
    except TelegramRequestError as exc:
        logger.error("telegram_send_failed", chat_id=chat_id, error=str(exc))
    except Exception as exc:
        logger.error(
            "telegram_send_unexpected_error",
            chat_id=chat_id,
            error=str(exc),
            exc_info=True,
        )


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

    # Rich terminal output
    format_terminal(report, verbose=args.verbose)

    # Save markdown report to file
    md = format_markdown(report, verbose=args.verbose)
    reports_dir = Path("reports")
    reports_dir.mkdir(exist_ok=True)
    report_path = reports_dir / f"{ts_label}_{args.run_type}_pipeline.md"
    report_path.write_text(md, encoding="utf-8")
    print(f"\nReport saved to: {report_path}")

    if args.cache_report:
        try:
            cache_file = cache_report(report)
            print(f"Report cached to: {cache_file}")
        except Exception as exc:
            logger.warning("cache_report_failed", error=str(exc))

    telegram_message = _build_telegram_summary(report)
    _maybe_send_telegram_summary(
        send_requested=args.send_telegram,
        bot_token=settings.telegram_bot_token,
        chat_id=settings.telegram_chat_id,
        message=telegram_message,
    )

    duration = summary.get("duration_ms")
    if duration is not None:
        print(f"Completed in {duration}ms")

    return 0


def cli() -> None:
    """Console-script entry point (calls ``sys.exit``)."""
    sys.exit(main())


if __name__ == "__main__":
    cli()
