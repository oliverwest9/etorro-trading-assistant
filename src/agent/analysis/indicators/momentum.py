"""Momentum indicator — rate of change (ROC) over a configurable window.

Measures how fast the price is moving by computing the percentage
change of the close price over ``window`` periods.

A strong positive ROC signals bullish momentum; a strong negative
ROC signals bearish momentum.  The ``threshold`` parameter controls
where "neutral" begins.

The default window of 50 daily candles (~2–3 months) and threshold
of 5 % are tuned for long-term momentum assessment, filtering out
short-term noise.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from agent.analysis.types import IndicatorResult


class MomentumIndicator:
    """Rate-of-change momentum indicator.

    Args:
        window: Look-back periods for ROC calculation (default 50).
            Covers roughly 2–3 months of daily candles for long-term
            momentum assessment.
        threshold: Minimum absolute ROC (%) to count as non-neutral
            (default 5.0, i.e. ±5 %).  The wider neutral zone avoids
            triggering on normal short-term fluctuations.
    """

    def __init__(self, window: int = 50, threshold: float = 5.0) -> None:
        self._window = window
        self._threshold = threshold

    @property
    def name(self) -> str:
        return "momentum"

    def analyse(self, df: pd.DataFrame) -> IndicatorResult:
        """Compute rate-of-change momentum.

        Args:
            df: DataFrame with a ``close`` column, sorted by
                ``timestamp`` ascending.

        Returns:
            ``IndicatorResult`` with ROC details.
        """
        if len(df) < 2:
            return IndicatorResult(
                name=self.name,
                signal="neutral",
                strength=0.0,
                details={"reason": "insufficient data"},
            )

        closes = df["close"].values
        # Use min(window, available) look-back
        lookback = min(self._window, len(closes) - 1)
        old_close = float(closes[-(lookback + 1)])
        new_close = float(closes[-1])

        if old_close == 0:
            roc = 0.0
        else:
            roc = ((new_close - old_close) / old_close) * 100.0

        details: dict[str, Any] = {
            "roc_pct": round(roc, 4),
            "window": lookback,
            "old_close": old_close,
            "new_close": new_close,
            "threshold": self._threshold,
        }

        if roc > self._threshold:
            signal = "bullish"
            # Strength: scale ROC into [0, 1], capped at 1.0
            strength = min(roc / (self._threshold * 5), 1.0)
        elif roc < -self._threshold:
            signal = "bearish"
            strength = min(abs(roc) / (self._threshold * 5), 1.0)
        else:
            signal = "neutral"
            strength = 0.0

        return IndicatorResult(
            name=self.name,
            signal=signal,
            strength=round(strength, 4),
            details=details,
        )
