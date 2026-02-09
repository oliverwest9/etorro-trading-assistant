"""Tests for the eToro market data module."""

import time

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
# Caching Tests
# =============================================================================


def test_search_instruments_uses_cache_on_repeated_calls(httpx_mock):
    """Verify that search_instruments uses cached response on repeated calls."""
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
        # First call should hit the API
        instruments1 = search_instruments(client, "AAPL")
        assert len(instruments1) == 1
        assert instruments1[0].symbol == "AAPL"

        # Second call should use cache (no additional request)
        instruments2 = search_instruments(client, "Apple")
        assert len(instruments2) == 1
        assert instruments2[0].symbol == "AAPL"

    # Verify only one request was made
    assert len(httpx_mock.get_requests()) == 1


def test_get_instrument_by_symbol_uses_cache_on_repeated_calls(httpx_mock):
    """Verify that get_instrument_by_symbol uses cached response."""
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
                    "symbolFull": "ETH",
                    "instrumentDisplayName": "Ethereum",
                    "instrumentTypeID": 10,
                },
            ],
        },
    )

    with EToroClient(_settings()) as client:
        # First call should hit the API
        btc = get_instrument_by_symbol(client, "BTC")
        assert btc.symbol == "BTC"

        # Second call for different symbol should use cache
        eth = get_instrument_by_symbol(client, "ETH")
        assert eth.symbol == "ETH"

    # Verify only one request was made
    assert len(httpx_mock.get_requests()) == 1


def test_cache_respects_ttl(httpx_mock):
    """Verify cache expires after TTL."""
    # Register response twice since cache will expire and request again
    httpx_mock.add_response(
        url="https://example.com/market-data/instruments",
        json={
            "instrumentDisplayDatas": [
                {
                    "instrumentID": 1001,
                    "symbolFull": "AAPL",
                    "instrumentDisplayName": "Apple Inc",
                    "instrumentTypeID": 5,
                }
            ]
        },
    )
    httpx_mock.add_response(
        url="https://example.com/market-data/instruments",
        json={
            "instrumentDisplayDatas": [
                {
                    "instrumentID": 1001,
                    "symbolFull": "AAPL",
                    "instrumentDisplayName": "Apple Inc",
                    "instrumentTypeID": 5,
                }
            ]
        },
    )

    # Use a very short TTL
    with EToroClient(_settings(), cache_ttl=0.1) as client:
        # First call
        instruments1 = search_instruments(client, "AAPL")
        assert len(instruments1) == 1

        # Wait for cache to expire
        time.sleep(0.2)

        # Second call should hit API again after TTL
        instruments2 = search_instruments(client, "AAPL")
        assert len(instruments2) == 1

    # Verify two requests were made
    assert len(httpx_mock.get_requests()) == 2


def test_clear_cache_forces_new_request(httpx_mock):
    """Verify clear_cache() forces a new API request."""
    # Register response twice since we'll call it twice
    httpx_mock.add_response(
        url="https://example.com/market-data/instruments",
        json={
            "instrumentDisplayDatas": [
                {
                    "instrumentID": 1001,
                    "symbolFull": "AAPL",
                    "instrumentDisplayName": "Apple Inc",
                    "instrumentTypeID": 5,
                }
            ]
        },
    )
    httpx_mock.add_response(
        url="https://example.com/market-data/instruments",
        json={
            "instrumentDisplayDatas": [
                {
                    "instrumentID": 1001,
                    "symbolFull": "AAPL",
                    "instrumentDisplayName": "Apple Inc",
                    "instrumentTypeID": 5,
                }
            ]
        },
    )

    with EToroClient(_settings()) as client:
        # First call
        search_instruments(client, "AAPL")

        # Clear cache
        client.clear_cache()

        # Second call should hit API again
        search_instruments(client, "AAPL")

    # Verify two requests were made
    assert len(httpx_mock.get_requests()) == 2
