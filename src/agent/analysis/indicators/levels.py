"""Levels indicator — swing high / low support and resistance detection.

Identifies the most recent local maxima (resistance) and local minima
(support) from the candle data.  A point is considered a swing high if
it is higher than the ``order`` candles on either side; likewise for
swing lows.

The default order of 5 and max_levels of 5 are tuned for identifying
major support/resistance levels over months of daily data, rather than
short-term intraday pivots.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from agent.analysis.types import IndicatorResult


class LevelsIndicator:
    """Detects key support and resistance price levels.

    Args:
        order: Number of candles on each side required to confirm a
            swing point (default 5).  A higher order filters out minor
            fluctuations, surfacing only significant long-term levels.
        max_levels: Maximum number of levels to return on each side
            (default 5).
    """

    def __init__(self, order: int = 5, max_levels: int = 5) -> None:
        self._order = order
        self._max_levels = max_levels

    @property
    def name(self) -> str:
        return "levels"

    def analyse(self, df: pd.DataFrame) -> IndicatorResult:
        """Find swing-high resistance and swing-low support levels.

        Args:
            df: DataFrame with ``high`` and ``low`` columns, sorted
                by ``timestamp`` ascending.

        Returns:
            ``IndicatorResult`` with support/resistance price lists.
        """
        min_candles = 2 * self._order + 1
        if len(df) < min_candles:
            return IndicatorResult(
                name=self.name,
                signal="neutral",
                strength=0.0,
                details={
                    "support_levels": [],
                    "resistance_levels": [],
                    "reason": "insufficient data",
                },
            )

        highs = df["high"].values
        lows = df["low"].values
        last_close = float(df["close"].values[-1])

        resistance_levels: list[float] = []
        support_levels: list[float] = []

        for i in range(self._order, len(highs) - self._order):
            # Swing high: higher than `order` candles on each side
            is_swing_high = all(
                highs[i] >= highs[i - j] and highs[i] >= highs[i + j]
                for j in range(1, self._order + 1)
            )
            if is_swing_high:
                resistance_levels.append(float(highs[i]))

            # Swing low: lower than `order` candles on each side
            is_swing_low = all(
                lows[i] <= lows[i - j] and lows[i] <= lows[i + j]
                for j in range(1, self._order + 1)
            )
            if is_swing_low:
                support_levels.append(float(lows[i]))

        # Keep only the most recent levels
        resistance_levels = resistance_levels[-self._max_levels:]
        support_levels = support_levels[-self._max_levels:]

        # Nearest support below current price
        supports_below = [s for s in support_levels if s < last_close]
        nearest_support = max(supports_below) if supports_below else None

        # Nearest resistance above current price
        resistances_above = [r for r in resistance_levels if r > last_close]
        nearest_resistance = min(resistances_above) if resistances_above else None

        details: dict[str, Any] = {
            "support_levels": support_levels,
            "resistance_levels": resistance_levels,
            "nearest_support": nearest_support,
            "nearest_resistance": nearest_resistance,
            "last_close": last_close,
            "order": self._order,
        }

        # Signal: if price is closer to support → bullish bias, else bearish
        if nearest_support is not None and nearest_resistance is not None:
            range_size = nearest_resistance - nearest_support
            if range_size > 0:
                position = (last_close - nearest_support) / range_size
                if position < 0.33:
                    signal = "bullish"   # near support
                    strength = 1.0 - position
                elif position > 0.67:
                    signal = "bearish"   # near resistance
                    strength = position
                else:
                    signal = "neutral"
                    strength = 0.0
            else:
                signal = "neutral"
                strength = 0.0
        else:
            signal = "neutral"
            strength = 0.0

        return IndicatorResult(
            name=self.name,
            signal=signal,
            strength=round(strength, 4),
            details=details,
        )
