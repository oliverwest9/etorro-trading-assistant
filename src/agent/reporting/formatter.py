"""Report rendering as markdown and rich terminal output.

Two public functions:

- ``format_markdown(report, verbose)`` → markdown string
- ``format_terminal(report, verbose)`` → prints to stdout via ``rich``
"""

from __future__ import annotations

from io import StringIO
from typing import TYPE_CHECKING

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

if TYPE_CHECKING:
    from agent.reporting.generator import Report, ReportDiff


# =====================================================================
# Markdown
# =====================================================================

_ACTION_LABELS = {
    "buy": "BUY",
    "sell": "SELL",
    "hold": "HOLD",
    "reduce": "REDUCE",
    "increase": "INCREASE",
}


def format_markdown(report: Report, *, verbose: bool = False) -> str:
    """Render a ``Report`` as a markdown string.

    Args:
        report: The assembled report object.
        verbose: If ``True``, include debug tables (analyses, positions,
            instruments, candle counts).

    Returns:
        The complete markdown document as a string.
    """
    lines: list[str] = []

    # ---- Header ----
    ts = report.generated_at.strftime("%Y-%m-%d %H:%M UTC")
    run_label = report.run_type.replace("_", " ").title()
    lines.append(f"# eToro Advisory Report — {ts}")
    lines.append(f"**{run_label}** | Run ID: `{report.run_id}`\n")

    # ---- Portfolio overview ----
    lines.append("## Portfolio Overview\n")
    snap = report.snapshot
    lines.append(f"- **Total value:** ${snap.total_value:,.2f}")
    lines.append(f"- **Cash available:** ${snap.cash_available:,.2f}")
    lines.append(f"- **Open positions:** {snap.open_positions}")
    lines.append(f"- **Total P&L:** ${snap.total_pnl:,.2f}")
    lines.append("")

    # ---- LLM Commentary ----
    if report.commentary:
        lines.append("## Market Commentary\n")
        lines.append(f"**{report.commentary.summary}**\n")
        lines.append(report.commentary.market_context)
        lines.append("")

        # ---- Diff section (changes since last run) ----
        if report.diff is not None:
            lines.append("## Key Changes Since Last Run\n")
            prev_label = report.diff.previous_run_type.replace("_", " ").title()
            lines.append(
                f"*Compared to previous {prev_label} "
                f"(run `{report.diff.previous_run_id}`)*\n"
            )

            if report.diff.changed:
                lines.append("### Recommendation Changes\n")
                lines.append("| Symbol | Previous | Current | Conviction | Reasoning |")
                lines.append("|---|---|---|---|---|")
                for c in report.diff.changed:
                    prev_a = _ACTION_LABELS.get(c.previous_action, c.previous_action.upper())
                    new_a = _ACTION_LABELS.get(c.new_action, c.new_action.upper())
                    lines.append(
                        f"| {c.symbol} | {prev_a} ({c.previous_conviction}) "
                        f"| **{new_a}** ({c.new_conviction}) "
                        f"| {c.new_conviction} | {c.reasoning} |"
                    )
                lines.append("")

            if report.diff.new_symbols:
                lines.append("### New Positions\n")
                lines.append("| Symbol | Action | Conviction | Reasoning |")
                lines.append("|---|---|---|---|")
                for r in report.diff.new_symbols:
                    action = _ACTION_LABELS.get(r.action, r.action.upper())
                    lines.append(
                        f"| {r.symbol} | {action} | {r.conviction} | {r.reasoning} |"
                    )
                lines.append("")

            if report.diff.removed_symbols:
                lines.append("### Positions No Longer Held\n")
                for sym in report.diff.removed_symbols:
                    lines.append(f"- {sym}")
                lines.append("")

            if not report.diff.changed and not report.diff.new_symbols and not report.diff.removed_symbols:
                lines.append("*No changes — all recommendations unchanged.*\n")

            lines.append(f"*{report.diff.unchanged_count} recommendation(s) unchanged.*\n")

        # ---- Recommendations (full list in verbose, or when no diff) ----
        if report.commentary.position_commentaries and (verbose or report.diff is None):
            lines.append("### Position Analysis\n")
            for pc in report.commentary.position_commentaries:
                lines.append(f"**{pc['symbol']}**: {pc['commentary']}\n")

        if report.commentary.recommendations and (verbose or report.diff is None):
            lines.append("### Recommendations\n")
            lines.append("| Symbol | Action | Conviction | Reasoning |")
            lines.append("|---|---|---|---|")
            for r in report.commentary.recommendations:
                action = _ACTION_LABELS.get(r.action, r.action.upper())
                lines.append(
                    f"| {r.symbol} | {action} | {r.conviction} | {r.reasoning} |"
                )
            lines.append("")
    else:
        lines.append("## Market Commentary\n")
        lines.append("*LLM commentary not available for this run.*\n")

    # ---- Verbose sections ----
    if verbose:
        # Analysis results table
        if report.analyses:
            lines.append("## Analysis Results\n")
            lines.append("| Symbol | Trend | Strength | Support | Resistance | Momentum |")
            lines.append("|---|---|---:|---:|---:|---|")
            for a in report.analyses:
                support_str = f"{a.support:.2f}" if a.support is not None else "—"
                resistance_str = f"{a.resistance:.2f}" if a.resistance is not None else "—"
                lines.append(
                    f"| {a.symbol} | {a.trend} | {a.trend_strength:.2f} "
                    f"| {support_str} | {resistance_str} | {a.momentum} |"
                )
            lines.append("")

        # Instruments table
        if report.instruments:
            lines.append("## Instruments\n")
            lines.append("| Symbol | eToro ID | Asset Class | Exchange | Daily Candles |")
            lines.append("|---|---:|---|---|---:|")
            for inst in report.instruments:
                exchange = inst.exchange or "—"
                lines.append(
                    f"| {inst.symbol} | {inst.etoro_id} "
                    f"| {inst.asset_class} | {exchange} | {inst.candle_count} |"
                )
            lines.append("")

        # Positions table
        if report.snapshot.positions:
            lines.append("## Open Positions\n")
            lines.append("| # | Symbol | Direction | Open Rate | Amount | Units | P&L |")
            lines.append("|---:|---|---|---:|---:|---:|---:|")
            for idx, pos in enumerate(report.snapshot.positions, 1):
                pnl_str = f"${pos.pnl:,.2f}" if pos.pnl is not None else "—"
                lines.append(
                    f"| {idx} | {pos.symbol} | {pos.direction} "
                    f"| {pos.open_rate:.4f} | ${pos.amount:,.2f} "
                    f"| {pos.units:.4f} | {pnl_str} |"
                )
            lines.append("")

        # Candle counts
        if report.candle_counts:
            lines.append("## Candles Inserted This Run\n")
            lines.append("| Instrument ID | Candles |")
            lines.append("|---:|---:|")
            for iid, count in sorted(report.candle_counts.items()):
                lines.append(f"| {iid} | {count} |")
            lines.append("")

    # ---- Errors ----
    if report.errors:
        lines.append("## Errors\n")
        for err in report.errors:
            if "instrument_id" in err:
                lines.append(f"- Instrument {err['instrument_id']}: `{err['error']}`")
            else:
                lines.append(f"- {err.get('step', 'unknown')}: `{err['error']}`")
        lines.append("")

    return "\n".join(lines)


# =====================================================================
# Rich terminal
# =====================================================================

_ACTION_COLORS = {
    "buy": "green",
    "increase": "green",
    "sell": "red",
    "reduce": "red",
    "hold": "yellow",
}


def format_terminal(report: Report, *, verbose: bool = False) -> None:
    """Print the report to the terminal using ``rich`` formatting.

    Args:
        report: The assembled report object.
        verbose: If ``True``, include debug tables (analyses, positions,
            instruments).
    """
    console = Console()
    _render_to_console(console, report, verbose=verbose)


def _render_to_console(
    console: Console,
    report: Report,
    *,
    verbose: bool = False,
) -> None:
    """Internal renderer — accepts a Console so tests can capture output."""
    ts = report.generated_at.strftime("%Y-%m-%d %H:%M UTC")
    run_label = report.run_type.replace("_", " ").title()

    # ---- Header ----
    console.print()
    console.print(
        Panel(
            f"[bold]{run_label}[/bold]  •  {ts}\nRun ID: {report.run_id}",
            title="[bold blue]eToro Advisory Report[/bold blue]",
            border_style="blue",
        )
    )

    # ---- Portfolio overview ----
    snap = report.snapshot
    pnl_color = "green" if snap.total_pnl >= 0 else "red"
    portfolio_text = (
        f"[bold]Total value:[/bold] ${snap.total_value:,.2f}    "
        f"[bold]Cash:[/bold] ${snap.cash_available:,.2f}    "
        f"[bold]Positions:[/bold] {snap.open_positions}    "
        f"[bold]P&L:[/bold] [{pnl_color}]${snap.total_pnl:,.2f}[/{pnl_color}]"
    )
    console.print(Panel(portfolio_text, title="Portfolio", border_style="cyan"))

    # ---- Commentary ----
    if report.commentary:
        console.print()
        console.print(f"  [bold yellow]{report.commentary.summary}[/bold yellow]")
        console.print()
        console.print(f"  {report.commentary.market_context}")
        console.print()

        # ---- Diff section ----
        if report.diff is not None:
            _render_diff_to_console(console, report.diff)

        # Recommendations table — full list in verbose or when no diff
        if report.commentary.recommendations and (verbose or report.diff is None):
            rec_table = Table(
                title="All Recommendations" if report.diff else "Recommendations",
                show_header=True,
                header_style="bold",
                border_style="dim",
            )
            rec_table.add_column("Symbol", style="bold")
            rec_table.add_column("Action")
            rec_table.add_column("Conviction")
            rec_table.add_column("Reasoning", max_width=60)

            for r in report.commentary.recommendations:
                color = _ACTION_COLORS.get(r.action, "white")
                action_label = _ACTION_LABELS.get(r.action, r.action.upper())
                rec_table.add_row(
                    r.symbol,
                    f"[{color}]{action_label}[/{color}]",
                    r.conviction,
                    r.reasoning,
                )
            console.print(rec_table)
            console.print()

        # Position commentaries (verbose only when diff is present)
        if report.commentary.position_commentaries and (verbose or report.diff is None):
            console.rule("[bold]Position Analysis[/bold]")
            for pc in report.commentary.position_commentaries:
                console.print(f"  [bold]{pc['symbol']}[/bold]: {pc['commentary']}")
            console.print()
    else:
        console.print()
        console.print("  [dim]LLM commentary not available for this run.[/dim]")
        console.print()

    # ---- Verbose sections ----
    if verbose:
        # Analysis results
        if report.analyses:
            analysis_table = Table(
                title="Analysis Results",
                show_header=True,
                header_style="bold",
                border_style="dim",
            )
            analysis_table.add_column("Symbol", style="bold")
            analysis_table.add_column("Trend")
            analysis_table.add_column("Strength", justify="right")
            analysis_table.add_column("Support", justify="right")
            analysis_table.add_column("Resistance", justify="right")
            analysis_table.add_column("Momentum")

            for a in report.analyses:
                trend_color = {"bullish": "green", "bearish": "red"}.get(a.trend, "yellow")
                support_str = f"{a.support:.2f}" if a.support is not None else "—"
                resistance_str = f"{a.resistance:.2f}" if a.resistance is not None else "—"
                analysis_table.add_row(
                    a.symbol,
                    f"[{trend_color}]{a.trend}[/{trend_color}]",
                    f"{a.trend_strength:.2f}",
                    support_str,
                    resistance_str,
                    a.momentum,
                )
            console.print(analysis_table)
            console.print()

        # Positions detail
        if report.snapshot.positions:
            pos_table = Table(
                title="Open Positions",
                show_header=True,
                header_style="bold",
                border_style="dim",
            )
            pos_table.add_column("#", justify="right")
            pos_table.add_column("Symbol", style="bold")
            pos_table.add_column("Direction")
            pos_table.add_column("Open Rate", justify="right")
            pos_table.add_column("Amount", justify="right")
            pos_table.add_column("Units", justify="right")
            pos_table.add_column("P&L", justify="right")

            for idx, pos in enumerate(report.snapshot.positions, 1):
                pnl_str = f"${pos.pnl:,.2f}" if pos.pnl is not None else "—"
                pnl_color = ""
                if pos.pnl is not None:
                    pnl_color = "green" if pos.pnl >= 0 else "red"
                    pnl_str = f"[{pnl_color}]{pnl_str}[/{pnl_color}]"
                pos_table.add_row(
                    str(idx),
                    pos.symbol,
                    pos.direction,
                    f"{pos.open_rate:.4f}",
                    f"${pos.amount:,.2f}",
                    f"{pos.units:.4f}",
                    pnl_str,
                )
            console.print(pos_table)
            console.print()

        # Instruments
        if report.instruments:
            inst_table = Table(
                title="Instruments",
                show_header=True,
                header_style="bold",
                border_style="dim",
            )
            inst_table.add_column("Symbol", style="bold")
            inst_table.add_column("eToro ID", justify="right")
            inst_table.add_column("Asset Class")
            inst_table.add_column("Exchange")
            inst_table.add_column("Daily Candles", justify="right")

            for inst in report.instruments:
                inst_table.add_row(
                    inst.symbol,
                    str(inst.etoro_id),
                    inst.asset_class,
                    inst.exchange or "—",
                    str(inst.candle_count),
                )
            console.print(inst_table)
            console.print()

    # ---- Errors ----
    if report.errors:
        console.rule("[bold red]Errors[/bold red]")
        for err in report.errors:
            if "instrument_id" in err:
                console.print(f"  [red]Instrument {err['instrument_id']}:[/red] {err['error']}")
            else:
                console.print(f"  [red]{err.get('step', 'unknown')}:[/red] {err['error']}")
        console.print()


def _render_diff_to_console(console: Console, diff: ReportDiff) -> None:
    """Render the changes-since-last-run section."""
    prev_label = diff.previous_run_type.replace("_", " ").title()
    console.rule(f"[bold]Key Changes Since Last {prev_label}[/bold]")
    console.print()

    if diff.changed:
        change_table = Table(
            title="Recommendation Changes",
            show_header=True,
            header_style="bold",
            border_style="dim",
        )
        change_table.add_column("Symbol", style="bold")
        change_table.add_column("Previous")
        change_table.add_column("Current")
        change_table.add_column("Reasoning", max_width=60)

        for c in diff.changed:
            prev_color = _ACTION_COLORS.get(c.previous_action, "white")
            prev_label_a = _ACTION_LABELS.get(c.previous_action, c.previous_action.upper())
            new_color = _ACTION_COLORS.get(c.new_action, "white")
            new_label = _ACTION_LABELS.get(c.new_action, c.new_action.upper())
            change_table.add_row(
                c.symbol,
                f"[{prev_color}]{prev_label_a}[/{prev_color}] ({c.previous_conviction})",
                f"[{new_color}]{new_label}[/{new_color}] ({c.new_conviction})",
                c.reasoning,
            )
        console.print(change_table)
        console.print()

    if diff.new_symbols:
        new_table = Table(
            title="New Positions",
            show_header=True,
            header_style="bold",
            border_style="dim",
        )
        new_table.add_column("Symbol", style="bold")
        new_table.add_column("Action")
        new_table.add_column("Conviction")
        new_table.add_column("Reasoning", max_width=60)

        for r in diff.new_symbols:
            color = _ACTION_COLORS.get(r.action, "white")
            action_label = _ACTION_LABELS.get(r.action, r.action.upper())
            new_table.add_row(
                r.symbol,
                f"[{color}]{action_label}[/{color}]",
                r.conviction,
                r.reasoning,
            )
        console.print(new_table)
        console.print()

    if diff.removed_symbols:
        console.print("  [bold]Positions No Longer Held:[/bold]")
        for sym in diff.removed_symbols:
            console.print(f"    [dim]- {sym}[/dim]")
        console.print()

    if not diff.changed and not diff.new_symbols and not diff.removed_symbols:
        console.print("  [dim]No changes — all recommendations unchanged.[/dim]")
        console.print()

    console.print(
        f"  [dim]{diff.unchanged_count} recommendation(s) unchanged.[/dim]"
    )
    console.print()
