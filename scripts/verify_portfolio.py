"""Manual verification script for Step 4: eToro Portfolio API.

This script fetches the live portfolio and recent trading history
from the eToro API and prints the results as JSON, and saves
the output to reports/portfolio_snapshot.json.
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

    print("=" * 60)
    print("Step 4 Manual Verification: eToro Portfolio API")
    print("=" * 60)

    output: dict = {"timestamp": datetime.now(tz=timezone.utc).isoformat()}

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

    # Save to file
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = REPORTS_DIR / "portfolio_snapshot.json"
    out_path.write_text(json.dumps(output, indent=2), encoding="utf-8")

    print("\n" + "=" * 60)
    print(f"Saved to {out_path}")
    print("=" * 60)


if __name__ == "__main__":
    main()
