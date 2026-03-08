"""Tests for the analysis engine — price-action indicators, registry, and aggregation.

Uses synthetic candle data with known trends to verify correct classification.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import pytest

from agent.analysis.indicators.levels import LevelsIndicator
from agent.analysis.indicators.momentum import MomentumIndicator
from agent.analysis.indicators.trend import TrendIndicator
from agent.analysis.price_action import (
    _candles_to_dataframe,
    analyse_price_action,
)
from agent.analysis.registry import Indicator, IndicatorRegistry
from agent.analysis.types import IndicatorResult, PriceActionResult


# ---------------------------------------------------------------------------
# Helpers — synthetic candle builders
# ---------------------------------------------------------------------------


def _make_candle(
    open_: float,
    high: float,
    low: float,
    close: float,
    volume: float = 1000.0,
    day_offset: int = 0,
) -> dict[str, Any]:
    """Build a single candle dict."""
    return {
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "timestamp": f"2024-01-{10 + day_offset:02d}T00:00:00Z",
    }


def _uptrend_candles(n: int = 15) -> list[dict[str, Any]]:
    """Create a clear uptrend: higher highs and higher lows."""
    candles = []
    base = 100.0
    for i in range(n):
        open_ = base + i * 2
        close = open_ + 1.5
        high = close + 0.5
        low = open_ - 0.3
        candles.append(_make_candle(open_, high, low, close, day_offset=i))
    return candles


def _downtrend_candles(n: int = 15) -> list[dict[str, Any]]:
    """Create a clear downtrend: lower highs and lower lows."""
    candles = []
    base = 200.0
    for i in range(n):
        open_ = base - i * 2
        close = open_ - 1.5
        high = open_ + 0.3
        low = close - 0.5
        candles.append(_make_candle(open_, high, low, close, day_offset=i))
    return candles


def _flat_candles(n: int = 15) -> list[dict[str, Any]]:
    """Create a sideways market: price oscillates around a level."""
    candles = []
    base = 150.0
    for i in range(n):
        offset = 0.5 if i % 2 == 0 else -0.5
        open_ = base + offset
        close = base - offset
        high = base + 1.0
        low = base - 1.0
        candles.append(_make_candle(open_, high, low, close, day_offset=i))
    return candles


# ---------------------------------------------------------------------------
# Trend indicator tests
# ---------------------------------------------------------------------------


class TestTrendIndicator:
    def test_bullish_trend_detected(self) -> None:
        indicator = TrendIndicator(window=10)
        candles = _uptrend_candles()
        df = _candles_to_dataframe(candles)
        result = indicator.analyse(df)

        assert result.name == "trend"
        assert result.signal == "bullish"
        assert result.strength > 0.5

    def test_bearish_trend_detected(self) -> None:
        indicator = TrendIndicator(window=10)
        candles = _downtrend_candles()
        df = _candles_to_dataframe(candles)
        result = indicator.analyse(df)

        assert result.name == "trend"
        assert result.signal == "bearish"
        assert result.strength > 0.5

    def test_neutral_on_flat_market(self) -> None:
        indicator = TrendIndicator(window=10)
        candles = _flat_candles()
        df = _candles_to_dataframe(candles)
        result = indicator.analyse(df)

        assert result.name == "trend"
        # Flat market: should NOT be strongly bullish or bearish
        # (may be neutral or weak signal depending on oscillation)
        assert result.strength <= 0.6

    def test_insufficient_data_returns_neutral(self) -> None:
        indicator = TrendIndicator()
        candles = [_make_candle(100, 101, 99, 100.5)]
        df = _candles_to_dataframe(candles)
        result = indicator.analyse(df)

        assert result.signal == "neutral"
        assert result.strength == 0.0

    def test_custom_window(self) -> None:
        indicator = TrendIndicator(window=5)
        candles = _uptrend_candles(20)
        df = _candles_to_dataframe(candles)
        result = indicator.analyse(df)

        # Should still detect the uptrend with a smaller window
        assert result.signal == "bullish"
        assert result.details["window"] == 5


# ---------------------------------------------------------------------------
# Momentum indicator tests
# ---------------------------------------------------------------------------


class TestMomentumIndicator:
    def test_bullish_momentum(self) -> None:
        indicator = MomentumIndicator(window=5, threshold=1.0)
        candles = _uptrend_candles(10)
        df = _candles_to_dataframe(candles)
        result = indicator.analyse(df)

        assert result.name == "momentum"
        assert result.signal == "bullish"
        assert result.details["roc_pct"] > 0

    def test_bearish_momentum(self) -> None:
        indicator = MomentumIndicator(window=5, threshold=1.0)
        candles = _downtrend_candles(10)
        df = _candles_to_dataframe(candles)
        result = indicator.analyse(df)

        assert result.name == "momentum"
        assert result.signal == "bearish"
        assert result.details["roc_pct"] < 0

    def test_neutral_on_flat_market(self) -> None:
        indicator = MomentumIndicator(window=5, threshold=2.0)
        candles = _flat_candles(10)
        df = _candles_to_dataframe(candles)
        result = indicator.analyse(df)

        assert result.name == "momentum"
        assert result.signal == "neutral"

    def test_insufficient_data_returns_neutral(self) -> None:
        indicator = MomentumIndicator()
        candles = [_make_candle(100, 101, 99, 100.5)]
        df = _candles_to_dataframe(candles)
        result = indicator.analyse(df)

        assert result.signal == "neutral"
        assert result.strength == 0.0


# ---------------------------------------------------------------------------
# Levels indicator tests
# ---------------------------------------------------------------------------


class TestLevelsIndicator:
    def test_detects_swing_levels(self) -> None:
        """A V-shaped or zigzag pattern should produce support/resistance."""
        indicator = LevelsIndicator(order=2, max_levels=3)
        # Create a zigzag pattern with clear swing points
        candles = []
        prices = [100, 105, 110, 107, 103, 98, 95, 99, 104, 108, 113, 110, 106]
        for i, p in enumerate(prices):
            candles.append(_make_candle(p - 1, p + 1, p - 2, p, day_offset=i))
        df = _candles_to_dataframe(candles)
        result = indicator.analyse(df)

        assert result.name == "levels"
        assert "support_levels" in result.details
        assert "resistance_levels" in result.details

    def test_insufficient_data_returns_neutral(self) -> None:
        indicator = LevelsIndicator(order=3)
        candles = [_make_candle(100, 101, 99, 100.5, day_offset=i) for i in range(3)]
        df = _candles_to_dataframe(candles)
        result = indicator.analyse(df)

        assert result.signal == "neutral"
        assert result.strength == 0.0
        assert result.details["reason"] == "insufficient data"


# ---------------------------------------------------------------------------
# Registry tests
# ---------------------------------------------------------------------------


class TestIndicatorRegistry:
    def test_register_and_run_all(self) -> None:
        registry = IndicatorRegistry()
        registry.register(TrendIndicator())
        registry.register(MomentumIndicator())

        candles = _uptrend_candles()
        df = _candles_to_dataframe(candles)
        results = registry.run_all(df)

        assert len(results) == 2
        assert results[0].name == "trend"
        assert results[1].name == "momentum"

    def test_register_duplicate_raises(self) -> None:
        registry = IndicatorRegistry()
        registry.register(TrendIndicator())

        with pytest.raises(ValueError, match="already registered"):
            registry.register(TrendIndicator())

    def test_register_invalid_type_raises(self) -> None:
        registry = IndicatorRegistry()

        with pytest.raises(TypeError, match="Expected an Indicator"):
            registry.register("not an indicator")  # type: ignore[arg-type]

    def test_run_single_by_name(self) -> None:
        registry = IndicatorRegistry()
        registry.register(TrendIndicator())

        candles = _uptrend_candles()
        df = _candles_to_dataframe(candles)
        result = registry.run("trend", df)

        assert result.name == "trend"

    def test_run_unknown_name_raises(self) -> None:
        registry = IndicatorRegistry()

        candles = _uptrend_candles()
        df = _candles_to_dataframe(candles)

        with pytest.raises(KeyError, match="No indicator registered"):
            registry.run("nonexistent", df)

    def test_names_property(self) -> None:
        registry = IndicatorRegistry()
        registry.register(TrendIndicator())
        registry.register(MomentumIndicator())

        assert registry.names == ["trend", "momentum"]

    def test_len_and_contains(self) -> None:
        registry = IndicatorRegistry()
        assert len(registry) == 0
        assert "trend" not in registry

        registry.register(TrendIndicator())
        assert len(registry) == 1
        assert "trend" in registry

    def test_custom_indicator_extension(self) -> None:
        """A user-defined indicator can be registered and runs with the rest."""

        class CustomIndicator:
            @property
            def name(self) -> str:
                return "custom_rsi"

            def analyse(self, df: pd.DataFrame) -> IndicatorResult:
                return IndicatorResult(
                    name=self.name,
                    signal="bullish",
                    strength=0.75,
                    details={"custom_metric": 42},
                )

        registry = IndicatorRegistry()
        registry.register(TrendIndicator())
        registry.register(CustomIndicator())

        candles = _uptrend_candles()
        df = _candles_to_dataframe(candles)
        results = registry.run_all(df)

        assert len(results) == 2
        custom = [r for r in results if r.name == "custom_rsi"]
        assert len(custom) == 1
        assert custom[0].strength == 0.75
        assert custom[0].details["custom_metric"] == 42

    def test_broken_indicator_produces_neutral_fallback(self) -> None:
        """An indicator that raises is gracefully handled."""

        class BrokenIndicator:
            @property
            def name(self) -> str:
                return "broken"

            def analyse(self, df: pd.DataFrame) -> IndicatorResult:
                raise RuntimeError("Something went wrong")

        registry = IndicatorRegistry()
        registry.register(TrendIndicator())
        registry.register(BrokenIndicator())

        candles = _uptrend_candles()
        df = _candles_to_dataframe(candles)
        results = registry.run_all(df)

        assert len(results) == 2
        broken_result = [r for r in results if r.name == "broken"][0]
        assert broken_result.signal == "neutral"
        assert broken_result.strength == 0.0
        assert "error" in broken_result.details


# ---------------------------------------------------------------------------
# Default registry tests
# ---------------------------------------------------------------------------


class TestDefaultRegistry:
    def test_default_registry_has_three_indicators(self) -> None:
        from agent.analysis.indicators import default_registry

        assert len(default_registry) == 3
        assert "trend" in default_registry
        assert "momentum" in default_registry
        assert "levels" in default_registry


# ---------------------------------------------------------------------------
# Price action analysis tests
# ---------------------------------------------------------------------------


class TestAnalysePriceAction:
    def test_uptrend_analysis(self) -> None:
        candles = _uptrend_candles()
        result = analyse_price_action(candles)

        assert isinstance(result, PriceActionResult)
        assert result.trend == "bullish"
        assert result.trend_strength > 0
        assert len(result.indicators) == 3

    def test_downtrend_analysis(self) -> None:
        candles = _downtrend_candles()
        result = analyse_price_action(candles)

        assert isinstance(result, PriceActionResult)
        assert result.trend == "bearish"
        assert result.trend_strength > 0

    def test_empty_candles_returns_neutral(self) -> None:
        result = analyse_price_action([])

        assert result.trend == "neutral"
        assert result.trend_strength == 0.0
        assert result.indicators == []

    def test_custom_registry(self) -> None:
        """analyse_price_action uses a custom registry when provided."""

        class AlwaysBullish:
            @property
            def name(self) -> str:
                return "always_bullish"

            def analyse(self, df: pd.DataFrame) -> IndicatorResult:
                return IndicatorResult(
                    name=self.name, signal="bullish", strength=1.0
                )

        registry = IndicatorRegistry()
        registry.register(AlwaysBullish())

        candles = _flat_candles()
        result = analyse_price_action(candles, registry=registry)

        assert result.trend == "bullish"
        assert len(result.indicators) == 1

    def test_momentum_signal_extracted(self) -> None:
        candles = _uptrend_candles()
        result = analyse_price_action(candles)

        # momentum_signal should come from the momentum indicator
        assert result.momentum_signal in ("bullish", "bearish", "neutral")


# ---------------------------------------------------------------------------
# DataFrame conversion tests
# ---------------------------------------------------------------------------


class TestCandlesToDataframe:
    def test_empty_list_returns_empty_df(self) -> None:
        df = _candles_to_dataframe([])
        assert len(df) == 0
        assert "open" in df.columns

    def test_sorted_by_timestamp(self) -> None:
        candles = [
            _make_candle(100, 101, 99, 100, day_offset=3),
            _make_candle(100, 101, 99, 100, day_offset=1),
            _make_candle(100, 101, 99, 100, day_offset=2),
        ]
        df = _candles_to_dataframe(candles)
        timestamps = df["timestamp"].tolist()
        assert timestamps == sorted(timestamps)

    def test_missing_required_column_raises(self) -> None:
        candles = [{"open": 100, "high": 101, "low": 99}]  # no 'close'
        with pytest.raises(ValueError, match="missing required columns"):
            _candles_to_dataframe(candles)
