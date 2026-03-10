"""Analysis engine — price-action indicators, sector grouping, and risk analysis."""

from agent.analysis.types import (
    AnalysisResult,
    CriticResult,
    DiversificationAssessment,
    IndicatorResult,
    InstrumentRiskMetrics,
    PortfolioRiskSummary,
    PriceActionResult,
    SectorGroupResult,
    SectorResult,
)
from agent.analysis.registry import Indicator, IndicatorRegistry
from agent.analysis.price_action import analyse_price_action
from agent.analysis.sector import analyse_sector
from agent.analysis.critic import analyse_risk

__all__ = [
    # Types
    "AnalysisResult",
    "CriticResult",
    "DiversificationAssessment",
    "IndicatorResult",
    "InstrumentRiskMetrics",
    "PortfolioRiskSummary",
    "PriceActionResult",
    "SectorGroupResult",
    "SectorResult",
    # Registry
    "Indicator",
    "IndicatorRegistry",
    # Entry points
    "analyse_price_action",
    "analyse_risk",
    "analyse_sector",
]
