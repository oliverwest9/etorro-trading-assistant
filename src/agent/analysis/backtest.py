"""Walk-forward backtesting for analysis signal quality measurement.

Slides through historical candle data with a fixed look-back window,
generates a signal at each step using ``analyse_price_action``, then
measures whether the predicted direction matched the actual forward
return.  The output is a ``BacktestResult`` containing hit-rate,
average returns by signal type, profit factor, and the full list of
individual signal events.

This module contains **no** API or DB calls — it is a pure function
from data to results.
"""

from __future__ import annotations

from typing import Any

from agent.analysis.price_action import analyse_price_action
from agent.analysis.registry import IndicatorRegistry
from agent.analysis.types import BacktestResult, SignalEvent


def backtest_signals(
    candles: list[dict[str, Any]],
    *,
    window: int = 50,
    forward_period: int = 10,
    step: int = 1,
    registry: IndicatorRegistry | None = None,
) -> BacktestResult:
    """Run a walk-forward backtest of the analysis engine's signals.

    Starting from index ``window``, the function looks back ``window``
    candles, runs ``analyse_price_action`` on that slice, records the
    signal, then checks the actual return over the next
    ``forward_period`` candles.

    Args:
        candles: Full OHLCV candle history sorted by timestamp ascending.
            Each dict must have at least ``open``, ``high``, ``low``,
            ``close`` keys.
        window: Number of historical candles fed to the analysis engine
            at each step (default 50).
        forward_period: Number of candles into the future to measure the
            actual return (default 10).
        step: How many candles to advance between signal evaluations
            (default 1).  Increase to reduce computation on large
            datasets.
        registry: Optional custom ``IndicatorRegistry``.  If ``None``,
            the default registry (trend, momentum, levels) is used.

    Returns:
        ``BacktestResult`` with aggregate metrics and per-event details.

    Raises:
        ValueError: If *candles* is too short to produce at least one
            signal (needs ``window + forward_period`` candles minimum).
    """
    min_required = window + forward_period
    if len(candles) < min_required:
        raise ValueError(
            f"Need at least {min_required} candles "
            f"(window={window} + forward_period={forward_period}), "
            f"got {len(candles)}"
        )

    events: list[SignalEvent] = []

    # Walk through the data: at each step, analyse the last `window`
    # candles and check the forward return.
    i = window
    while i + forward_period <= len(candles):
        analysis_slice = candles[i - window: i]
        entry_price = float(analysis_slice[-1]["close"])
        exit_price = float(candles[i + forward_period - 1]["close"])

        result = analyse_price_action(analysis_slice, registry=registry)

        if entry_price > 0:
            forward_return_pct = ((exit_price - entry_price) / entry_price) * 100.0
        else:
            forward_return_pct = 0.0

        # Determine correctness: bullish → positive return, bearish → negative
        if result.trend == "bullish":
            correct = forward_return_pct > 0
        elif result.trend == "bearish":
            correct = forward_return_pct < 0
        else:
            # Neutral signals are neither correct nor incorrect
            correct = False

        events.append(
            SignalEvent(
                index=i,
                signal=result.trend,
                strength=result.trend_strength,
                entry_price=entry_price,
                exit_price=exit_price,
                forward_return_pct=round(forward_return_pct, 4),
                correct=correct,
            )
        )

        i += step

    return _compile_result(events)


def _compile_result(events: list[SignalEvent]) -> BacktestResult:
    """Aggregate individual signal events into summary metrics."""
    if not events:
        return BacktestResult(
            total_signals=0,
            bullish_signals=0,
            bearish_signals=0,
            neutral_signals=0,
            hit_rate_pct=0.0,
            avg_forward_return_pct=0.0,
            avg_bullish_return_pct=0.0,
            avg_bearish_return_pct=0.0,
            profit_factor=0.0,
            events=[],
        )

    bullish = [e for e in events if e.signal == "bullish"]
    bearish = [e for e in events if e.signal == "bearish"]
    neutral = [e for e in events if e.signal == "neutral"]

    directional = [e for e in events if e.signal in ("bullish", "bearish")]
    hit_count = sum(1 for e in directional if e.correct)
    hit_rate = (hit_count / len(directional) * 100.0) if directional else 0.0

    avg_all = (
        sum(e.forward_return_pct for e in events) / len(events)
    )
    avg_bull = (
        sum(e.forward_return_pct for e in bullish) / len(bullish)
        if bullish else 0.0
    )
    avg_bear = (
        sum(e.forward_return_pct for e in bearish) / len(bearish)
        if bearish else 0.0
    )

    # Profit factor: gross gains from correct directional signals /
    # gross losses from incorrect directional signals.
    gains = sum(
        abs(e.forward_return_pct)
        for e in directional
        if e.correct and e.forward_return_pct != 0.0
    )
    losses = sum(
        abs(e.forward_return_pct)
        for e in directional
        if not e.correct and e.forward_return_pct != 0.0
    )
    profit_factor = gains / losses if losses > 0 else (float("inf") if gains > 0 else 0.0)

    return BacktestResult(
        total_signals=len(events),
        bullish_signals=len(bullish),
        bearish_signals=len(bearish),
        neutral_signals=len(neutral),
        hit_rate_pct=round(hit_rate, 2),
        avg_forward_return_pct=round(avg_all, 4),
        avg_bullish_return_pct=round(avg_bull, 4),
        avg_bearish_return_pct=round(avg_bear, 4),
        profit_factor=round(profit_factor, 4) if profit_factor != float("inf") else float("inf"),
        events=events,
    )
