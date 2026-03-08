"""Tests for db/analysis.py — CRUD operations against in-memory SurrealDB.

Uses the ``db`` fixture from conftest.py (fresh in-memory SurrealDB per test).
"""

from __future__ import annotations

from typing import Any

import pytest
from surrealdb import RecordID
from surrealdb.connections.sync_template import SyncTemplate

from agent.db.analysis import (
    create_analysis,
    get_analyses_by_run_id,
    get_analysis_for_instrument,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_instrument(db: SyncTemplate, etoro_id: int, symbol: str = "TEST") -> None:
    """Insert a minimal instrument record (required by the FK)."""
    db.upsert(
        RecordID("instrument", etoro_id),
        {
            "etoro_id": etoro_id,
            "symbol": symbol,
            "name": f"Test {symbol}",
            "asset_class": "Stocks",
            "is_active": True,
        },
    )


def _sample_price_action() -> dict[str, Any]:
    return {
        "support": 145.0,
        "resistance": 160.0,
        "momentum_signal": "bullish",
        "indicators": [
            {"name": "trend", "signal": "bullish", "strength": 0.8, "details": {}},
        ],
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCreateAnalysis:
    def test_creates_record(self, db: SyncTemplate) -> None:
        _seed_instrument(db, 1001, "AAPL")
        record = create_analysis(
            db,
            instrument_etoro_id=1001,
            run_id="run-001",
            trend="bullish",
            trend_strength=0.75,
            price_action=_sample_price_action(),
        )

        assert record is not None
        assert "id" in record
        assert record["trend"] == "bullish"
        assert record["trend_strength"] == 0.75
        assert record["run_id"] == "run-001"

    def test_sector_context_stored(self, db: SyncTemplate) -> None:
        _seed_instrument(db, 1001)
        sector_ctx = {
            "group_name": "US",
            "instrument_count": 3,
            "avg_return_pct": 5.2,
        }
        record = create_analysis(
            db,
            instrument_etoro_id=1001,
            run_id="run-002",
            trend="neutral",
            trend_strength=0.0,
            price_action=_sample_price_action(),
            sector_context=sector_ctx,
        )

        assert record["sector_context"]["group_name"] == "US"

    def test_raw_data_stored(self, db: SyncTemplate) -> None:
        _seed_instrument(db, 1001)
        raw = {"extra": "info", "nested": {"value": 42}}
        record = create_analysis(
            db,
            instrument_etoro_id=1001,
            run_id="run-003",
            trend="bearish",
            trend_strength=0.6,
            price_action=_sample_price_action(),
            raw_data=raw,
        )

        assert record["raw_data"]["extra"] == "info"
        assert record["raw_data"]["nested"]["value"] == 42


class TestGetAnalysesByRunId:
    def test_returns_all_for_run(self, db: SyncTemplate) -> None:
        _seed_instrument(db, 1001, "AAPL")
        _seed_instrument(db, 1002, "MSFT")

        create_analysis(
            db,
            instrument_etoro_id=1001,
            run_id="run-100",
            trend="bullish",
            trend_strength=0.8,
            price_action=_sample_price_action(),
        )
        create_analysis(
            db,
            instrument_etoro_id=1002,
            run_id="run-100",
            trend="bearish",
            trend_strength=0.6,
            price_action=_sample_price_action(),
        )
        # Different run — should not appear
        create_analysis(
            db,
            instrument_etoro_id=1001,
            run_id="run-999",
            trend="neutral",
            trend_strength=0.0,
            price_action=_sample_price_action(),
        )

        results = get_analyses_by_run_id(db, "run-100")
        assert len(results) == 2
        trends = {r["trend"] for r in results}
        assert trends == {"bullish", "bearish"}

    def test_returns_empty_for_unknown_run(self, db: SyncTemplate) -> None:
        results = get_analyses_by_run_id(db, "nonexistent")
        assert results == []


class TestGetAnalysisForInstrument:
    def test_with_run_id(self, db: SyncTemplate) -> None:
        _seed_instrument(db, 1001)
        create_analysis(
            db,
            instrument_etoro_id=1001,
            run_id="run-a",
            trend="bullish",
            trend_strength=0.9,
            price_action=_sample_price_action(),
        )
        create_analysis(
            db,
            instrument_etoro_id=1001,
            run_id="run-b",
            trend="bearish",
            trend_strength=0.5,
            price_action=_sample_price_action(),
        )

        result = get_analysis_for_instrument(db, 1001, run_id="run-a")
        assert result is not None
        assert result["trend"] == "bullish"

    def test_without_run_id_returns_latest(self, db: SyncTemplate) -> None:
        _seed_instrument(db, 1001)
        create_analysis(
            db,
            instrument_etoro_id=1001,
            run_id="run-old",
            trend="bullish",
            trend_strength=0.5,
            price_action=_sample_price_action(),
        )
        create_analysis(
            db,
            instrument_etoro_id=1001,
            run_id="run-new",
            trend="bearish",
            trend_strength=0.8,
            price_action=_sample_price_action(),
        )

        result = get_analysis_for_instrument(db, 1001)
        assert result is not None
        # Should be the latest one (bearish, run-new)
        assert result["run_id"] == "run-new"

    def test_returns_none_for_nonexistent(self, db: SyncTemplate) -> None:
        result = get_analysis_for_instrument(db, 9999)
        assert result is None
