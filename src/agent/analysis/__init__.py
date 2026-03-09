"""Analysis engine — price-action indicators and sector grouping."""

from agent.analysis.types import (
    AnalysisResult,
    IndicatorResult,
    PriceActionResult,
    SectorGroupResult,
    SectorResult,
)
from agent.analysis.registry import Indicator, IndicatorRegistry
from agent.analysis.price_action import analyse_price_action
from agent.analysis.sector import analyse_sector

__all__ = [
    # Types
    "AnalysisResult",
    "IndicatorResult",
    "PriceActionResult",
    "SectorGroupResult",
    "SectorResult",
    # Registry
    "Indicator",
    "IndicatorRegistry",
    # Entry points
    "analyse_price_action",
    "analyse_sector",
]
