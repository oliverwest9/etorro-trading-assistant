"""Tests for the sector/exchange-group analysis module."""

from __future__ import annotations

from typing import Any

import pytest

from agent.analysis.sector import (
    EXCHANGE_GROUPS,
    _compute_simple_return,
    analyse_sector,
)
from agent.analysis.types import SectorGroupResult, SectorResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_instrument(
    etoro_id: int,
    symbol: str,
    exchange: str | None = None,
) -> dict[str, Any]:
    """Build a minimal instrument dict."""
    return {
        "etoro_id": etoro_id,
        "symbol": symbol,
        "exchange": exchange,
    }


def _make_candles(start_close: float, end_close: float, n: int = 5) -> list[dict[str, Any]]:
    """Build a list of candles that go from start_close to end_close linearly."""
    candles = []
    step = (end_close - start_close) / max(n - 1, 1)
    for i in range(n):
        price = start_close + step * i
        candles.append({
            "open": price - 0.5,
            "high": price + 1.0,
            "low": price - 1.0,
            "close": price,
            "volume": 1000.0,
            "timestamp": f"2024-01-{10 + i:02d}T00:00:00Z",
        })
    return candles


# ---------------------------------------------------------------------------
# Simple return tests
# ---------------------------------------------------------------------------


class TestComputeSimpleReturn:
    def test_positive_return(self) -> None:
        candles = _make_candles(100.0, 110.0)
        ret = _compute_simple_return(candles)
        assert ret == pytest.approx(10.0)  # +10%

    def test_negative_return(self) -> None:
        candles = _make_candles(100.0, 90.0)
        ret = _compute_simple_return(candles)
        assert ret == pytest.approx(-10.0)  # -10%

    def test_zero_return(self) -> None:
        candles = _make_candles(100.0, 100.0)
        ret = _compute_simple_return(candles)
        assert ret == pytest.approx(0.0)

    def test_insufficient_data(self) -> None:
        candles = [{"close": 100.0}]
        assert _compute_simple_return(candles) == 0.0
        assert _compute_simple_return([]) == 0.0

    def test_zero_first_close(self) -> None:
        candles = _make_candles(0.0, 10.0, n=3)
        # First close is 0 → should return 0.0 (no division by zero)
        assert _compute_simple_return(candles) == 0.0


# ---------------------------------------------------------------------------
# Sector analysis tests
# ---------------------------------------------------------------------------


class TestAnalyseSector:
    def test_groups_by_exchange(self) -> None:
        instruments = [
            _make_instrument(1, "AAPL", exchange="5"),    # US
            _make_instrument(2, "MSFT", exchange="33"),   # US
            _make_instrument(3, "BP", exchange="7"),      # UK
            _make_instrument(4, "BTC", exchange="8"),     # Crypto
        ]
        candle_map = {
            1: _make_candles(100, 110),  # +10%
            2: _make_candles(200, 220),  # +10%
            3: _make_candles(50, 55),    # +10%
            4: _make_candles(40000, 44000),  # +10%
        }
        result = analyse_sector(instruments, candle_map)

        assert isinstance(result, SectorResult)
        assert "US" in result.groups
        assert "UK" in result.groups
        assert "Crypto" in result.groups
        assert result.groups["US"].instrument_count == 2
        assert result.groups["UK"].instrument_count == 1
        assert result.groups["Crypto"].instrument_count == 1

    def test_null_exchange_goes_to_other(self) -> None:
        instruments = [_make_instrument(1, "UNKNOWN", exchange=None)]
        candle_map = {1: _make_candles(100, 105)}
        result = analyse_sector(instruments, candle_map)

        assert "Other" in result.groups
        assert result.groups["Other"].instrument_count == 1

    def test_unknown_exchange_goes_to_other(self) -> None:
        instruments = [_make_instrument(1, "XYZ", exchange="999")]
        candle_map = {1: _make_candles(100, 105)}
        result = analyse_sector(instruments, candle_map)

        assert "Other" in result.groups

    def test_best_and_worst_group(self) -> None:
        instruments = [
            _make_instrument(1, "AAPL", exchange="5"),   # US
            _make_instrument(2, "BP", exchange="7"),      # UK
        ]
        candle_map = {
            1: _make_candles(100, 120),  # +20%
            2: _make_candles(50, 45),    # -10%
        }
        result = analyse_sector(instruments, candle_map)

        assert result.best_group == "US"
        assert result.worst_group == "UK"

    def test_avg_return_per_group(self) -> None:
        instruments = [
            _make_instrument(1, "AAPL", exchange="5"),
            _make_instrument(2, "MSFT", exchange="5"),
        ]
        candle_map = {
            1: _make_candles(100, 110),  # +10%
            2: _make_candles(100, 120),  # +20%
        }
        result = analyse_sector(instruments, candle_map)

        us_group = result.groups["US"]
        assert us_group.avg_return_pct == pytest.approx(15.0, abs=0.1)

    def test_empty_instruments(self) -> None:
        result = analyse_sector([], {})

        assert result.groups == {}
        assert result.best_group is None
        assert result.worst_group is None

    def test_missing_candle_data_treated_as_zero_return(self) -> None:
        instruments = [_make_instrument(1, "AAPL", exchange="5")]
        candle_map: dict[int, list[dict[str, Any]]] = {}  # no candle data
        result = analyse_sector(instruments, candle_map)

        us_group = result.groups["US"]
        assert us_group.avg_return_pct == pytest.approx(0.0)

    def test_group_instruments_list(self) -> None:
        instruments = [
            _make_instrument(1, "AAPL", exchange="5"),
            _make_instrument(2, "MSFT", exchange="5"),
        ]
        candle_map = {
            1: _make_candles(100, 110),
            2: _make_candles(200, 210),
        }
        result = analyse_sector(instruments, candle_map)

        us_group = result.groups["US"]
        assert len(us_group.instruments) == 2
        symbols = [s for _, s, _ in us_group.instruments]
        assert "AAPL" in symbols
        assert "MSFT" in symbols
