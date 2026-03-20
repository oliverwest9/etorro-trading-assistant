"""Analysis engine — price-action indicators, sector grouping, risk analysis, and backtesting."""

from agent.analysis.types import (
    AnalysisResult,
    BacktestResult,
    CriticResult,
    DiversificationAssessment,
    IndicatorResult,
    InstrumentRiskMetrics,
    PortfolioRiskSummary,
    PriceActionResult,
    SectorGroupResult,
    SectorResult,
    SignalEvent,
)
from agent.analysis.registry import Indicator, IndicatorRegistry
from agent.analysis.price_action import analyse_price_action
from agent.analysis.sector import analyse_sector
from agent.analysis.critic import analyse_risk
from agent.analysis.backtest import backtest_signals

__all__ = [
    # Types
    "AnalysisResult",
    "BacktestResult",
    "CriticResult",
    "DiversificationAssessment",
    "IndicatorResult",
    "InstrumentRiskMetrics",
    "PortfolioRiskSummary",
    "PriceActionResult",
    "SectorGroupResult",
    "SectorResult",
    "SignalEvent",
    # Registry
    "Indicator",
    "IndicatorRegistry",
    # Entry points
    "analyse_price_action",
    "analyse_risk",
    "analyse_sector",
    "backtest_signals",
]
