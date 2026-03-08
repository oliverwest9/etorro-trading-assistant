"""Price-action analysis — the main entry point for per-instrument analysis.

Converts raw candle dicts (as returned by ``db.query_candles``) into a
pandas DataFrame and runs every registered indicator via the
``IndicatorRegistry``, then aggregates the results into a single
``PriceActionResult``.

This module contains **no** API or DB calls — it is a pure function
from data to results.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from agent.analysis.registry import IndicatorRegistry
from agent.analysis.types import IndicatorResult, PriceActionResult


def _candles_to_dataframe(candles: list[dict[str, Any]]) -> pd.DataFrame:
    """Convert a list of candle dicts to a pandas DataFrame.

    Expects each dict to have at least: ``open``, ``high``, ``low``,
    ``close``, ``volume``, ``timestamp``.

    Returns:
        DataFrame with those columns, sorted by ``timestamp`` ascending.
    """
    if not candles:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume", "timestamp"])

    df = pd.DataFrame(candles)

    # Ensure the required columns exist
    required = {"open", "high", "low", "close"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Candle data missing required columns: {missing}")

    # Optional columns with defaults
    if "volume" not in df.columns:
        df["volume"] = 0.0
    if "timestamp" not in df.columns:
        df["timestamp"] = range(len(df))

    df = df.sort_values("timestamp").reset_index(drop=True)
    return df


def analyse_price_action(
    candles: list[dict[str, Any]],
    registry: IndicatorRegistry | None = None,
) -> PriceActionResult:
    """Run all indicators on candle data and aggregate results.

    If no registry is provided, the ``default_registry`` (with trend,
    momentum, and levels indicators) is used.

    Args:
        candles: List of OHLCV candle dicts (from ``query_candles``).
        registry: Optional custom ``IndicatorRegistry``.

    Returns:
        Aggregated ``PriceActionResult``.
    """
    if registry is None:
        from agent.analysis.indicators import default_registry
        registry = default_registry

    if not candles:
        return PriceActionResult(
            trend="neutral",
            trend_strength=0.0,
            support=None,
            resistance=None,
            momentum_signal="neutral",
            indicators=[],
        )

    df = _candles_to_dataframe(candles)
    indicator_results = registry.run_all(df)

    return _aggregate_results(indicator_results)


def _aggregate_results(results: list[IndicatorResult]) -> PriceActionResult:
    """Combine individual indicator results into a cohesive summary.

    - **Trend**: determined by majority vote across indicators.
    - **Strength**: weighted average of all indicator strengths.
    - **Support / resistance**: extracted from the ``levels`` indicator.
    - **Momentum signal**: extracted from the ``momentum`` indicator.
    """
    if not results:
        return PriceActionResult(
            trend="neutral",
            trend_strength=0.0,
            support=None,
            resistance=None,
            momentum_signal="neutral",
            indicators=[],
        )

    # Vote on trend
    votes: dict[str, float] = {"bullish": 0.0, "bearish": 0.0, "neutral": 0.0}
    total_strength = 0.0

    for r in results:
        votes[r.signal] = votes.get(r.signal, 0.0) + 1.0
        total_strength += r.strength

    avg_strength = total_strength / len(results) if results else 0.0

    # Majority signal wins
    trend = max(votes, key=lambda k: votes[k])
    # If tied, fall back to neutral
    max_votes = votes[trend]
    ties = [s for s, v in votes.items() if v == max_votes]
    if len(ties) > 1:
        trend = "neutral"

    # Extract levels-specific data
    support: float | None = None
    resistance: float | None = None
    for r in results:
        if r.name == "levels":
            support = r.details.get("nearest_support")
            resistance = r.details.get("nearest_resistance")
            break

    # Extract momentum signal
    momentum_signal = "neutral"
    for r in results:
        if r.name == "momentum":
            momentum_signal = r.signal
            break

    return PriceActionResult(
        trend=trend,
        trend_strength=round(avg_strength, 4),
        support=support,
        resistance=resistance,
        momentum_signal=momentum_signal,
        indicators=results,
    )
