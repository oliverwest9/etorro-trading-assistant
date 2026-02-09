"""Tests for the eToro market data module."""

import pytest
from pydantic import ValidationError

from agent.config import Settings
from agent.etoro.client import EToroClient
from agent.etoro.market_data import (
    InstrumentNotFoundError,
    get_candles,
    get_instrument_by_symbol,
    get_prices,
    search_instruments,
)


def _settings() -> Settings:
    return Settings(
        etoro_api_key="test-api-key",
        etoro_user_key="test-user-key",
        etoro_base_url="https://example.com",
        surreal_url="ws://localhost:8000/rpc",
        surreal_namespace="trading",
        surreal_database="agent",
        surreal_user="root",
        surreal_pass="root",
        llm_provider="openai",
        llm_api_key="test-llm",
        llm_model="gpt-4o",
    )


# =============================================================================
# Instrument Search Tests
# =============================================================================


def test_search_instruments_returns_parsed_results(httpx_mock):
    """Verify search_instruments parses the API response correctly."""
    httpx_mock.add_response(
        url="https://example.com/market-data/instruments",
        json={
            "instrumentDisplayDatas": [
                {
                    "instrumentID": 1001,
                    "symbolFull": "AAPL",
                    "instrumentDisplayName": "Apple Inc",
                    "instrumentTypeID": 5,
                    "exchangeID": 10,
                }
            ]
        },
    )

    with EToroClient(_settings()) as client:
        instruments = search_instruments(client, "AAPL")

    assert len(instruments) == 1
    assert instruments[0].instrument_id == 1001
    assert instruments[0].symbol == "AAPL"
    assert instruments[0].name == "Apple Inc"
    assert instruments[0].asset_class == "Stocks"
    assert instruments[0].exchange_id == 10


def test_search_instruments_handles_empty_results(httpx_mock):
    """Empty search returns an empty list."""
    httpx_mock.add_response(
        url="https://example.com/market-data/instruments",
        json={
            "instrumentDisplayDatas": []
        },
    )

    with EToroClient(_settings()) as client:
        instruments = search_instruments(client, "NONEXISTENT")

    assert instruments == []


def test_get_instrument_by_symbol_finds_exact_match(httpx_mock):
    """get_instrument_by_symbol returns the instrument with exact symbol match."""
    httpx_mock.add_response(
        url="https://example.com/market-data/instruments",
        json={
            "instrumentDisplayDatas": [
                {
                    "instrumentID": 100001,
                    "symbolFull": "BTC",
                    "instrumentDisplayName": "Bitcoin",
                    "instrumentTypeID": 10,
                },
                {
                    "instrumentID": 100002,
                    "symbolFull": "BTC-ETH",
                    "instrumentDisplayName": "BTC/ETH Pair",
                    "instrumentTypeID": 10,
                },
            ],
        },
    )

    with EToroClient(_settings()) as client:
        instrument = get_instrument_by_symbol(client, "BTC")

    assert instrument.instrument_id == 100001
    assert instrument.symbol == "BTC"
    assert instrument.name == "Bitcoin"


def test_get_instrument_by_symbol_raises_on_no_match(httpx_mock):
    """get_instrument_by_symbol raises InstrumentNotFoundError when no exact match."""
    httpx_mock.add_response(
        url="https://example.com/market-data/instruments",
        json={
            "instrumentDisplayDatas": [],
        },
    )

    with EToroClient(_settings()) as client:
        with pytest.raises(InstrumentNotFoundError) as excinfo:
            get_instrument_by_symbol(client, "UNKNOWN")

    assert "UNKNOWN" in str(excinfo.value)


# =============================================================================
# Candle Tests
# =============================================================================


def test_get_candles_parses_ohlcv_data(httpx_mock):
    """Verify get_candles parses OHLCV data correctly."""
    httpx_mock.add_response(
        url="https://example.com/market-data/instruments/1001/history/candles/desc/OneDay/10",
        json={
            "interval": "OneDay",
            "candles": [
                {
                    "instrumentId": 1001,
                    "candles": [
                        {
                            "instrumentID": 1001,
                            "fromDate": "2025-03-07T00:00:00Z",
                            "open": 175.50,
                            "high": 178.25,
                            "low": 174.00,
                            "close": 177.80,
                            "volume": 1234567.0,
                        },
                        {
                            "instrumentID": 1001,
                            "fromDate": "2025-03-06T00:00:00Z",
                            "open": 173.00,
                            "high": 176.50,
                            "low": 172.50,
                            "close": 175.50,
                            "volume": 987654.0,
                        },
                    ],
                    "rangeOpen": 173.00,
                    "rangeClose": 177.80,
                    "rangeHigh": 178.25,
                    "rangeLow": 172.50,
                    "volume": 2222221.0,
                }
            ],
        },
    )

    with EToroClient(_settings()) as client:
        candles = get_candles(client, 1001, interval="OneDay", count=10)

    assert len(candles) == 2
    assert candles[0].instrument_id == 1001
    assert candles[0].open == 175.50
    assert candles[0].high == 178.25
    assert candles[0].low == 174.00
    assert candles[0].close == 177.80
    assert candles[0].volume == 1234567.0


def test_get_candles_handles_various_intervals(httpx_mock):
    """Test that different interval values work correctly."""
    httpx_mock.add_response(
        url="https://example.com/market-data/instruments/1001/history/candles/asc/OneHour/50",
        json={
            "interval": "OneHour",
            "candles": [
                {
                    "instrumentId": 1001,
                    "candles": [
                        {
                            "instrumentID": 1001,
                            "fromDate": "2025-03-07T10:00:00Z",
                            "open": 175.50,
                            "high": 176.00,
                            "low": 175.25,
                            "close": 175.75,
                            "volume": 10000.0,
                        }
                    ],
                    "rangeOpen": 175.50,
                    "rangeClose": 175.75,
                    "rangeHigh": 176.00,
                    "rangeLow": 175.25,
                    "volume": 10000.0,
                }
            ],
        },
    )

    with EToroClient(_settings()) as client:
        candles = get_candles(
            client, 1001, interval="OneHour", count=50, direction="asc"
        )

    assert len(candles) == 1
    assert candles[0].close == 175.75


# =============================================================================
# Rate/Price Tests
# =============================================================================


def test_get_prices_returns_bid_ask(httpx_mock):
    """Verify get_prices parses bid/ask correctly."""
    httpx_mock.add_response(
        url="https://example.com/market-data/instruments/rates?instrumentIds=1001",
        json={
            "rates": [
                {
                    "instrumentID": 1001,
                    "bid": 177.50,
                    "ask": 177.55,
                    "lastExecution": 177.52,
                    "date": "2025-03-07T14:30:00Z",
                    "conversionRateAsk": 1.0,
                    "conversionRateBid": 1.0,
                }
            ]
        },
    )

    with EToroClient(_settings()) as client:
        prices = get_prices(client, [1001])

    assert len(prices) == 1
    assert prices[0].instrument_id == 1001
    assert prices[0].bid == 177.50
    assert prices[0].ask == 177.55
    assert prices[0].last_execution == 177.52


def test_get_prices_handles_multiple_instruments(httpx_mock):
    """Multiple instrument IDs work correctly."""
    httpx_mock.add_response(
        url="https://example.com/market-data/instruments/rates?instrumentIds=1001,1002,1003",
        json={
            "rates": [
                {
                    "instrumentID": 1001,
                    "bid": 177.50,
                    "ask": 177.55,
                    "lastExecution": 177.52,
                    "date": "2025-03-07T14:30:00Z",
                },
                {
                    "instrumentID": 1002,
                    "bid": 85000.00,
                    "ask": 85050.00,
                    "lastExecution": 85025.00,
                    "date": "2025-03-07T14:30:00Z",
                },
                {
                    "instrumentID": 1003,
                    "bid": 450.25,
                    "ask": 450.30,
                    "lastExecution": 450.27,
                    "date": "2025-03-07T14:30:00Z",
                },
            ]
        },
    )

    with EToroClient(_settings()) as client:
        prices = get_prices(client, [1001, 1002, 1003])

    assert len(prices) == 3
    assert prices[0].instrument_id == 1001
    assert prices[1].instrument_id == 1002
    assert prices[2].instrument_id == 1003


def test_get_prices_handles_empty_list():
    """Empty instrument list returns empty result without API call."""
    with EToroClient(_settings()) as client:
        prices = get_prices(client, [])

    assert prices == []


# =============================================================================
# Validation Tests
# =============================================================================


def test_invalid_instrument_response_raises_validation_error(httpx_mock):
    """Pydantic rejects malformed instrument data."""
    httpx_mock.add_response(
        url="https://example.com/market-data/instruments",
        json={
            "instrumentDisplayDatas": [
                {
                    # Missing required fields like instrumentId, displayname, etc.
                    "someUnknownField": "value",
                }
            ],
        },
    )

    with EToroClient(_settings()) as client:
        instruments = search_instruments(client, "BAD")
        assert instruments == []


def test_invalid_candle_response_raises_validation_error(httpx_mock):
    """Pydantic rejects malformed candle data."""
    httpx_mock.add_response(
        url="https://example.com/market-data/instruments/1001/history/candles/desc/OneDay/10",
        json={
            "interval": "OneDay",
            "candles": [
                {
                    "instrumentId": 1001,
                    "candles": [
                        {
                            # Missing required OHLCV fields
                            "fromDate": "2025-03-07T00:00:00Z",
                        }
                    ],
                    "rangeOpen": 0,
                    "rangeClose": 0,
                    "rangeHigh": 0,
                    "rangeLow": 0,
                    "volume": 0,
                }
            ],
        },
    )

    with EToroClient(_settings()) as client:
        with pytest.raises(ValidationError):
            get_candles(client, 1001, count=10)


# =============================================================================
# Instrument Type Mapping Tests
# =============================================================================


def test_instrument_type_id_1_maps_to_forex():
    """Instrument type ID 1 should map to Forex."""
    from agent.etoro.models import Instrument
    
    instrument = Instrument(
        instrumentID=1,
        symbolFull="EUR/USD",
        instrumentDisplayName="Euro vs US Dollar",
        instrumentTypeID=1,
    )
    assert instrument.asset_class == "Forex"


def test_instrument_type_id_4_maps_to_commodities():
    """Instrument type ID 4 should map to Commodities."""
    from agent.etoro.models import Instrument
    
    instrument = Instrument(
        instrumentID=2,
        symbolFull="GOLD",
        instrumentDisplayName="Gold",
        instrumentTypeID=4,
    )
    assert instrument.asset_class == "Commodities"


def test_instrument_type_id_5_maps_to_stocks():
    """Instrument type ID 5 should map to Stocks."""
    from agent.etoro.models import Instrument
    
    instrument = Instrument(
        instrumentID=3,
        symbolFull="AAPL",
        instrumentDisplayName="Apple Inc",
        instrumentTypeID=5,
    )
    assert instrument.asset_class == "Stocks"


def test_instrument_type_id_6_maps_to_etf():
    """Instrument type ID 6 should map to ETF."""
    from agent.etoro.models import Instrument
    
    instrument = Instrument(
        instrumentID=4,
        symbolFull="SPY",
        instrumentDisplayName="SPDR S&P 500 ETF",
        instrumentTypeID=6,
    )
    assert instrument.asset_class == "ETF"


def test_instrument_type_id_10_maps_to_crypto():
    """Instrument type ID 10 should map to Crypto."""
    from agent.etoro.models import Instrument
    
    instrument = Instrument(
        instrumentID=5,
        symbolFull="BTC",
        instrumentDisplayName="Bitcoin",
        instrumentTypeID=10,
    )
    assert instrument.asset_class == "Crypto"


def test_instrument_unknown_type_id_maps_to_other():
    """Unknown instrument type IDs should map to Other."""
    from agent.etoro.models import Instrument
    
    instrument = Instrument(
        instrumentID=6,
        symbolFull="UNKNOWN",
        instrumentDisplayName="Unknown Instrument",
        instrumentTypeID=999,
    )
    assert instrument.asset_class == "Other"
