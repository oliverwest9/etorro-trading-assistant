"""Built-in indicators — auto-registered on import.

Importing this package registers the three default indicators
(trend, momentum, levels) into a module-level ``default_registry``.
"""

from __future__ import annotations

from agent.analysis.registry import IndicatorRegistry

from agent.analysis.indicators.trend import TrendIndicator
from agent.analysis.indicators.momentum import MomentumIndicator
from agent.analysis.indicators.levels import LevelsIndicator

default_registry = IndicatorRegistry()
default_registry.register(TrendIndicator())
default_registry.register(MomentumIndicator())
default_registry.register(LevelsIndicator())

__all__ = [
    "default_registry",
    "TrendIndicator",
    "MomentumIndicator",
    "LevelsIndicator",
]
