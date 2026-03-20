"""Tests for the walk-forward backtesting module (backtest.py).

Covers:
- Basic signal generation and forward-return measurement
- Hit-rate calculation for directional signals
- Per-signal-type average returns
- Profit factor computation
- Edge cases: minimum candles, step > 1, all-neutral signals
- Custom registry support
"""

from __future__ import annotations

import math
from typing import Any

import pandas as pd
import pytest

from agent.analysis.backtest import backtest_signals, _compile_result
from agent.analysis.registry import IndicatorRegistry
from agent.analysis.types import BacktestResult, IndicatorResult, SignalEvent


# ---------------------------------------------------------------------------
# Helpers — synthetic candle builders
# ---------------------------------------------------------------------------


def _make_candle(
    close: float,
    day_offset: int = 0,
) -> dict[str, Any]:
    """Build a single candle dict with realistic OHLCV fields."""
    return {
        "open": close - 0.5,
        "high": close + 1.0,
        "low": close - 1.0,
        "close": close,
        "volume": 1000.0,
        "timestamp": f"2024-01-{10 + day_offset:02d}T00:00:00Z",
    }


def _uptrend_candles(n: int = 80) -> list[dict[str, Any]]:
    """Create a clear, sustained uptrend over *n* candles."""
    return [_make_candle(100.0 + i * 2.0, i) for i in range(n)]


def _downtrend_candles(n: int = 80) -> list[dict[str, Any]]:
    """Create a clear, sustained downtrend over *n* candles."""
    return [_make_candle(200.0 - i * 2.0, i) for i in range(n)]


def _flat_candles(n: int = 80) -> list[dict[str, Any]]:
    """Create a flat / sideways market (oscillates ±0.5 around 100)."""
    return [
        _make_candle(100.0 + (0.5 if i % 2 == 0 else -0.5), i)
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# A trivial stub indicator for deterministic testing
# ---------------------------------------------------------------------------


class _AlwaysBullish:
    """Indicator that always returns bullish with strength 0.8."""

    @property
    def name(self) -> str:
        return "stub_bullish"

    def analyse(self, df: pd.DataFrame) -> IndicatorResult:
        return IndicatorResult(
            name=self.name, signal="bullish", strength=0.8,
        )


class _AlwaysBearish:
    """Indicator that always returns bearish with strength 0.7."""

    @property
    def name(self) -> str:
        return "stub_bearish"

    def analyse(self, df: pd.DataFrame) -> IndicatorResult:
        return IndicatorResult(
            name=self.name, signal="bearish", strength=0.7,
        )


class _AlwaysNeutral:
    """Indicator that always returns neutral with strength 0.0."""

    @property
    def name(self) -> str:
        return "stub_neutral"

    def analyse(self, df: pd.DataFrame) -> IndicatorResult:
        return IndicatorResult(
            name=self.name, signal="neutral", strength=0.0,
        )


def _bullish_registry() -> IndicatorRegistry:
    r = IndicatorRegistry()
    r.register(_AlwaysBullish())
    return r


def _bearish_registry() -> IndicatorRegistry:
    r = IndicatorRegistry()
    r.register(_AlwaysBearish())
    return r


def _neutral_registry() -> IndicatorRegistry:
    r = IndicatorRegistry()
    r.register(_AlwaysNeutral())
    return r


# ---------------------------------------------------------------------------
# backtest_signals — basic operation
# ---------------------------------------------------------------------------


class TestBacktestSignals:
    """Core happy-path and basic behaviour tests."""

    def test_returns_backtest_result(self) -> None:
        candles = _uptrend_candles(80)
        result = backtest_signals(
            candles, window=50, forward_period=10, registry=_bullish_registry(),
        )
        assert isinstance(result, BacktestResult)

    def test_total_signals_matches_expected_count(self) -> None:
        # 80 candles, window=50, forward=10, step=1 → indices 50..70 = 21 signals
        candles = _uptrend_candles(80)
        result = backtest_signals(
            candles, window=50, forward_period=10, step=1,
            registry=_bullish_registry(),
        )
        assert result.total_signals == 21
        assert len(result.events) == 21

    def test_step_reduces_signal_count(self) -> None:
        candles = _uptrend_candles(80)
        result = backtest_signals(
            candles, window=50, forward_period=10, step=5,
            registry=_bullish_registry(),
        )
        # indices: 50, 55, 60, 65, 70 → 5 signals
        assert result.total_signals == 5

    def test_signal_events_have_correct_prices(self) -> None:
        candles = _uptrend_candles(80)
        result = backtest_signals(
            candles, window=50, forward_period=10, step=1,
            registry=_bullish_registry(),
        )
        first = result.events[0]
        # Entry: close of candle at index 49 (0-based in the slice)
        # = 100 + 49*2 = 198.0
        assert first.entry_price == pytest.approx(198.0)
        # Exit: close of candle at index 59
        # = 100 + 59*2 = 218.0
        assert first.exit_price == pytest.approx(218.0)

    def test_forward_return_is_correct(self) -> None:
        candles = _uptrend_candles(80)
        result = backtest_signals(
            candles, window=50, forward_period=10, step=1,
            registry=_bullish_registry(),
        )
        first = result.events[0]
        expected_pct = ((218.0 - 198.0) / 198.0) * 100.0
        assert first.forward_return_pct == pytest.approx(expected_pct, abs=0.01)


# ---------------------------------------------------------------------------
# Hit rate and correctness
# ---------------------------------------------------------------------------


class TestHitRate:
    """Verify that hit-rate correctly measures directional accuracy."""

    def test_bullish_signal_on_uptrend_is_correct(self) -> None:
        candles = _uptrend_candles(80)
        result = backtest_signals(
            candles, window=50, forward_period=10,
            registry=_bullish_registry(),
        )
        # All signals should be bullish AND correct (uptrend → positive return)
        assert result.bullish_signals == result.total_signals
        assert result.hit_rate_pct == 100.0

    def test_bearish_signal_on_downtrend_is_correct(self) -> None:
        candles = _downtrend_candles(80)
        result = backtest_signals(
            candles, window=50, forward_period=10,
            registry=_bearish_registry(),
        )
        assert result.bearish_signals == result.total_signals
        assert result.hit_rate_pct == 100.0

    def test_bullish_signal_on_downtrend_is_incorrect(self) -> None:
        candles = _downtrend_candles(80)
        result = backtest_signals(
            candles, window=50, forward_period=10,
            registry=_bullish_registry(),
        )
        assert result.hit_rate_pct == 0.0

    def test_bearish_signal_on_uptrend_is_incorrect(self) -> None:
        candles = _uptrend_candles(80)
        result = backtest_signals(
            candles, window=50, forward_period=10,
            registry=_bearish_registry(),
        )
        assert result.hit_rate_pct == 0.0

    def test_neutral_signals_excluded_from_hit_rate(self) -> None:
        candles = _uptrend_candles(80)
        result = backtest_signals(
            candles, window=50, forward_period=10,
            registry=_neutral_registry(),
        )
        assert result.neutral_signals == result.total_signals
        # No directional signals → hit rate is 0.0 (not undefined)
        assert result.hit_rate_pct == 0.0


# ---------------------------------------------------------------------------
# Average returns by signal type
# ---------------------------------------------------------------------------


class TestAverageReturns:
    """Validate per-signal-type return averages."""

    def test_bullish_avg_return_positive_on_uptrend(self) -> None:
        candles = _uptrend_candles(80)
        result = backtest_signals(
            candles, window=50, forward_period=10,
            registry=_bullish_registry(),
        )
        assert result.avg_bullish_return_pct > 0
        assert result.avg_forward_return_pct > 0

    def test_bearish_avg_return_negative_on_downtrend(self) -> None:
        candles = _downtrend_candles(80)
        result = backtest_signals(
            candles, window=50, forward_period=10,
            registry=_bearish_registry(),
        )
        assert result.avg_bearish_return_pct < 0

    def test_avg_returns_zero_when_no_signals_of_type(self) -> None:
        candles = _uptrend_candles(80)
        result = backtest_signals(
            candles, window=50, forward_period=10,
            registry=_bullish_registry(),
        )
        # No bearish signals → average bearish return defaults to 0.0
        assert result.avg_bearish_return_pct == 0.0


# ---------------------------------------------------------------------------
# Profit factor
# ---------------------------------------------------------------------------


class TestProfitFactor:
    """Verify profit factor computation."""

    def test_all_correct_directional_signals(self) -> None:
        candles = _uptrend_candles(80)
        result = backtest_signals(
            candles, window=50, forward_period=10,
            registry=_bullish_registry(),
        )
        # All correct → losses = 0 → profit factor = inf
        assert result.profit_factor == float("inf")

    def test_all_incorrect_directional_signals(self) -> None:
        candles = _downtrend_candles(80)
        result = backtest_signals(
            candles, window=50, forward_period=10,
            registry=_bullish_registry(),
        )
        # All incorrect → gains = 0 → profit factor = 0.0
        assert result.profit_factor == 0.0

    def test_neutral_only_profit_factor_zero(self) -> None:
        candles = _uptrend_candles(80)
        result = backtest_signals(
            candles, window=50, forward_period=10,
            registry=_neutral_registry(),
        )
        assert result.profit_factor == 0.0


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestBacktestEdgeCases:
    """Boundary conditions and error handling."""

    def test_too_few_candles_raises_value_error(self) -> None:
        candles = _uptrend_candles(30)
        with pytest.raises(ValueError, match="Need at least"):
            backtest_signals(candles, window=50, forward_period=10)

    def test_exact_minimum_candles_produces_one_signal(self) -> None:
        # window=50, forward=10 → need exactly 60 candles → 1 signal
        candles = _uptrend_candles(60)
        result = backtest_signals(
            candles, window=50, forward_period=10,
            registry=_bullish_registry(),
        )
        assert result.total_signals == 1

    def test_smaller_window_produces_more_signals(self) -> None:
        candles = _uptrend_candles(80)
        result_small = backtest_signals(
            candles, window=20, forward_period=10,
            registry=_bullish_registry(),
        )
        result_large = backtest_signals(
            candles, window=50, forward_period=10,
            registry=_bullish_registry(),
        )
        assert result_small.total_signals > result_large.total_signals

    def test_larger_forward_period_reduces_signals(self) -> None:
        candles = _uptrend_candles(80)
        result_short = backtest_signals(
            candles, window=50, forward_period=5,
            registry=_bullish_registry(),
        )
        result_long = backtest_signals(
            candles, window=50, forward_period=20,
            registry=_bullish_registry(),
        )
        assert result_short.total_signals > result_long.total_signals


# ---------------------------------------------------------------------------
# Default registry (uses real indicators)
# ---------------------------------------------------------------------------


class TestBacktestWithDefaultRegistry:
    """Verify backtesting works with the real default indicators."""

    def test_uptrend_with_default_registry(self) -> None:
        candles = _uptrend_candles(100)
        result = backtest_signals(candles, window=50, forward_period=10)

        assert isinstance(result, BacktestResult)
        assert result.total_signals > 0
        # With a strong uptrend, we expect mostly bullish signals
        assert result.bullish_signals >= result.bearish_signals

    def test_downtrend_with_default_registry(self) -> None:
        candles = _downtrend_candles(100)
        result = backtest_signals(candles, window=50, forward_period=10)

        assert result.total_signals > 0
        # With a strong downtrend, we expect mostly bearish signals
        assert result.bearish_signals >= result.bullish_signals


# ---------------------------------------------------------------------------
# _compile_result internal helper
# ---------------------------------------------------------------------------


class TestCompileResult:
    """Direct tests of the _compile_result aggregation helper."""

    def test_empty_events(self) -> None:
        result = _compile_result([])
        assert result.total_signals == 0
        assert result.hit_rate_pct == 0.0
        assert result.profit_factor == 0.0

    def test_single_correct_bullish(self) -> None:
        events = [
            SignalEvent(
                index=50, signal="bullish", strength=0.8,
                entry_price=100.0, exit_price=110.0,
                forward_return_pct=10.0, correct=True,
            ),
        ]
        result = _compile_result(events)
        assert result.total_signals == 1
        assert result.bullish_signals == 1
        assert result.hit_rate_pct == 100.0
        assert result.avg_bullish_return_pct == 10.0
        assert result.profit_factor == float("inf")

    def test_mixed_signals(self) -> None:
        events = [
            SignalEvent(50, "bullish", 0.8, 100.0, 110.0, 10.0, True),
            SignalEvent(51, "bearish", 0.7, 100.0, 95.0, -5.0, True),
            SignalEvent(52, "bullish", 0.6, 100.0, 98.0, -2.0, False),
            SignalEvent(53, "neutral", 0.0, 100.0, 101.0, 1.0, False),
        ]
        result = _compile_result(events)
        assert result.total_signals == 4
        assert result.bullish_signals == 2
        assert result.bearish_signals == 1
        assert result.neutral_signals == 1
        # Directional: 3 signals, 2 correct → 66.67%
        assert result.hit_rate_pct == pytest.approx(66.67, abs=0.01)
        # Gains: |10.0| + |-5.0| = 15.0; Losses: |-2.0| = 2.0
        assert result.profit_factor == pytest.approx(7.5, abs=0.01)
