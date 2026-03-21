"""Tests for reporting/formatter.py — markdown and rich terminal rendering.

Tests use pre-built Report fixtures to verify output structure without
requiring any database or API interaction.
"""

from __future__ import annotations

from datetime import datetime, timezone
from io import StringIO

from rich.console import Console

from agent.reporting.formatter import format_markdown, format_terminal, _render_to_console
from agent.reporting.generator import (
    AnalysisSummary,
    CommentarySummary,
    InstrumentSummary,
    PositionSummary,
    RecommendationChange,
    RecommendationSummary,
    Report,
    ReportDiff,
    SnapshotSummary,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_report(
    *,
    with_commentary: bool = True,
    with_analyses: bool = True,
    with_instruments: bool = True,
    with_positions: bool = True,
    with_errors: bool = False,
    with_diff: ReportDiff | None = None,
) -> Report:
    """Build a Report fixture with configurable sections."""
    positions = []
    if with_positions:
        positions = [
            PositionSummary(
                instrument_id=1001,
                symbol="AAPL",
                direction="Long",
                open_rate=150.0,
                amount=1000.0,
                units=10.0,
                pnl=250.0,
            ),
            PositionSummary(
                instrument_id=1002,
                symbol="BTC",
                direction="Long",
                open_rate=40000.0,
                amount=500.0,
                units=0.01,
                pnl=-50.0,
            ),
        ]

    snapshot = SnapshotSummary(
        total_value=1500.0,
        cash_available=500.0,
        open_positions=len(positions),
        total_pnl=200.0,
        run_type="market_open",
        captured_at="2026-03-08T12:00:00+00:00",
        positions=positions,
    )

    instruments = []
    if with_instruments:
        instruments = [
            InstrumentSummary(
                etoro_id=1001,
                symbol="AAPL",
                asset_class="Stocks",
                exchange="10",
                candle_count=100,
            ),
            InstrumentSummary(
                etoro_id=1002,
                symbol="BTC",
                asset_class="Crypto",
                exchange=None,
                candle_count=100,
            ),
        ]

    analyses = []
    if with_analyses:
        analyses = [
            AnalysisSummary(
                symbol="AAPL",
                etoro_id=1001,
                trend="bullish",
                trend_strength=0.75,
                support=148.0,
                resistance=155.0,
                momentum="strong_up",
            ),
            AnalysisSummary(
                symbol="BTC",
                etoro_id=1002,
                trend="bearish",
                trend_strength=0.45,
                support=38000.0,
                resistance=42000.0,
                momentum="weak_down",
            ),
        ]

    commentary = None
    if with_commentary:
        commentary = CommentarySummary(
            summary="Mixed signals across portfolio.",
            market_context="US tech is stable while crypto remains volatile.",
            position_commentaries=[
                {"symbol": "AAPL", "commentary": "Apple shows strong bullish momentum."},
                {"symbol": "BTC", "commentary": "Bitcoin faces downward pressure."},
            ],
            recommendations=[
                RecommendationSummary(symbol="AAPL", action="hold", conviction="medium", reasoning="Maintain position."),
                RecommendationSummary(symbol="BTC", action="sell", conviction="high", reasoning="Cut losses."),
            ],
        )

    errors = []
    if with_errors:
        errors = [
            {"instrument_id": 9999, "error": "candle fetch failed"},
            {"step": "commentary", "error": "API timeout"},
        ]

    return Report(
        run_id="test-run-abc-123",
        run_type="market_open",
        generated_at=datetime(2026, 3, 8, 12, 0, 0, tzinfo=timezone.utc),
        snapshot=snapshot,
        instruments=instruments,
        analyses=analyses,
        commentary=commentary,
        candle_counts={1001: 5, 1002: 5},
        errors=errors,
        diff=with_diff,
    )


# ---------------------------------------------------------------------------
# Markdown tests
# ---------------------------------------------------------------------------


def test_format_markdown_default_has_advisory_sections() -> None:
    """Default markdown includes title, portfolio overview, commentary, and recommendations."""
    report = _make_report()
    md = format_markdown(report)

    assert "# eToro Advisory Report" in md
    assert "## Portfolio Overview" in md
    assert "## Market Commentary" in md
    assert "Mixed signals across portfolio." in md
    assert "US tech is stable" in md
    assert "### Position Analysis" in md
    assert "Apple shows strong bullish momentum." in md
    assert "### Recommendations" in md
    assert "| AAPL | HOLD | medium | Maintain position. |" in md
    assert "| BTC | SELL | high | Cut losses. |" in md


def test_format_markdown_default_no_debug_tables() -> None:
    """Default markdown does NOT include verbose debug tables."""
    report = _make_report()
    md = format_markdown(report)

    assert "## Analysis Results" not in md
    assert "## Instruments" not in md
    assert "## Open Positions" not in md
    assert "## Candles Inserted" not in md


def test_format_markdown_verbose_has_debug_tables() -> None:
    """Verbose mode includes analysis, instruments, positions, and candle tables."""
    report = _make_report()
    md = format_markdown(report, verbose=True)

    # Advisory sections still present
    assert "## Market Commentary" in md
    assert "### Recommendations" in md

    # Debug tables present
    assert "## Analysis Results" in md
    assert "| AAPL | bullish |" in md
    assert "## Instruments" in md
    assert "| AAPL | 1001 |" in md
    assert "## Open Positions" in md
    assert "## Candles Inserted This Run" in md


def test_format_markdown_no_commentary() -> None:
    """Markdown handles missing commentary gracefully."""
    report = _make_report(with_commentary=False)
    md = format_markdown(report)

    assert "## Market Commentary" in md
    assert "LLM commentary not available" in md
    assert "### Recommendations" not in md


def test_format_markdown_recommendations_table_format() -> None:
    """Recommendations table has correct column structure."""
    report = _make_report()
    md = format_markdown(report)

    assert "| Symbol | Action | Conviction | Reasoning |" in md
    assert "|---|---|---|---|" in md


def test_format_markdown_with_errors() -> None:
    """Errors section appears when errors are present."""
    report = _make_report(with_errors=True)
    md = format_markdown(report)

    assert "## Errors" in md
    assert "Instrument 9999" in md
    assert "candle fetch failed" in md
    assert "API timeout" in md


def test_format_markdown_portfolio_overview_values() -> None:
    """Portfolio overview includes correct numeric values."""
    report = _make_report()
    md = format_markdown(report)

    assert "£1,500.00" in md
    assert "£500.00" in md
    assert "£200.00" in md
    assert "Open positions:** 2" in md


# ---------------------------------------------------------------------------
# Terminal tests
# ---------------------------------------------------------------------------


def _capture_terminal(report: Report, *, verbose: bool = False) -> str:
    """Render a report to a string via rich Console."""
    buf = StringIO()
    console = Console(file=buf, width=120, force_terminal=True)
    _render_to_console(console, report, verbose=verbose)
    return buf.getvalue()


def test_format_terminal_runs_without_error() -> None:
    """Smoke test — format_terminal completes without raising."""
    report = _make_report()
    # Should not raise
    format_terminal(report)


def test_format_terminal_verbose_runs_without_error() -> None:
    """Smoke test — verbose terminal output completes without raising."""
    report = _make_report()
    format_terminal(report, verbose=True)


def test_format_terminal_contains_recommendations() -> None:
    """Terminal output contains recommendation symbols and actions."""
    report = _make_report()
    output = _capture_terminal(report)

    assert "AAPL" in output
    assert "BTC" in output
    assert "HOLD" in output
    assert "SELL" in output


def test_format_terminal_no_commentary() -> None:
    """Terminal output handles missing commentary."""
    report = _make_report(with_commentary=False)
    output = _capture_terminal(report)

    assert "not available" in output


def test_format_terminal_verbose_shows_analysis() -> None:
    """Verbose terminal output includes analysis table content."""
    report = _make_report()
    output = _capture_terminal(report, verbose=True)

    assert "bullish" in output
    assert "bearish" in output
    assert "Analysis Results" in output


def test_format_terminal_with_errors() -> None:
    """Terminal output shows errors when present."""
    report = _make_report(with_errors=True)
    output = _capture_terminal(report)

    assert "9999" in output
    assert "candle fetch failed" in output


# ---------------------------------------------------------------------------
# Diff helper
# ---------------------------------------------------------------------------


def _sample_diff() -> ReportDiff:
    """Build a sample ReportDiff for testing."""
    return ReportDiff(
        previous_run_id="prev-run-xyz",
        previous_run_type="market_close",
        major_changes=[
            RecommendationChange(
                symbol="BTC",
                previous_action="hold",
                new_action="sell",
                previous_conviction="low",
                new_conviction="high",
                reasoning="Bearish momentum intensified.",
            ),
        ],
        minor_changes=[
            RecommendationChange(
                symbol="AAPL",
                previous_action="hold",
                new_action="hold",
                previous_conviction="medium",
                new_conviction="high",
                reasoning="Conviction increased slightly.",
            ),
        ],
        new_symbols=[
            RecommendationSummary(symbol="SNOW", action="buy", conviction="high", reasoning="Strong entry."),
        ],
        removed_symbols=["GOOG"],
        unchanged_count=5,
    )


# ---------------------------------------------------------------------------
# Markdown diff tests
# ---------------------------------------------------------------------------


def test_format_markdown_diff_shows_changes_section() -> None:
    """When diff is present, markdown includes the changes section."""
    report = _make_report(with_diff=_sample_diff())
    md = format_markdown(report)

    assert "## Key Changes Since Last Run" in md
    assert "prev-run-xyz" in md
    assert "### Major Changes" in md
    assert "BTC" in md
    assert "HOLD" in md
    assert "**SELL**" in md
    assert "### Minor Changes" in md
    assert "AAPL" in md


def test_format_markdown_diff_shows_new_positions() -> None:
    """New symbols appear in a separate table."""
    report = _make_report(with_diff=_sample_diff())
    md = format_markdown(report)

    assert "### New Positions" in md
    assert "SNOW" in md
    assert "BUY" in md


def test_format_markdown_diff_shows_removed_positions() -> None:
    """Removed positions are listed."""
    report = _make_report(with_diff=_sample_diff())
    md = format_markdown(report)

    assert "### Positions No Longer Held" in md
    assert "GOOG" in md


def test_format_markdown_diff_hides_full_recommendations() -> None:
    """Default mode with diff does NOT show the full recommendations table."""
    report = _make_report(with_diff=_sample_diff())
    md = format_markdown(report)

    assert "### Recommendations" not in md
    assert "### Position Analysis" not in md


def test_format_markdown_diff_verbose_shows_all() -> None:
    """Verbose mode shows both diff AND full recommendations."""
    report = _make_report(with_diff=_sample_diff())
    md = format_markdown(report, verbose=True)

    assert "## Key Changes Since Last Run" in md
    assert "### Recommendations" in md
    assert "### Position Analysis" in md


def test_format_markdown_diff_unchanged_count() -> None:
    """Unchanged count is displayed."""
    report = _make_report(with_diff=_sample_diff())
    md = format_markdown(report)

    assert "5 recommendation(s) unchanged" in md


def test_format_markdown_diff_no_changes() -> None:
    """When nothing changed, shows 'no changes' message."""
    no_changes_diff = ReportDiff(
        previous_run_id="prev-run",
        previous_run_type="market_open",
        major_changes=[],
        minor_changes=[],
        new_symbols=[],
        removed_symbols=[],
        unchanged_count=3,
    )
    report = _make_report(with_diff=no_changes_diff)
    md = format_markdown(report)

    assert "No changes" in md
    assert "3 recommendation(s) unchanged" in md


# ---------------------------------------------------------------------------
# Terminal diff tests
# ---------------------------------------------------------------------------


def test_format_terminal_diff_shows_changes() -> None:
    """Terminal output includes the diff change table."""
    report = _make_report(with_diff=_sample_diff())
    output = _capture_terminal(report)

    assert "Key Changes" in output
    assert "Major Changes" in output
    assert "BTC" in output
    assert "SELL" in output


def test_format_terminal_diff_hides_full_recommendations() -> None:
    """Default mode with diff does NOT show the full recommendations table."""
    report = _make_report(with_diff=_sample_diff())
    output = _capture_terminal(report)

    assert "All Recommendations" not in output


def test_format_terminal_diff_verbose_shows_all() -> None:
    """Verbose mode shows both diff AND full recommendations."""
    report = _make_report(with_diff=_sample_diff())
    output = _capture_terminal(report, verbose=True)

    assert "Key Changes" in output
    assert "All Recommendations" in output
