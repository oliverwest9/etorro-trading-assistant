"""Analysis engine — price-action indicators, sector grouping, and portfolio critique."""

from agent.analysis.types import (
    AnalysisResult,
    CritiqueResult,
    DiversificationAssessment,
    IndicatorResult,
    PriceActionResult,
    RiskMetrics,
    SectorGroupResult,
    SectorResult,
)
from agent.analysis.registry import Indicator, IndicatorRegistry
from agent.analysis.critic import (
    assess_diversification,
    compute_risk_metrics,
    critique_portfolio,
)
from agent.analysis.price_action import analyse_price_action
from agent.analysis.sector import analyse_sector

__all__ = [
    # Types
    "AnalysisResult",
    "CritiqueResult",
    "DiversificationAssessment",
    "IndicatorResult",
    "PriceActionResult",
    "RiskMetrics",
    "SectorGroupResult",
    "SectorResult",
    # Registry
    "Indicator",
    "IndicatorRegistry",
    # Entry points
    "analyse_price_action",
    "analyse_sector",
    "compute_risk_metrics",
    "assess_diversification",
    "critique_portfolio",
]
