"""Tests for reporting/generator.py — Report model and assembly logic.

Uses an in-memory SurrealDB to test ``generate_report()`` in realistic
conditions: instruments, candles, snapshots, and analyses already stored.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from surrealdb import RecordID
from surrealdb.connections.sync_template import SyncTemplate

from agent.config import Settings
from agent.db.analysis import create_analysis
from agent.db.candles import bulk_insert_candles
from agent.db.instruments import upsert_instrument
from agent.db.snapshots import create_snapshot
from agent.etoro.models import Candle, ClientPortfolio, Instrument, PositionWithPnl
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
    generate_report,
    _compute_diff,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_instruments(db: SyncTemplate) -> None:
    """Insert two test instruments into the database."""
    upsert_instrument(
        db,
        Instrument(
            instrumentID=1001,
            symbolFull="AAPL",
            instrumentDisplayName="Apple Inc.",
            instrumentTypeID=5,
            exchangeID=10,
        ),
    )
    upsert_instrument(
        db,
        Instrument(
            instrumentID=1002,
            symbolFull="BTC",
            instrumentDisplayName="Bitcoin",
            instrumentTypeID=10,
            exchangeID=None,
        ),
    )


def _seed_snapshot(db: SyncTemplate, run_type: str = "market_open") -> dict:
    """Create a portfolio snapshot with two positions."""
    portfolio = ClientPortfolio(
        positions=[
            PositionWithPnl(
                positionID=1,
                CID=1,
                openDateTime=datetime(2024, 1, 1, tzinfo=timezone.utc),
                openRate=150.0,
                instrumentID=1001,
                isBuy=True,
                takeProfitRate=200.0,
                stopLossRate=100.0,
                amount=1000.0,
                leverage=1,
                orderID=100,
                orderType=1,
                units=10.0,
                totalFees=0.0,
                initialAmountInDollars=1000.0,
                isTslEnabled=False,
                initialUnits=10.0,
                isPartiallyAltered=False,
                unitsBaseValueDollars=1000.0,
                settlementTypeID=1,
                openConversionRate=1.0,
                totalExternalFees=0.0,
                totalExternalTaxes=0.0,
                isNoTakeProfit=False,
                isNoStopLoss=False,
                lotCount=1.0,
            ),
            PositionWithPnl(
                positionID=2,
                CID=1,
                openDateTime=datetime(2024, 1, 1, tzinfo=timezone.utc),
                openRate=40000.0,
                instrumentID=1002,
                isBuy=True,
                takeProfitRate=60000.0,
                stopLossRate=30000.0,
                amount=500.0,
                leverage=1,
                orderID=101,
                orderType=1,
                units=0.01,
                totalFees=0.0,
                initialAmountInDollars=500.0,
                isTslEnabled=False,
                initialUnits=0.01,
                isPartiallyAltered=False,
                unitsBaseValueDollars=500.0,
                settlementTypeID=1,
                openConversionRate=1.0,
                totalExternalFees=0.0,
                totalExternalTaxes=0.0,
                isNoTakeProfit=False,
                isNoStopLoss=False,
                lotCount=1.0,
            ),
        ],
        credit=5000.0,
    )
    return create_snapshot(db, portfolio, run_type)


def _seed_candles(db: SyncTemplate, iid: int, count: int = 5) -> None:
    """Insert test candles for an instrument."""
    candles = [
        Candle(
            instrumentID=iid,
            fromDate=datetime(2024, 1, 10 + i, tzinfo=timezone.utc),
            open=100.0 + i,
            high=105.0 + i,
            low=99.0 + i,
            close=103.0 + i,
            volume=1_000_000.0,
        )
        for i in range(count)
    ]
    bulk_insert_candles(db, candles, iid, "1d")


def _seed_analyses(db: SyncTemplate, run_id: str) -> None:
    """Create analysis records for both test instruments."""
    for iid in (1001, 1002):
        create_analysis(
            db,
            instrument_etoro_id=iid,
            run_id=run_id,
            trend="bullish" if iid == 1001 else "bearish",
            trend_strength=0.75 if iid == 1001 else 0.45,
            price_action={
                "support": 148.0 if iid == 1001 else 38000.0,
                "resistance": 155.0 if iid == 1001 else 42000.0,
                "momentum_signal": "strong_up" if iid == 1001 else "weak_down",
                "indicators": [],
            },
            sector_context={"group_name": "US", "instrument_count": 2, "avg_return_pct": 1.5},
            raw_data={},
        )


def _base_summary(
    run_id: str = "test-run-123",
    snapshot_id: str = "portfolio_snapshot:abc",
    commentary: dict | None = None,
) -> dict:
    """Build a minimal pipeline summary dict."""
    return {
        "run_id": run_id,
        "run_type": "market_open",
        "snapshot_id": snapshot_id,
        "instruments_processed": 2,
        "instruments_failed": 0,
        "candle_counts": {1001: 5, 1002: 5},
        "analyses_created": 2,
        "commentary": commentary,
        "errors": [],
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_generate_report_assembles_all_sections(db: SyncTemplate) -> None:
    """generate_report() returns a Report with all sections populated."""
    _seed_instruments(db)
    _seed_snapshot(db)
    _seed_candles(db, 1001, 5)
    _seed_candles(db, 1002, 5)
    run_id = "test-run-001"
    _seed_analyses(db, run_id)

    commentary = {
        "summary": "Portfolio looks mixed.",
        "market_context": "Tech stable, crypto volatile.",
        "position_commentaries": [
            {"symbol": "AAPL", "commentary": "Solid performer."},
        ],
        "recommendations": [
            {"symbol": "AAPL", "action": "hold", "conviction": "medium", "reasoning": "Maintain."},
        ],
    }

    report = generate_report(_base_summary(run_id=run_id, commentary=commentary), db)

    assert isinstance(report, Report)
    assert report.run_id == run_id
    assert report.run_type == "market_open"
    assert report.generated_at is not None

    # Snapshot
    assert report.snapshot.open_positions == 2
    assert report.snapshot.cash_available == 5000.0
    assert len(report.snapshot.positions) == 2

    # Instruments
    assert len(report.instruments) == 2
    symbols = {i.symbol for i in report.instruments}
    assert symbols == {"AAPL", "BTC"}

    # Analyses
    assert len(report.analyses) == 2

    # Commentary
    assert report.commentary is not None
    assert report.commentary.summary == "Portfolio looks mixed."
    assert len(report.commentary.recommendations) == 1


def test_generate_report_no_commentary(db: SyncTemplate) -> None:
    """Report is valid when commentary is None (no LLM key)."""
    _seed_instruments(db)
    _seed_snapshot(db)
    run_id = "test-run-002"
    _seed_analyses(db, run_id)

    report = generate_report(_base_summary(run_id=run_id, commentary=None), db)

    assert report.commentary is None
    assert report.run_id == run_id
    assert report.snapshot.open_positions == 2


def test_generate_report_empty_portfolio(db: SyncTemplate) -> None:
    """generate_report() handles an empty portfolio gracefully."""
    # Create an empty snapshot
    portfolio = ClientPortfolio(positions=[], credit=10000.0)
    create_snapshot(db, portfolio, "market_open")

    summary = {
        "run_id": "test-run-003",
        "run_type": "market_open",
        "snapshot_id": "portfolio_snapshot:xyz",
        "instruments_processed": 0,
        "instruments_failed": 0,
        "candle_counts": {},
        "analyses_created": 0,
        "commentary": None,
        "errors": [],
    }

    report = generate_report(summary, db)

    assert report.snapshot.open_positions == 0
    assert len(report.snapshot.positions) == 0
    assert len(report.instruments) == 0
    assert len(report.analyses) == 0


def test_generate_report_with_errors(db: SyncTemplate) -> None:
    """Errors from the pipeline are propagated into the report."""
    _seed_instruments(db)
    _seed_snapshot(db)
    run_id = "test-run-004"

    summary = _base_summary(run_id=run_id)
    summary["errors"] = [
        {"instrument_id": 9999, "error": "candle fetch failed"},
        {"step": "commentary", "error": "API timeout"},
    ]

    report = generate_report(summary, db)

    assert len(report.errors) == 2
    assert report.errors[0]["instrument_id"] == 9999
    assert report.errors[1]["step"] == "commentary"


def test_snapshot_summary_position_fields(db: SyncTemplate) -> None:
    """Position data is correctly mapped into PositionSummary objects."""
    _seed_instruments(db)
    _seed_snapshot(db)
    run_id = "test-run-005"

    report = generate_report(_base_summary(run_id=run_id), db)

    # Find AAPL position
    aapl_positions = [p for p in report.snapshot.positions if p.symbol == "AAPL"]
    assert len(aapl_positions) == 1
    pos = aapl_positions[0]
    assert pos.instrument_id == 1001
    assert pos.direction == "Long"
    assert pos.open_rate == 150.0
    assert pos.amount == 1000.0
    assert pos.units == 10.0


def test_analysis_summary_symbol_resolution(db: SyncTemplate) -> None:
    """Analysis records resolve instrument record IDs to human-readable symbols."""
    _seed_instruments(db)
    _seed_snapshot(db)
    _seed_candles(db, 1001, 3)
    run_id = "test-run-006"
    _seed_analyses(db, run_id)

    report = generate_report(_base_summary(run_id=run_id), db)

    analysis_symbols = {a.symbol for a in report.analyses}
    assert "AAPL" in analysis_symbols or "BTC" in analysis_symbols
    # Verify the analysis details
    for a in report.analyses:
        assert a.trend in ("bullish", "bearish", "neutral")
        assert 0.0 <= a.trend_strength <= 1.0


# ---------------------------------------------------------------------------
# _compute_diff tests
# ---------------------------------------------------------------------------


def test_compute_diff_detects_action_change() -> None:
    """Detects when a recommendation's action changes."""
    current = [
        RecommendationSummary(symbol="AAPL", action="reduce", conviction="high", reasoning="Bearish now."),
    ]
    previous_report = {
        "run_id": "old-run",
        "run_type": "market_open",
        "recommendations": [
            {"symbol": "AAPL", "action": "hold", "conviction": "medium", "reasoning": "Stable."},
        ],
    }

    diff = _compute_diff(current, previous_report)
    assert len(diff.changed) == 1
    assert diff.changed[0].symbol == "AAPL"
    assert diff.changed[0].previous_action == "hold"
    assert diff.changed[0].new_action == "reduce"
    assert diff.changed[0].previous_conviction == "medium"
    assert diff.changed[0].new_conviction == "high"
    assert diff.unchanged_count == 0
    assert diff.new_symbols == []
    assert diff.removed_symbols == []


def test_compute_diff_detects_conviction_change() -> None:
    """Detects when conviction changes but action stays the same."""
    current = [
        RecommendationSummary(symbol="BTC", action="hold", conviction="high", reasoning="Stronger now."),
    ]
    previous_report = {
        "run_id": "old-run",
        "run_type": "market_open",
        "recommendations": [
            {"symbol": "BTC", "action": "hold", "conviction": "low", "reasoning": "Weak."},
        ],
    }

    diff = _compute_diff(current, previous_report)
    assert len(diff.changed) == 1
    assert diff.changed[0].previous_conviction == "low"
    assert diff.changed[0].new_conviction == "high"


def test_compute_diff_new_and_removed_symbols() -> None:
    """Detects new and removed symbols."""
    current = [
        RecommendationSummary(symbol="AAPL", action="hold", conviction="medium", reasoning="Stable."),
        RecommendationSummary(symbol="SNOW", action="buy", conviction="high", reasoning="New entry."),
    ]
    previous_report = {
        "run_id": "old-run",
        "run_type": "market_open",
        "recommendations": [
            {"symbol": "AAPL", "action": "hold", "conviction": "medium", "reasoning": "Stable."},
            {"symbol": "GOOG", "action": "sell", "conviction": "high", "reasoning": "Exit."},
        ],
    }

    diff = _compute_diff(current, previous_report)
    assert diff.unchanged_count == 1  # AAPL unchanged
    assert len(diff.new_symbols) == 1
    assert diff.new_symbols[0].symbol == "SNOW"
    assert diff.removed_symbols == ["GOOG"]
    assert diff.changed == []


def test_compute_diff_no_changes() -> None:
    """All recommendations unchanged produces zero changes."""
    current = [
        RecommendationSummary(symbol="AAPL", action="hold", conviction="medium", reasoning="Same."),
    ]
    previous_report = {
        "run_id": "old-run",
        "run_type": "market_close",
        "recommendations": [
            {"symbol": "AAPL", "action": "hold", "conviction": "medium", "reasoning": "Same."},
        ],
    }

    diff = _compute_diff(current, previous_report)
    assert diff.unchanged_count == 1
    assert diff.changed == []
    assert diff.new_symbols == []
    assert diff.removed_symbols == []


def test_compute_diff_empty_previous() -> None:
    """When previous report has no recommendations, everything is 'new'."""
    current = [
        RecommendationSummary(symbol="AAPL", action="hold", conviction="medium", reasoning="First."),
    ]
    previous_report = {
        "run_id": "old-run",
        "run_type": "market_open",
        "recommendations": [],
    }

    diff = _compute_diff(current, previous_report)
    assert len(diff.new_symbols) == 1
    assert diff.unchanged_count == 0
