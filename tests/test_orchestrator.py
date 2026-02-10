"""Tests for orchestrator.py — end-to-end data pipeline against in-memory SurrealDB.

All eToro HTTP calls are mocked via ``pytest-httpx``.  The SurrealDB is an
in-memory instance provided by the ``db`` fixture from conftest.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from surrealdb.connections.sync_template import SyncTemplate

from agent.config import Settings
from agent.db.candles import count_candles
from agent.db.instruments import get_instrument_by_etoro_id, list_instruments
from agent.db.snapshots import get_latest_snapshot, query_snapshots
from agent.etoro.client import EToroClient
from agent.orchestrator import Orchestrator, PipelineError


# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

# Two instruments in the portfolio
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


def _instruments_response(*instruments: dict) -> dict:
    """Build a ``/market-data/instruments`` API response."""
    return {"instrumentDisplayDatas": list(instruments)}


def _candles_response(instrument_id: int, count: int = 3) -> dict:
    """Build a ``/market-data/instruments/{id}/history/candles/...`` response."""
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
    """Build a ``/trading/info/real/pnl`` API response.

    Creates one position per instrument ID.
    """
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
    """Build a portfolio response with no positions."""
    return {
        "clientPortfolio": {
            "positions": [],
            "credit": 10000.0,
        }
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_full_pipeline(
    httpx_mock,
    instrument_ids: tuple[int, ...] = (1001, 1002),
    candle_count: int = 3,
) -> None:
    """Register all HTTP mocks for a successful pipeline run.

    Mocks: portfolio, instruments catalog, and candles for each instrument.
    """
    # Portfolio
    httpx_mock.add_response(
        url="https://example.com/trading/info/real/pnl",
        json=_portfolio_response(*instrument_ids),
    )
    # Instruments catalog
    instruments = []
    for iid in instrument_ids:
        if iid == 1001:
            instruments.append(_INSTRUMENT_AAPL)
        elif iid == 1002:
            instruments.append(_INSTRUMENT_BTC)
        else:
            instruments.append(
                {
                    "instrumentID": iid,
                    "symbolFull": f"SYM{iid}",
                    "instrumentDisplayName": f"Instrument {iid}",
                    "instrumentTypeID": 5,
                    "exchangeID": 1,
                }
            )
    httpx_mock.add_response(
        url="https://example.com/market-data/instruments",
        json=_instruments_response(*instruments),
    )
    # Candles for each instrument
    for iid in instrument_ids:
        httpx_mock.add_response(
            url=f"https://example.com/market-data/instruments/{iid}/history/candles/desc/OneDay/100",
            json=_candles_response(iid, candle_count),
        )


def _create_orchestrator(
    test_settings: Settings, db: SyncTemplate
) -> Orchestrator:
    """Create an Orchestrator with injected client + DB for testing."""
    client = EToroClient(test_settings)
    client.__enter__()
    return Orchestrator(test_settings, client=client, db=db)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_run_data_pipeline_creates_snapshot(
    db: SyncTemplate, test_settings: Settings, httpx_mock
) -> None:
    """Pipeline creates a portfolio snapshot in the database."""
    _mock_full_pipeline(httpx_mock)
    orch = _create_orchestrator(test_settings, db)

    summary = orch.run_data_pipeline("market_open")

    assert summary["snapshot_id"] != ""
    snapshot = get_latest_snapshot(db)
    assert snapshot is not None
    assert snapshot["run_type"] == "market_open"
    assert snapshot["open_positions"] == 2
    assert snapshot["cash_available"] == 5000.0


def test_run_data_pipeline_stores_instruments(
    db: SyncTemplate, test_settings: Settings, httpx_mock
) -> None:
    """Pipeline upserts instrument metadata into the database."""
    _mock_full_pipeline(httpx_mock)
    orch = _create_orchestrator(test_settings, db)

    orch.run_data_pipeline("market_open")

    aapl = get_instrument_by_etoro_id(db, 1001)
    btc = get_instrument_by_etoro_id(db, 1002)

    assert aapl is not None
    assert aapl["symbol"] == "AAPL"
    assert aapl["asset_class"] == "Stocks"

    assert btc is not None
    assert btc["symbol"] == "BTC"
    assert btc["asset_class"] == "Crypto"


def test_run_data_pipeline_stores_candles(
    db: SyncTemplate, test_settings: Settings, httpx_mock
) -> None:
    """Pipeline fetches and stores candles for each instrument."""
    _mock_full_pipeline(httpx_mock, candle_count=5)
    orch = _create_orchestrator(test_settings, db)

    summary = orch.run_data_pipeline("market_open")

    assert summary["candle_counts"][1001] == 5
    assert summary["candle_counts"][1002] == 5
    assert count_candles(db, 1001, "1d") == 5
    assert count_candles(db, 1002, "1d") == 5


def test_run_data_pipeline_end_to_end(
    db: SyncTemplate, test_settings: Settings, httpx_mock
) -> None:
    """Full pipeline: snapshot + instruments + candles all stored correctly."""
    _mock_full_pipeline(httpx_mock)
    orch = _create_orchestrator(test_settings, db)

    summary = orch.run_data_pipeline("market_open")

    # Summary checks
    assert summary["instruments_processed"] == 2
    assert summary["instruments_failed"] == 0
    assert summary["errors"] == []
    assert summary["run_type"] == "market_open"
    assert len(summary["run_id"]) == 36  # UUID format

    # DB checks
    assert len(list_instruments(db)) == 2
    assert len(query_snapshots(db)) == 1
    assert count_candles(db, 1001, "1d") == 3
    assert count_candles(db, 1002, "1d") == 3


def test_run_data_pipeline_skips_failed_instrument(
    db: SyncTemplate, test_settings: Settings, httpx_mock
) -> None:
    """If one instrument's candle fetch fails, the rest still succeed."""
    # Portfolio with two instruments
    httpx_mock.add_response(
        url="https://example.com/trading/info/real/pnl",
        json=_portfolio_response(1001, 1002),
    )
    # Instruments catalog
    httpx_mock.add_response(
        url="https://example.com/market-data/instruments",
        json=_instruments_response(_INSTRUMENT_AAPL, _INSTRUMENT_BTC),
    )
    # AAPL candles succeed
    httpx_mock.add_response(
        url="https://example.com/market-data/instruments/1001/history/candles/desc/OneDay/100",
        json=_candles_response(1001),
    )
    # BTC candles fail with 500
    httpx_mock.add_response(
        url="https://example.com/market-data/instruments/1002/history/candles/desc/OneDay/100",
        status_code=500,
    )
    httpx_mock.add_response(
        url="https://example.com/market-data/instruments/1002/history/candles/desc/OneDay/100",
        status_code=500,
    )
    httpx_mock.add_response(
        url="https://example.com/market-data/instruments/1002/history/candles/desc/OneDay/100",
        status_code=500,
    )

    orch = _create_orchestrator(test_settings, db)
    summary = orch.run_data_pipeline("market_open")

    # AAPL should succeed, BTC should fail
    assert summary["instruments_processed"] == 1
    assert summary["instruments_failed"] == 1
    assert len(summary["errors"]) == 1
    assert summary["errors"][0]["instrument_id"] == 1002

    # AAPL candles should be in DB
    assert count_candles(db, 1001, "1d") == 3
    # BTC candles should NOT be in DB
    assert count_candles(db, 1002, "1d") == 0

    # Snapshot should still exist (created before candle fetches)
    assert get_latest_snapshot(db) is not None


def test_run_data_pipeline_empty_portfolio(
    db: SyncTemplate, test_settings: Settings, httpx_mock
) -> None:
    """An empty portfolio completes successfully with no candles fetched."""
    httpx_mock.add_response(
        url="https://example.com/trading/info/real/pnl",
        json=_empty_portfolio_response(),
    )

    orch = _create_orchestrator(test_settings, db)
    summary = orch.run_data_pipeline("market_open")

    assert summary["instruments_processed"] == 0
    assert summary["instruments_failed"] == 0
    assert summary["candle_counts"] == {}
    assert summary["errors"] == []
    assert summary["snapshot_id"] != ""

    # Snapshot should still be created (just with 0 positions)
    snapshot = get_latest_snapshot(db)
    assert snapshot is not None
    assert snapshot["open_positions"] == 0


def test_run_data_pipeline_portfolio_fetch_failure(
    db: SyncTemplate, test_settings: Settings, httpx_mock
) -> None:
    """If the portfolio fetch fails, the pipeline raises PipelineError."""
    # All 3 retries fail
    httpx_mock.add_response(
        url="https://example.com/trading/info/real/pnl", status_code=500
    )
    httpx_mock.add_response(
        url="https://example.com/trading/info/real/pnl", status_code=500
    )
    httpx_mock.add_response(
        url="https://example.com/trading/info/real/pnl", status_code=500
    )

    orch = _create_orchestrator(test_settings, db)

    with pytest.raises(PipelineError, match="Portfolio fetch failed"):
        orch.run_data_pipeline("market_open")


def test_run_data_pipeline_instrument_resolution_failure(
    db: SyncTemplate, test_settings: Settings, httpx_mock
) -> None:
    """If instrument resolution fails, candle fetch still proceeds."""
    # Portfolio succeeds
    httpx_mock.add_response(
        url="https://example.com/trading/info/real/pnl",
        json=_portfolio_response(1001),
    )
    # Instruments catalog fails (all 3 retries)
    httpx_mock.add_response(
        url="https://example.com/market-data/instruments", status_code=500
    )
    httpx_mock.add_response(
        url="https://example.com/market-data/instruments", status_code=500
    )
    httpx_mock.add_response(
        url="https://example.com/market-data/instruments", status_code=500
    )
    # Candles still succeed (instrument resolution is best-effort)
    httpx_mock.add_response(
        url="https://example.com/market-data/instruments/1001/history/candles/desc/OneDay/100",
        json=_candles_response(1001),
    )

    orch = _create_orchestrator(test_settings, db)
    summary = orch.run_data_pipeline("market_open")

    # Pipeline should still succeed — just without instrument metadata
    assert summary["instruments_processed"] == 1
    assert count_candles(db, 1001, "1d") == 3

    # No instrument metadata was stored (resolution failed)
    assert get_instrument_by_etoro_id(db, 1001) is None


def test_run_data_pipeline_idempotent_candles(
    db: SyncTemplate, test_settings: Settings, httpx_mock
) -> None:
    """Running the pipeline twice does not duplicate candle records."""
    # First run
    _mock_full_pipeline(httpx_mock, instrument_ids=(1001,), candle_count=3)
    orch = _create_orchestrator(test_settings, db)
    orch.run_data_pipeline("market_open")

    assert count_candles(db, 1001, "1d") == 3

    # Second run (same candle data)
    _mock_full_pipeline(httpx_mock, instrument_ids=(1001,), candle_count=3)
    orch.run_data_pipeline("market_close")

    # Candle count should stay at 3 (deduplication via unique index)
    assert count_candles(db, 1001, "1d") == 3
    # But we should have two snapshots
    assert len(query_snapshots(db)) == 2


def test_run_data_pipeline_context_manager(
    test_settings: Settings, httpx_mock
) -> None:
    """Orchestrator can be used as a context manager (creates its own connections)."""
    # Mock all HTTP calls
    httpx_mock.add_response(
        url="https://example.com/trading/info/real/pnl",
        json=_empty_portfolio_response(),
    )

    # Use in-memory DB — the orchestrator creates its own
    settings = Settings(
        etoro_api_key="test-api-key",
        etoro_user_key="test-user-key",
        etoro_base_url="https://example.com",
        surreal_url="memory",
        surreal_namespace="test_ns",
        surreal_database="test_db",
        surreal_user="root",
        surreal_pass="root",
        llm_provider="openai",
        llm_api_key="test",
        llm_model="gpt-4o",
    )

    with Orchestrator(settings) as orch:
        summary = orch.run_data_pipeline("market_open")
        assert summary["instruments_processed"] == 0
        assert summary["snapshot_id"] != ""


def test_run_data_pipeline_market_close_run_type(
    db: SyncTemplate, test_settings: Settings, httpx_mock
) -> None:
    """The run_type is correctly propagated to the snapshot."""
    httpx_mock.add_response(
        url="https://example.com/trading/info/real/pnl",
        json=_portfolio_response(1001),
    )
    httpx_mock.add_response(
        url="https://example.com/market-data/instruments",
        json=_instruments_response(_INSTRUMENT_AAPL),
    )
    httpx_mock.add_response(
        url="https://example.com/market-data/instruments/1001/history/candles/desc/OneDay/100",
        json=_candles_response(1001),
    )

    orch = _create_orchestrator(test_settings, db)
    summary = orch.run_data_pipeline("market_close")

    assert summary["run_type"] == "market_close"
    snapshot = get_latest_snapshot(db)
    assert snapshot is not None
    assert snapshot["run_type"] == "market_close"


def test_run_data_pipeline_rejects_invalid_run_type(
    db: SyncTemplate, test_settings: Settings
) -> None:
    """Invalid run_type values raise ValueError."""
    orch = _create_orchestrator(test_settings, db)

    with pytest.raises(ValueError, match=r"Invalid run_type: 'invalid'"):
        orch.run_data_pipeline("invalid")  # type: ignore[arg-type]

    with pytest.raises(ValueError, match=r"Invalid run_type: 'Market_Open'"):
        orch.run_data_pipeline("Market_Open")  # type: ignore[arg-type]

    with pytest.raises(ValueError, match=r"Invalid run_type: ''"):
        orch.run_data_pipeline("")  # type: ignore[arg-type]

