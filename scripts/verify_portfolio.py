"""Manual verification script for Step 4: eToro Portfolio API.

This script fetches the live portfolio and recent trading history
from the eToro API and prints the results as JSON, and saves
the output to reports/portfolio_snapshot.json and a human-readable
markdown report to reports/<date>_portfolio_snapshot.md.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from pydantic import ValidationError

from agent.config import Settings
from agent.etoro.client import EToroClient
from agent.etoro.models import Instrument, InstrumentSearchResponse
from agent.etoro.portfolio import get_portfolio, get_trading_history

REPORTS_DIR = Path(__file__).resolve().parent.parent / "reports"


def _build_instrument_map(client: EToroClient) -> dict[int, Instrument]:
    """Fetch all instruments and return a dict keyed by instrument ID."""
    response = client.get("/market-data/instruments")
    parsed = InstrumentSearchResponse.model_validate(response.json())
    result: dict[int, Instrument] = {}
    for item in parsed.items:
        try:
            inst = Instrument.model_validate(item)
            result[inst.instrument_id] = inst
        except ValidationError:
            continue
    return result


def main():
    settings = Settings()
    ts = datetime.now(tz=timezone.utc)

    print("=" * 60)
    print("Step 4 Manual Verification: eToro Portfolio API")
    print("=" * 60)

    output: dict = {"timestamp": ts.isoformat()}

    with EToroClient(settings) as client:
        # 0. Build instrument name lookup
        print("\n[0] Fetching instrument catalogue...")
        try:
            inst_map = _build_instrument_map(client)
            print(f"    Loaded {len(inst_map)} instruments")
        except Exception as e:
            print(f"    Error fetching instruments: {e}")
            inst_map = {}

        # 1. Fetch portfolio with P&L data
        print("\n[1] Fetching portfolio (real account PnL)...")
        try:
            portfolio = get_portfolio(client)
            cp = portfolio.client_portfolio

            print(f"    Credit: ${cp.credit:.2f}")
            print(f"    Unrealised P&L: ${cp.unrealized_pnl}")
            print(f"    Bonus credit: ${cp.bonus_credit:.2f}")
            print(f"    Open positions: {len(cp.positions)}")
            print(f"    Mirrors (copy trades): {len(cp.mirrors)}")
            print(f"    Pending orders: {len(cp.orders)}")

            portfolio_data = json.loads(portfolio.model_dump_json())

            # Enrich each position with ticker/name
            for pos in portfolio_data["client_portfolio"]["positions"]:
                iid = pos["instrument_id"]
                inst = inst_map.get(iid)
                pos["ticker"] = inst.symbol if inst else None
                pos["instrument_name"] = inst.name if inst else None

            output["portfolio"] = portfolio_data
        except Exception as e:
            print(f"    Error fetching portfolio: {e}")
            output["portfolio_error"] = str(e)

        # 2. Fetch trading history (last 90 days)
        print("\n" + "-" * 60)
        print("\n[2] Fetching trading history (last 90 days)...")
        try:
            trades = get_trading_history(client)
            print(f"    Found {len(trades)} closed trades")

            output["trading_history"] = [
                json.loads(t.model_dump_json()) for t in trades
            ]
        except Exception as e:
            print(f"    Error fetching trading history: {e}")
            output["trading_history_error"] = str(e)

    # Save JSON
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = REPORTS_DIR / "portfolio_snapshot.json"
    out_path.write_text(json.dumps(output, indent=2), encoding="utf-8")

    # Generate markdown report
    md_path = REPORTS_DIR / f"{ts.strftime('%Y-%m-%d')}_portfolio_snapshot.md"
    md = _generate_markdown_report(output, inst_map)
    md_path.write_text(md, encoding="utf-8")

    print("\n" + "=" * 60)
    print(f"JSON saved to {out_path}")
    print(f"Report saved to {md_path}")
    print("=" * 60)


def _generate_markdown_report(
    output: dict, inst_map: dict[int, Instrument]
) -> str:
    """Generate a human-readable markdown report from portfolio data.
    
    Args:
        output: Portfolio snapshot data with positions and trading history.
        inst_map: Instrument lookup by ID, used as fallback when positions
                  lack ticker/name (e.g., if enrichment failed).
    """
    
    def _get_ticker_name(pos: dict) -> tuple[str, str]:
        """Extract ticker and name from position, with inst_map fallback."""
        iid = pos.get("instrument_id")
        inst = inst_map.get(iid) if iid else None
        ticker = (
            pos.get("ticker")
            or getattr(inst, 'symbol', None)
            or f"ID:{iid or '?'}"
        )
        name = (
            pos.get("instrument_name")
            or getattr(inst, 'name', None)
            or "—"
        )
        return ticker, name
    
    lines: list[str] = []
    w = lines.append

    ts = output.get("timestamp", "unknown")
    w(f"# Portfolio Snapshot — {ts[:19].replace('T', ' ')} UTC\n")
    w("Source: `scripts/verify_portfolio.py` → eToro Public API\n")
    w("---\n")

    portfolio = output.get("portfolio", {}).get("client_portfolio", {})
    if not portfolio:
        w("**Error:** No portfolio data available.\n")
        if err := output.get("portfolio_error"):
            w(f"Error: `{err}`\n")
        return "\n".join(lines)

    positions = portfolio.get("positions", [])
    credit = portfolio.get("credit", 0)
    total_pnl = portfolio.get("unrealized_pnl", 0) or 0

    # Account summary
    total_invested = sum(p.get("amount", 0) for p in positions)
    total_exposure = sum(
        p.get("unrealized_pnl", {}).get("exposure_in_account_currency", 0)
        for p in positions
    )

    w("## Account Summary\n")
    w("| Metric | Value |")
    w("|---|---:|")
    w(f"| Cash Available | ${credit:,.2f} |")
    w(f"| Total Invested | ${total_invested:,.2f} |")
    w(f"| Total Exposure | ${total_exposure:,.2f} |")
    w(f"| Unrealised P&L | ${total_pnl:,.2f} |")
    w(f"| Open Positions | {len(positions)} |")
    w("")

    # Positions table
    if positions:
        w("---\n")
        w("## Open Positions\n")
        w("| # | Ticker | Name | Open Rate | Amount | Current Rate | P&L | P&L % | Opened |")
        w("|---:|---|---|---:|---:|---:|---:|---:|---|")

        sorted_pos = sorted(positions, key=lambda p: p.get("ticker") or "ZZZ")
        for idx, pos in enumerate(sorted_pos, 1):
            ticker, name = _get_ticker_name(pos)
            open_rate = pos.get("open_rate", 0)
            amount = pos.get("amount", 0)
            pnl_data = pos.get("unrealized_pnl", {})
            pnl = pnl_data.get("pnl", 0)
            close_rate = pnl_data.get("close_rate", 0)
            pnl_pct = (pnl / amount * 100) if amount > 0 else 0
            opened = pos.get("open_date_time", "?")[:10]

            pnl_str = f"+${pnl:,.2f}" if pnl >= 0 else f"-${abs(pnl):,.2f}"
            pnl_pct_str = f"+{pnl_pct:.1f}%" if pnl_pct >= 0 else f"{pnl_pct:.1f}%"

            w(f"| {idx} | {ticker} | {name} | {open_rate:,.2f} | ${amount:,.2f} | {close_rate:,.2f} | {pnl_str} | {pnl_pct_str} | {opened} |")
        w("")

        # P&L summary
        winners = [p for p in positions if p.get("unrealized_pnl", {}).get("pnl", 0) > 0]
        losers = [p for p in positions if p.get("unrealized_pnl", {}).get("pnl", 0) < 0]
        win_total = sum(p["unrealized_pnl"]["pnl"] for p in winners)
        loss_total = sum(p["unrealized_pnl"]["pnl"] for p in losers)

        w("---\n")
        w("## P&L Summary\n")
        w("| Category | Count | Total P&L |")
        w("|---|---:|---:|")
        w(f"| Winners (P&L > 0) | {len(winners)} | +${win_total:,.2f} |")
        w(f"| Losers (P&L < 0) | {len(losers)} | -${abs(loss_total):,.2f} |")
        w(f"| **Net** | **{len(positions)}** | **${total_pnl:,.2f}** |")
        w("")

        # Top winners/losers
        by_pnl = sorted(positions, key=lambda p: p.get("unrealized_pnl", {}).get("pnl", 0), reverse=True)
        w("### Top Winners")
        for p in by_pnl[:3]:
            pnl = p.get("unrealized_pnl", {}).get("pnl", 0)
            if pnl <= 0:
                break
            ticker, name = _get_ticker_name(p)
            w(f"- **{ticker}** ({name}) — +${pnl:,.2f}")
        w("")

        w("### Top Losers")
        for p in reversed(by_pnl[-3:]):
            pnl = p.get("unrealized_pnl", {}).get("pnl", 0)
            if pnl >= 0:
                continue
            amt = p.get("amount", 0)
            pct = (pnl / amt * 100) if amt > 0 else 0
            ticker, name = _get_ticker_name(p)
            w(f"- **{ticker}** ({name}) — -${abs(pnl):,.2f} ({pct:.1f}%)")
        w("")

    # Trading history
    trades = output.get("trading_history", [])
    w("---\n")
    w("## Trading History\n")
    if trades:
        w(f"{len(trades)} closed trades in the last 90 days.")
    else:
        w("No closed trades in the last 90 days.")
    w("")

    w("---\n")
    w(f"*Generated {datetime.now(tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}*")

    return "\n".join(lines)


if __name__ == "__main__":
    main()
