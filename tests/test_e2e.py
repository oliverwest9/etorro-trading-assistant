"""End-to-end integration tests for the eToro trading agent pipeline.

These tests exercise the **full** pipeline (portfolio → instruments →
candles → analysis → commentary → report) against an in-memory SurrealDB
with all external HTTP calls mocked via ``pytest-httpx``.

Unlike the unit-level orchestrator tests that verify individual pipeline
stages in isolation, these E2E tests validate:

* Cross-stage data flow and referential integrity
* Cumulative state across multiple consecutive runs
* Graceful degradation under partial failures
* Configuration edge-cases
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

from typing import Generator

import pytest
from surrealdb.connections.sync_template import SyncTemplate

from agent.config import Settings
from agent.db.analysis import get_analyses_by_run_id
from agent.db.candles import count_candles, query_candles
from agent.db.instruments import (
    get_instrument_by_etoro_id,
    list_instruments,
)
from agent.db.reports import (
    get_latest_report,
    get_recommendations_for_report,
    get_report_by_run_id,
    query_reports,
)
from agent.db.snapshots import get_latest_snapshot, query_snapshots
from agent.etoro.client import EToroClient
from agent.orchestrator import Orchestrator, PipelineError
from agent.reporting.llm import (
    CommentaryResponse,
    PositionCommentary,
    Recommendation,
)

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

_INSTRUMENT_AAPL = {
    "instrumentID": 1001,
    "symbolFull": "AAPL",
    "instrumentDisplayName": "Apple Inc.",
    "instrumentTypeID": 5,
    "exchangeID": 10,
}
_INSTRUMENT_BTC = {
    "instrumentID": 1002,
    "symbolFull": "BTC",
    "instrumentDisplayName": "Bitcoin",
    "instrumentTypeID": 10,
    "exchangeID": None,
}
_INSTRUMENT_TSLA = {
    "instrumentID": 1003,
    "symbolFull": "TSLA",
    "instrumentDisplayName": "Tesla Inc.",
    "instrumentTypeID": 5,
    "exchangeID": 10,
}


def _instruments_response(*instruments: dict) -> dict:
    return {"instrumentDisplayDatas": list(instruments)}


def _candles_response(instrument_id: int, count: int = 15) -> dict:
    """Build an OHLCV candle response with *count* daily candles."""
    candles = []
    for i in range(count):
        candles.append(
            {
                "instrumentID": instrument_id,
                "fromDate": datetime(
                    2024, 1, 10 + i, tzinfo=timezone.utc
                ).isoformat(),
                "open": 150.0 + i,
                "high": 155.0 + i,
                "low": 149.0 + i,
                "close": 153.0 + i,
                "volume": 1_000_000.0,
            }
        )
    return {
        "interval": "OneDay",
        "candles": [
            {
                "instrumentId": instrument_id,
                "candles": candles,
                "rangeOpen": candles[0]["open"],
                "rangeClose": candles[-1]["close"],
                "rangeHigh": max(c["high"] for c in candles),
                "rangeLow": min(c["low"] for c in candles),
                "volume": sum(c["volume"] for c in candles),
            }
        ],
    }


def _portfolio_response(*instrument_ids: int) -> dict:
    """Build a portfolio response with one position per instrument."""
    positions = []
    for idx, iid in enumerate(instrument_ids, start=1):
        positions.append(
            {
                "positionID": 10000 + idx,
                "CID": 1,
                "openDateTime": "2024-01-01T10:00:00Z",
                "openRate": 150.0,
                "instrumentID": iid,
                "isBuy": True,
                "takeProfitRate": 200.0,
                "stopLossRate": 100.0,
                "amount": 1000.0,
                "leverage": 1,
                "orderID": 20000 + idx,
                "orderType": 1,
                "units": 10.0,
                "totalFees": 0.0,
                "initialAmountInDollars": 1000.0,
                "isTslEnabled": False,
                "initialUnits": 10.0,
                "isPartiallyAltered": False,
                "unitsBaseValueDollars": 1000.0,
                "settlementTypeID": 1,
                "openConversionRate": 1.0,
                "totalExternalFees": 0.0,
                "totalExternalTaxes": 0.0,
                "isNoTakeProfit": False,
                "isNoStopLoss": False,
                "lotCount": 1.0,
            }
        )
    return {
        "clientPortfolio": {
            "positions": positions,
            "credit": 5000.0,
            "unrealizedPnL": 250.0,
        }
    }


def _empty_portfolio_response() -> dict:
    return {
        "clientPortfolio": {
            "positions": [],
            "credit": 10000.0,
        }
    }


def _mock_commentary(
    instrument_ids: tuple[int, ...] = (1001, 1002),
) -> CommentaryResponse:
    """Build a fake LLM response for mocking ``generate_commentary``."""
    symbols = {1001: "AAPL", 1002: "BTC", 1003: "TSLA"}
    return CommentaryResponse(
        summary="Portfolio shows mixed signals across sectors.",
        market_context="US equities are stable; crypto remains volatile.",
        position_commentaries=[
            PositionCommentary(
                instrument_id=iid,
                symbol=symbols.get(iid, f"SYM{iid}"),
                commentary=f"{symbols.get(iid, f'SYM{iid}')} analysis complete.",
            )
            for iid in instrument_ids
        ],
        recommendations=[
            Recommendation(
                instrument_id=iid,
                symbol=symbols.get(iid, f"SYM{iid}"),
                action="hold",
                conviction="medium",
                reasoning=f"Maintain {symbols.get(iid, f'SYM{iid}')} position.",
            )
            for iid in instrument_ids
        ],
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BASE = "https://example.com"


def _mock_full_pipeline(
    httpx_mock,
    instrument_ids: tuple[int, ...] = (1001, 1002),
    candle_count: int = 15,
) -> None:
    """Register HTTP mocks for a successful pipeline run."""
    httpx_mock.add_response(
        url=f"{BASE}/trading/info/real/pnl",
        json=_portfolio_response(*instrument_ids),
    )
    instrument_defs = {
        1001: _INSTRUMENT_AAPL,
        1002: _INSTRUMENT_BTC,
        1003: _INSTRUMENT_TSLA,
    }
    instruments = [
        instrument_defs.get(
            iid,
            {
                "instrumentID": iid,
                "symbolFull": f"SYM{iid}",
                "instrumentDisplayName": f"Instrument {iid}",
                "instrumentTypeID": 5,
                "exchangeID": 1,
            },
        )
        for iid in instrument_ids
    ]
    httpx_mock.add_response(
        url=f"{BASE}/market-data/instruments",
        json=_instruments_response(*instruments),
    )
    # Crypto instruments (instrumentTypeID=10) get 200 candles (adaptive),
    # others get 100.
    crypto_ids = {
        inst["instrumentID"]
        for inst in instruments
        if inst.get("instrumentTypeID") == 10
    }
    for iid in instrument_ids:
        count = 200 if iid in crypto_ids else 100
        httpx_mock.add_response(
            url=f"{BASE}/market-data/instruments/{iid}/history/candles/desc/OneDay/{count}",
            json=_candles_response(iid, candle_count),
        )


@pytest.fixture()
def orch(
    test_settings: Settings, db: SyncTemplate
) -> Generator[Orchestrator, None, None]:
    """Provide an Orchestrator with managed client lifecycle."""
    client = EToroClient(test_settings)
    with client:
        yield Orchestrator(test_settings, client=client, db=db)


# ===================================================================
# E2E Tests
# ===================================================================


class TestFullHappyPath:
    """Full pipeline run with mocked API + LLM — verify all DB state."""

    def test_complete_pipeline_with_commentary(
        self, orch: Orchestrator, db: SyncTemplate, test_settings: Settings, httpx_mock
    ) -> None:
        """Run the entire pipeline and verify every layer of persisted data."""
        _mock_full_pipeline(httpx_mock)
        test_settings.llm_api_key = "test-key"

        mock_resp = _mock_commentary(instrument_ids=(1001, 1002))
        with patch(
            "agent.orchestrator.generate_commentary",
            return_value=mock_resp,
        ):
            summary = orch.run_data_pipeline("market_open")

        # -- Summary-level checks --
        assert summary["run_type"] == "market_open"
        assert len(summary["run_id"]) == 36
        assert summary["instruments_processed"] == 2
        assert summary["instruments_failed"] == 0
        assert summary["errors"] == []
        assert summary["analyses_created"] == 2
        assert summary["commentary"] is not None

        run_id = summary["run_id"]

        # -- Snapshot --
        snapshot = get_latest_snapshot(db)
        assert snapshot is not None
        assert snapshot["run_type"] == "market_open"
        assert snapshot["open_positions"] == 2
        assert snapshot["cash_available"] == 5000.0

        # -- Instruments --
        instruments = list_instruments(db)
        assert len(instruments) == 2
        aapl = get_instrument_by_etoro_id(db, 1001)
        btc = get_instrument_by_etoro_id(db, 1002)
        assert aapl is not None and aapl["symbol"] == "AAPL"
        assert btc is not None and btc["symbol"] == "BTC"

        # -- Candles --
        assert count_candles(db, 1001, "1d") == 15
        assert count_candles(db, 1002, "1d") == 15

        # -- Analysis --
        analyses = get_analyses_by_run_id(db, run_id)
        assert len(analyses) == 2
        for a in analyses:
            assert a["trend"] in ("bullish", "bearish", "neutral")
            assert 0.0 <= a["trend_strength"] <= 1.0
            assert "price_action" in a
            assert a["run_id"] == run_id

        # -- Report --
        report = get_report_by_run_id(db, run_id)
        assert report is not None
        assert report["run_type"] == "market_open"
        assert report["summary"] == "Portfolio shows mixed signals across sectors."
        assert "US equities" in report["commentary"]
        assert report["report_markdown"] != ""

        # -- Recommendations --
        report_id = summary["commentary"]["report_id"]
        recs = get_recommendations_for_report(db, report_id)
        assert len(recs) == 2
        for rec in recs:
            assert rec["action"] == "hold"
            assert rec["conviction"] == "medium"


class TestConsecutiveRuns:
    """Simulate two daily runs and validate cumulative DB state."""

    def test_market_open_then_close_accumulates_state(
        self, orch: Orchestrator, db: SyncTemplate, test_settings: Settings, httpx_mock
    ) -> None:
        """Two consecutive runs produce independent snapshots and reports."""
        test_settings.llm_api_key = "test-key"

        # ---- Run 1: market_open ----
        _mock_full_pipeline(httpx_mock, instrument_ids=(1001, 1002))
        mock_resp_1 = _mock_commentary(instrument_ids=(1001, 1002))
        with patch(
            "agent.orchestrator.generate_commentary",
            return_value=mock_resp_1,
        ):
            summary_1 = orch.run_data_pipeline("market_open")

        # ---- Run 2: market_close ----
        _mock_full_pipeline(httpx_mock, instrument_ids=(1001, 1002))
        mock_resp_2 = _mock_commentary(instrument_ids=(1001, 1002))
        with patch(
            "agent.orchestrator.generate_commentary",
            return_value=mock_resp_2,
        ):
            summary_2 = orch.run_data_pipeline("market_close")

        # Run IDs must be unique
        assert summary_1["run_id"] != summary_2["run_id"]

        # Two snapshots (one per run)
        snapshots = query_snapshots(db)
        assert len(snapshots) == 2
        run_types = {s["run_type"] for s in snapshots}
        assert run_types == {"market_open", "market_close"}

        # Two reports
        reports = query_reports(db)
        assert len(reports) == 2

        # Candles are deduplicated (same dates, same instruments)
        assert count_candles(db, 1001, "1d") == 15
        assert count_candles(db, 1002, "1d") == 15

        # Instruments are not duplicated
        assert len(list_instruments(db)) == 2

        # Each run has its own analysis records
        analyses_1 = get_analyses_by_run_id(db, summary_1["run_id"])
        analyses_2 = get_analyses_by_run_id(db, summary_2["run_id"])
        assert len(analyses_1) == 2
        assert len(analyses_2) == 2

    def test_portfolio_changes_between_runs(
        self, orch: Orchestrator, db: SyncTemplate, test_settings: Settings, httpx_mock
    ) -> None:
        """A new instrument appearing in run 2 is tracked correctly."""

        # Run 1: only AAPL
        _mock_full_pipeline(httpx_mock, instrument_ids=(1001,))
        summary_1 = orch.run_data_pipeline("market_open")
        assert summary_1["instruments_processed"] == 1

        # Run 2: AAPL + TSLA (new position opened)
        _mock_full_pipeline(httpx_mock, instrument_ids=(1001, 1003))
        summary_2 = orch.run_data_pipeline("market_close")
        assert summary_2["instruments_processed"] == 2

        # DB should have both instruments
        assert len(list_instruments(db)) == 2
        assert get_instrument_by_etoro_id(db, 1001) is not None
        assert get_instrument_by_etoro_id(db, 1003) is not None

        # Two snapshots with different position counts
        snapshots = query_snapshots(db)
        assert len(snapshots) == 2
        position_counts = sorted(s["open_positions"] for s in snapshots)
        assert position_counts == [1, 2]


class TestGracefulDegradation:
    """Verify the pipeline degrades gracefully under partial failures."""

    def test_instrument_failure_does_not_block_others(
        self, orch: Orchestrator, db: SyncTemplate, test_settings: Settings, httpx_mock
    ) -> None:
        """One instrument failing candles still allows the rest to complete."""
        # Portfolio with two instruments
        httpx_mock.add_response(
            url=f"{BASE}/trading/info/real/pnl",
            json=_portfolio_response(1001, 1002),
        )
        httpx_mock.add_response(
            url=f"{BASE}/market-data/instruments",
            json=_instruments_response(_INSTRUMENT_AAPL, _INSTRUMENT_BTC),
        )
        # AAPL candles succeed
        httpx_mock.add_response(
            url=f"{BASE}/market-data/instruments/1001/history/candles/desc/OneDay/100",
            json=_candles_response(1001),
        )
        # BTC candles fail (3 retries, crypto gets 200 via adaptive count)
        for _ in range(3):
            httpx_mock.add_response(
                url=f"{BASE}/market-data/instruments/1002/history/candles/desc/OneDay/200",
                status_code=500,
            )

        summary = orch.run_data_pipeline("market_open")

        assert summary["instruments_processed"] == 1
        assert summary["instruments_failed"] == 1
        assert len(summary["errors"]) == 1
        assert summary["errors"][0]["instrument_id"] == 1002

        # AAPL data persisted, BTC not
        assert count_candles(db, 1001, "1d") == 15
        assert count_candles(db, 1002, "1d") == 0

        # Snapshot still created
        assert get_latest_snapshot(db) is not None

        # Analysis only for AAPL
        assert summary["analyses_created"] == 1

    def test_llm_failure_still_produces_analysis(
        self, orch: Orchestrator, db: SyncTemplate, test_settings: Settings, httpx_mock
    ) -> None:
        """An LLM failure does not prevent analysis from being persisted."""
        _mock_full_pipeline(httpx_mock)
        test_settings.llm_api_key = "test-key"

        with patch(
            "agent.orchestrator.generate_commentary",
            side_effect=RuntimeError("LLM API down"),
        ):
            summary = orch.run_data_pipeline("market_open")

        # Commentary failed but everything else succeeded
        assert summary["commentary"] is None
        assert summary["instruments_processed"] == 2
        assert summary["analyses_created"] == 2
        assert count_candles(db, 1001, "1d") == 15

        # No report was created
        report = get_report_by_run_id(db, summary["run_id"])
        assert report is None

        # Errors list contains the commentary failure
        llm_errors = [e for e in summary["errors"] if e.get("step") == "commentary"]
        assert len(llm_errors) == 1

    def test_portfolio_failure_is_fatal(
        self, orch: Orchestrator, db: SyncTemplate, test_settings: Settings, httpx_mock
    ) -> None:
        """Portfolio fetch failure raises PipelineError and persists nothing."""
        for _ in range(3):
            httpx_mock.add_response(
                url=f"{BASE}/trading/info/real/pnl",
                status_code=500,
            )

        with pytest.raises(PipelineError, match="Portfolio fetch failed"):
            orch.run_data_pipeline("market_open")

        # Nothing should be persisted
        assert get_latest_snapshot(db) is None
        assert len(list_instruments(db)) == 0

    def test_instrument_catalog_failure_still_stores_candles(
        self, orch: Orchestrator, db: SyncTemplate, test_settings: Settings, httpx_mock
    ) -> None:
        """If instrument resolution fails, candles are still fetched and stored."""
        httpx_mock.add_response(
            url=f"{BASE}/trading/info/real/pnl",
            json=_portfolio_response(1001),
        )
        # Instruments catalog fails (3 retries)
        for _ in range(3):
            httpx_mock.add_response(
                url=f"{BASE}/market-data/instruments",
                status_code=500,
            )
        # Candles succeed
        httpx_mock.add_response(
            url=f"{BASE}/market-data/instruments/1001/history/candles/desc/OneDay/100",
            json=_candles_response(1001),
        )

        summary = orch.run_data_pipeline("market_open")

        # Candles were stored even without instrument metadata
        assert summary["instruments_processed"] == 1
        assert count_candles(db, 1001, "1d") == 15

        # But instrument metadata was NOT stored
        assert get_instrument_by_etoro_id(db, 1001) is None


class TestEmptyAndEdgeCases:
    """Edge cases: empty portfolio, single instrument, invalid config."""

    def test_empty_portfolio_produces_snapshot_only(
        self, orch: Orchestrator, db: SyncTemplate, test_settings: Settings, httpx_mock
    ) -> None:
        """An empty portfolio creates a snapshot but no instruments or analyses."""
        httpx_mock.add_response(
            url=f"{BASE}/trading/info/real/pnl",
            json=_empty_portfolio_response(),
        )

        summary = orch.run_data_pipeline("market_open")

        assert summary["instruments_processed"] == 0
        assert summary["analyses_created"] == 0
        assert summary["commentary"] is None
        assert summary["errors"] == []

        snapshot = get_latest_snapshot(db)
        assert snapshot is not None
        assert snapshot["open_positions"] == 0
        assert snapshot["cash_available"] == 10000.0

        assert len(list_instruments(db)) == 0

    def test_single_instrument_pipeline(
        self, orch: Orchestrator, db: SyncTemplate, test_settings: Settings, httpx_mock
    ) -> None:
        """Pipeline works correctly with a single-instrument portfolio."""
        _mock_full_pipeline(httpx_mock, instrument_ids=(1001,))
        test_settings.llm_api_key = "test-key"

        mock_resp = _mock_commentary(instrument_ids=(1001,))
        with patch(
            "agent.orchestrator.generate_commentary",
            return_value=mock_resp,
        ):
            summary = orch.run_data_pipeline("market_close")

        assert summary["instruments_processed"] == 1
        assert summary["analyses_created"] == 1
        assert summary["commentary"] is not None
        assert len(summary["commentary"]["recommendations"]) == 1
        assert summary["commentary"]["recommendations"][0]["symbol"] == "AAPL"

    def test_invalid_run_type_raises(
        self, orch: Orchestrator, db: SyncTemplate, test_settings: Settings
    ) -> None:
        """An invalid run_type is rejected before any API calls."""

        with pytest.raises(ValueError, match="Invalid run_type"):
            orch.run_data_pipeline("invalid_type")


class TestDataIntegrity:
    """Verify cross-table referential consistency after a full run."""

    def test_report_references_valid_snapshot(
        self, orch: Orchestrator, db: SyncTemplate, test_settings: Settings, httpx_mock
    ) -> None:
        """The report's portfolio_snapshot FK points to an existing snapshot."""
        _mock_full_pipeline(httpx_mock)
        test_settings.llm_api_key = "test-key"

        mock_resp = _mock_commentary()
        with patch(
            "agent.orchestrator.generate_commentary",
            return_value=mock_resp,
        ):
            summary = orch.run_data_pipeline("market_open")

        report = get_report_by_run_id(db, summary["run_id"])
        assert report is not None

        # The snapshot_id in the summary should correspond to a real snapshot
        snapshot_id = summary["snapshot_id"]
        assert snapshot_id != ""

        snapshot = get_latest_snapshot(db)
        assert snapshot is not None
        assert str(snapshot["id"]) == snapshot_id

    def test_analyses_match_stored_candle_data(
        self, orch: Orchestrator, db: SyncTemplate, test_settings: Settings, httpx_mock
    ) -> None:
        """Analyses are based on candle data that is actually in the DB."""
        _mock_full_pipeline(httpx_mock, candle_count=20)

        summary = orch.run_data_pipeline("market_open")

        # Both instruments have candles in the DB
        for iid in (1001, 1002):
            candles = query_candles(db, iid, "1d")
            assert len(candles) == 20

        # Analyses reference these instruments
        analyses = get_analyses_by_run_id(db, summary["run_id"])
        assert len(analyses) == 2

    def test_recommendations_reference_valid_analyses(
        self, orch: Orchestrator, db: SyncTemplate, test_settings: Settings, httpx_mock
    ) -> None:
        """Each recommendation's analysis FK points to an existing analysis."""
        _mock_full_pipeline(httpx_mock)
        test_settings.llm_api_key = "test-key"

        mock_resp = _mock_commentary()
        with patch(
            "agent.orchestrator.generate_commentary",
            return_value=mock_resp,
        ):
            summary = orch.run_data_pipeline("market_open")

        report_id = summary["commentary"]["report_id"]
        recs = get_recommendations_for_report(db, report_id)

        # Each recommendation should have an analysis FK
        for rec in recs:
            assert "analysis" in rec
            assert rec["analysis"] is not None

    def test_run_id_consistent_across_all_records(
        self, orch: Orchestrator, db: SyncTemplate, test_settings: Settings, httpx_mock
    ) -> None:
        """The same run_id links snapshot, analyses, and report."""
        _mock_full_pipeline(httpx_mock)
        test_settings.llm_api_key = "test-key"

        mock_resp = _mock_commentary()
        with patch(
            "agent.orchestrator.generate_commentary",
            return_value=mock_resp,
        ):
            summary = orch.run_data_pipeline("market_open")

        run_id = summary["run_id"]

        # All analyses share the run_id
        analyses = get_analyses_by_run_id(db, run_id)
        assert len(analyses) == 2
        for a in analyses:
            assert a["run_id"] == run_id

        # The report shares the run_id
        report = get_report_by_run_id(db, run_id)
        assert report is not None
        assert report["run_id"] == run_id


class TestContextManager:
    """Verify Orchestrator context-manager lifecycle for E2E scenarios."""

    def test_orchestrator_creates_and_tears_down_resources(
        self, test_settings: Settings, httpx_mock
    ) -> None:
        """The context manager creates its own client + DB and cleans up."""
        httpx_mock.add_response(
            url=f"{BASE}/trading/info/real/pnl",
            json=_empty_portfolio_response(),
        )

        with Orchestrator(test_settings) as orch:
            summary = orch.run_data_pipeline("market_open")
            assert summary["snapshot_id"] != ""
            assert summary["instruments_processed"] == 0
