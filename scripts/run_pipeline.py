#!/usr/bin/env python
"""Run the data pipeline with real API credentials.

Usage::

    python scripts/run_pipeline.py [market_open|market_close]

Loads settings from ``.env``, connects to SurrealDB, ensures the schema
is applied, then runs the full data pipeline (portfolio fetch → instrument
resolution → candle fetch → store everything in SurrealDB).

After the pipeline completes, queries SurrealDB to show detailed state
(table counts, stored instruments, candle coverage, snapshot details)
and saves a timestamped report to ``reports/``.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path

from agent.config import get_settings
from agent.db.candles import count_candles
from agent.db.instruments import list_instruments
from agent.db.schema import EXPECTED_TABLES
from agent.db.snapshots import get_latest_snapshot, query_snapshots
from agent.db.utils import normalise_response
from agent.orchestrator import Orchestrator

# Output goes to both stdout and a buffer for the report file
_buffer = StringIO()


def _log(msg: str = "") -> None:
    """Print to stdout and capture in the report buffer."""
    print(msg)
    _buffer.write(msg + "\n")


def _table_counts(orch: Orchestrator) -> dict[str, int]:
    """Query row counts for every schema table."""
    counts: dict[str, int] = {}
    for table in sorted(EXPECTED_TABLES):
        result = orch.db.query(f"SELECT count() AS total FROM {table} GROUP ALL;")
        rows = normalise_response(result)
        if rows and isinstance(rows[0], dict):
            counts[table] = int(rows[0].get("total", 0))
        else:
            counts[table] = 0
    return counts


def main() -> None:
    run_type = sys.argv[1] if len(sys.argv) > 1 else "market_open"
    if run_type not in ("market_open", "market_close"):
        print(f"Invalid run_type: {run_type!r}. Use 'market_open' or 'market_close'.")
        sys.exit(1)

    settings = get_settings()
    ts = datetime.now(tz=timezone.utc)
    ts_label = ts.strftime("%Y-%m-%d_%H%M%S")

    _log(f"# eToro Data Pipeline Report — {ts.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    _log(f"Run type: `{run_type}`\n")

    with Orchestrator(settings) as orch:
        # ---- SurrealDB state BEFORE pipeline ----
        _log("## SurrealDB State — Before Pipeline\n")
        before = _table_counts(orch)
        _log("| Table | Rows |")
        _log("|---|---:|")
        for table, count in before.items():
            _log(f"| {table} | {count} |")
        _log("")

        # ---- Run pipeline ----
        _log("## Pipeline Execution\n")
        _log("Running data pipeline...")
        summary = orch.run_data_pipeline(run_type)
        _log(f"- **Run ID:** `{summary['run_id']}`")
        _log(f"- **Snapshot ID:** `{summary['snapshot_id']}`")
        _log(f"- **Instruments processed:** {summary['instruments_processed']}")
        _log(f"- **Instruments failed:** {summary['instruments_failed']}")
        _log("")

        if summary["candle_counts"]:
            _log("### Candles Inserted This Run\n")
            _log("| Instrument ID | Candles Inserted |")
            _log("|---:|---:|")
            for iid, count in sorted(summary["candle_counts"].items()):
                _log(f"| {iid} | {count} |")
            _log("")

        if summary["errors"]:
            _log("### Errors\n")
            for err in summary["errors"]:
                _log(f"- Instrument {err['instrument_id']}: `{err['error']}`")
            _log("")

        # ---- SurrealDB state AFTER pipeline ----
        _log("## SurrealDB State — After Pipeline\n")
        after = _table_counts(orch)
        _log("| Table | Before | After | Δ |")
        _log("|---|---:|---:|---:|")
        for table in sorted(EXPECTED_TABLES):
            b = before.get(table, 0)
            a = after.get(table, 0)
            delta = a - b
            delta_str = f"+{delta}" if delta > 0 else str(delta)
            _log(f"| {table} | {b} | {a} | {delta_str} |")
        _log("")

        # ---- Instrument details ----
        instruments = list_instruments(orch.db)
        if instruments:
            _log("## Instruments in Database\n")
            _log("| Symbol | eToro ID | Asset Class | Exchange | Daily Candles |")
            _log("|---|---:|---|---|---:|")
            for inst in sorted(instruments, key=lambda i: i.get("symbol", "")):
                iid = inst.get("etoro_id", 0)
                total = count_candles(orch.db, iid, "1d")
                _log(
                    f"| {inst.get('symbol', '?')} "
                    f"| {iid} "
                    f"| {inst.get('asset_class', '?')} "
                    f"| {inst.get('exchange', '—') or '—'} "
                    f"| {total} |"
                )
            _log("")

        # ---- Latest snapshot details ----
        snapshot = get_latest_snapshot(orch.db)
        if snapshot:
            _log("## Latest Portfolio Snapshot\n")
            _log(f"- **Total value:** ${snapshot.get('total_value', 0):,.2f}")
            _log(f"- **Cash available:** ${snapshot.get('cash_available', 0):,.2f}")
            _log(f"- **Open positions:** {snapshot.get('open_positions', 0)}")
            _log(f"- **Total P&L:** ${snapshot.get('total_pnl', 0):,.2f}")
            _log(f"- **Run type:** {snapshot.get('run_type', '?')}")
            _log(f"- **Captured at:** {snapshot.get('captured_at', '?')}")
            _log("")

            # Show individual positions if present
            positions = snapshot.get("positions", [])
            if positions:
                _log(f"### Positions ({len(positions)})\n")
                _log("| # | Instrument ID | Direction | Open Rate | Amount | Units | P&L |")
                _log("|---:|---:|---|---:|---:|---:|---:|")
                for idx, pos in enumerate(positions, 1):
                    direction = "Long" if pos.get("isBuy", pos.get("is_buy")) else "Short"
                    pnl_data = pos.get("unrealizedPnL", pos.get("unrealized_pnl", {}))
                    pnl_val = "—"
                    if isinstance(pnl_data, dict) and pnl_data:
                        pnl_val = f"${pnl_data.get('pnL', pnl_data.get('pnl', 0)):,.2f}"
                    _log(
                        f"| {idx} "
                        f"| {pos.get('instrumentID', pos.get('instrument_id', '?'))} "
                        f"| {direction} "
                        f"| {pos.get('openRate', pos.get('open_rate', 0)):.4f} "
                        f"| ${pos.get('amount', 0):,.2f} "
                        f"| {pos.get('units', 0):.4f} "
                        f"| {pnl_val} |"
                    )
                _log("")

        # ---- Snapshot history ----
        all_snaps = query_snapshots(orch.db, limit=10)
        if len(all_snaps) > 1:
            _log("## Recent Snapshots (last 10)\n")
            _log("| # | Run Type | Positions | Total Value | P&L | Captured At |")
            _log("|---:|---|---:|---:|---:|---|")
            for idx, snap in enumerate(all_snaps, 1):
                _log(
                    f"| {idx} "
                    f"| {snap.get('run_type', '?')} "
                    f"| {snap.get('open_positions', 0)} "
                    f"| ${snap.get('total_value', 0):,.2f} "
                    f"| ${snap.get('total_pnl', 0):,.2f} "
                    f"| {snap.get('captured_at', '?')} |"
                )
            _log("")

    # ---- Save report to file ----
    reports_dir = Path("reports")
    reports_dir.mkdir(exist_ok=True)
    report_path = reports_dir / f"{ts_label}_{run_type}_pipeline.md"
    report_path.write_text(_buffer.getvalue(), encoding="utf-8")
    print(f"\n📄 Report saved to: {report_path}")


if __name__ == "__main__":
    main()
