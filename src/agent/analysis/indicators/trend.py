"""Trend indicator — higher-highs / higher-lows pattern detection.

Analyses the structure of recent candle highs and lows to determine
whether the instrument is in an uptrend, downtrend, or neutral range.

The algorithm compares a ``window`` of recent highs and lows to
detect sequences of higher-highs + higher-lows (bullish) or
lower-highs + lower-lows (bearish).

The default window of 50 daily candles (~2–3 months) is tuned for
long-term trend detection rather than short-term noise.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from agent.analysis.types import IndicatorResult


class TrendIndicator:
    """Detects trend direction via higher-highs / lower-lows patterns.

    Args:
        window: Number of candles to examine for the pattern (default 50).
            The default covers roughly 2–3 months of daily candles,
            suitable for identifying sustained long-term trends.
    """

    def __init__(self, window: int = 50) -> None:
        self._window = window

    @property
    def name(self) -> str:
        return "trend"

    def analyse(self, df: pd.DataFrame) -> IndicatorResult:
        """Evaluate trend from OHLCV candle DataFrame.

        Args:
            df: DataFrame with ``high``, ``low``, ``close`` columns,
                sorted by ``timestamp`` ascending.

        Returns:
            ``IndicatorResult`` with signal and details about the
            higher-high / lower-low counts.
        """
        if len(df) < 3:
            return IndicatorResult(
                name=self.name,
                signal="neutral",
                strength=0.0,
                details={"reason": "insufficient data"},
            )

        # Take the last `window` candles (or all if fewer)
        tail = df.tail(self._window)
        highs = tail["high"].values
        lows = tail["low"].values

        higher_highs = 0
        lower_highs = 0
        higher_lows = 0
        lower_lows = 0
        comparisons = len(highs) - 1

        for i in range(1, len(highs)):
            if highs[i] > highs[i - 1]:
                higher_highs += 1
            elif highs[i] < highs[i - 1]:
                lower_highs += 1

            if lows[i] > lows[i - 1]:
                higher_lows += 1
            elif lows[i] < lows[i - 1]:
                lower_lows += 1

        details: dict[str, Any] = {
            "window": len(tail),
            "higher_highs": higher_highs,
            "lower_highs": lower_highs,
            "higher_lows": higher_lows,
            "lower_lows": lower_lows,
        }

        # Bullish: majority of comparisons show HH + HL
        bullish_score = (higher_highs + higher_lows) / (2 * comparisons)
        bearish_score = (lower_highs + lower_lows) / (2 * comparisons)

        if bullish_score > 0.5:
            signal = "bullish"
            strength = min(bullish_score, 1.0)
        elif bearish_score > 0.5:
            signal = "bearish"
            strength = min(bearish_score, 1.0)
        else:
            signal = "neutral"
            strength = 0.0

        return IndicatorResult(
            name=self.name,
            signal=signal,
            strength=round(strength, 4),
            details=details,
        )
