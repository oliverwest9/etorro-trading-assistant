"""Indicator protocol and registry for the analysis engine.

Indicators implement the ``Indicator`` protocol (a ``name`` property and
an ``analyse`` method) and are collected in an ``IndicatorRegistry``.
The registry is the single entry point the price-action module uses to
evaluate all registered indicators in one call.

Usage::

    from agent.analysis.registry import IndicatorRegistry

    registry = IndicatorRegistry()
    registry.register(TrendIndicator())
    results = registry.run_all(candle_dataframe)
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

import pandas as pd

from agent.analysis.types import IndicatorResult


@runtime_checkable
class Indicator(Protocol):
    """Protocol that every indicator must satisfy.

    Implementing classes must expose:

    * ``name`` — a read-only string property (used as the registry key).
    * ``analyse(df)`` — accepts a pandas DataFrame of OHLCV candles
      (columns: ``open``, ``high``, ``low``, ``close``, ``volume``,
      ``timestamp``) sorted by ``timestamp`` ascending, and returns
      an ``IndicatorResult``.
    """

    @property
    def name(self) -> str: ...  # pragma: no cover

    def analyse(self, df: pd.DataFrame) -> IndicatorResult: ...  # pragma: no cover


class IndicatorRegistry:
    """Collects indicators and runs them against candle DataFrames.

    Attributes:
        _indicators: Internal mapping from indicator name → instance.
    """

    def __init__(self) -> None:
        self._indicators: dict[str, Indicator] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, indicator: Indicator) -> None:
        """Add an indicator to the registry.

        Args:
            indicator: Any object satisfying the ``Indicator`` protocol.

        Raises:
            TypeError: If *indicator* does not satisfy the protocol.
            ValueError: If an indicator with the same name is already
                registered.
        """
        if not isinstance(indicator, Indicator):
            raise TypeError(
                f"Expected an Indicator, got {type(indicator).__name__}"
            )
        if indicator.name in self._indicators:
            raise ValueError(
                f"Indicator '{indicator.name}' is already registered"
            )
        self._indicators[indicator.name] = indicator

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def run_all(self, df: pd.DataFrame) -> list[IndicatorResult]:
        """Run every registered indicator and return their results.

        Indicators are executed in registration order.  If a single
        indicator raises, it is skipped and a neutral fallback result
        is produced so that one broken indicator cannot crash the
        whole pipeline.

        Args:
            df: OHLCV candle DataFrame (sorted by timestamp ascending).

        Returns:
            List of ``IndicatorResult`` instances (one per indicator).
        """
        results: list[IndicatorResult] = []
        for name, indicator in self._indicators.items():
            try:
                results.append(indicator.analyse(df))
            except Exception:
                # Graceful degradation: emit a neutral placeholder
                results.append(
                    IndicatorResult(
                        name=name,
                        signal="neutral",
                        strength=0.0,
                        details={"error": "indicator raised an exception"},
                    )
                )
        return results

    def run(self, name: str, df: pd.DataFrame) -> IndicatorResult:
        """Run a single indicator by name.

        Args:
            name: The indicator's registered name.
            df: OHLCV candle DataFrame.

        Returns:
            The indicator's ``IndicatorResult``.

        Raises:
            KeyError: If no indicator is registered under *name*.
        """
        if name not in self._indicators:
            raise KeyError(f"No indicator registered with name '{name}'")
        return self._indicators[name].analyse(df)

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def names(self) -> list[str]:
        """Return the names of all registered indicators (in order)."""
        return list(self._indicators.keys())

    def __len__(self) -> int:
        return len(self._indicators)

    def __contains__(self, name: str) -> bool:
        return name in self._indicators

    def __iter__(self) -> Any:
        return iter(self._indicators.values())
