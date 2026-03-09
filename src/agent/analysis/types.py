"""Typed result dataclasses for the analysis engine.

All analysis functions return these well-defined types rather than
raw dicts.  This gives callers auto-complete, type checking, and a
stable contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class IndicatorResult:
    """Result from a single indicator run.

    Attributes:
        name: Indicator identifier (e.g. ``"trend"``, ``"momentum"``).
        signal: ``"bullish"``, ``"bearish"``, or ``"neutral"``.
        strength: Confidence in the signal, 0.0 (none) to 1.0 (max).
        details: Arbitrary indicator-specific data (key levels, periods, etc.).
    """

    name: str
    signal: str  # "bullish" | "bearish" | "neutral"
    strength: float  # 0.0 – 1.0
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PriceActionResult:
    """Aggregated price-action analysis for one instrument.

    Combines the outputs of every registered indicator into a single
    summary plus per-indicator breakdown.

    Attributes:
        trend: Overall trend direction (``"bullish"`` / ``"bearish"`` / ``"neutral"``).
        trend_strength: Weighted average strength across indicators (0.0–1.0).
        support: Nearest support price level (or ``None``).
        resistance: Nearest resistance price level (or ``None``).
        momentum_signal: Momentum-specific signal string.
        indicators: Full per-indicator results.
    """

    trend: str
    trend_strength: float
    support: float | None
    resistance: float | None
    momentum_signal: str
    indicators: list[IndicatorResult] = field(default_factory=list)


@dataclass(frozen=True)
class SectorGroupResult:
    """Performance summary for a single market/exchange group.

    Attributes:
        group_name: Human-readable group label (e.g. ``"US"``, ``"Crypto"``).
        instrument_count: Number of instruments in this group.
        avg_return_pct: Average simple return across group instruments.
        instruments: List of ``(etoro_id, symbol, return_pct)`` tuples.
    """

    group_name: str
    instrument_count: int
    avg_return_pct: float
    instruments: list[tuple[int, str, float]] = field(default_factory=list)


@dataclass(frozen=True)
class SectorResult:
    """Full sector analysis across all exchange groups.

    Attributes:
        groups: Individual group summaries keyed by group name.
        best_group: Name of the top-performing group (or ``None``).
        worst_group: Name of the worst-performing group (or ``None``).
    """

    groups: dict[str, SectorGroupResult] = field(default_factory=dict)
    best_group: str | None = None
    worst_group: str | None = None


@dataclass(frozen=True)
class AnalysisResult:
    """Complete analysis for one instrument, ready for DB persistence.

    Combines price action and optional sector context into a single
    record aligned with the SurrealDB ``analysis`` table schema.

    Attributes:
        instrument_etoro_id: The eToro instrument ID.
        price_action: Per-instrument price analysis.
        sector_context: The sector group this instrument belongs to (optional).
    """

    instrument_etoro_id: int
    price_action: PriceActionResult
    sector_context: SectorGroupResult | None = None

    def to_db_fields(self) -> dict[str, Any]:
        """Serialise to a dict matching the ``analysis`` table fields.

        The returned dict has keys: ``trend``, ``trend_strength``,
        ``price_action`` (object), ``sector_context`` (object | None),
        ``raw_data`` (object).
        """
        pa = self.price_action

        price_action_obj: dict[str, Any] = {
            "support": pa.support,
            "resistance": pa.resistance,
            "momentum_signal": pa.momentum_signal,
            "indicators": [
                {
                    "name": ind.name,
                    "signal": ind.signal,
                    "strength": ind.strength,
                    "details": ind.details,
                }
                for ind in pa.indicators
            ],
        }

        sector_obj: dict[str, Any] | None = None
        if self.sector_context is not None:
            sc = self.sector_context
            sector_obj = {
                "group_name": sc.group_name,
                "instrument_count": sc.instrument_count,
                "avg_return_pct": sc.avg_return_pct,
            }

        raw_data: dict[str, Any] = {
            "price_action": price_action_obj,
            "sector_context": sector_obj,
        }

        return {
            "trend": pa.trend,
            "trend_strength": pa.trend_strength,
            "price_action": price_action_obj,
            "sector_context": sector_obj,
            "raw_data": raw_data,
        }


# =====================================================================
# Financial analyst critic types
# =====================================================================


@dataclass(frozen=True)
class RiskMetrics:
    """Per-instrument risk metrics computed from historical candle data.

    Attributes:
        etoro_id: The eToro instrument ID.
        symbol: Instrument ticker symbol.
        daily_volatility_pct: Annualised volatility as a percentage
            (standard deviation of daily returns × √252).
        max_drawdown_pct: Maximum peak-to-trough decline as a
            percentage over the analysis window.
        simple_return_pct: Simple return from first to last close.
        risk_adjusted_return: Return divided by volatility (Sharpe-like
            ratio without the risk-free rate).
        data_points: Number of candles used for the calculation.
    """

    etoro_id: int
    symbol: str
    daily_volatility_pct: float
    max_drawdown_pct: float
    simple_return_pct: float
    risk_adjusted_return: float
    data_points: int


@dataclass(frozen=True)
class DiversificationAssessment:
    """Portfolio-level diversification evaluation.

    Attributes:
        herfindahl_index: Sum of squared position weight fractions
            (1/n for perfect diversification, 1.0 for single-stock).
        top_position_pct: Weight of the largest position as a percentage.
        top_position_symbol: Symbol of the largest position.
        sector_count: Number of distinct sector/exchange groups held.
        sector_weights: Mapping of sector name → aggregate weight percentage.
        rating: ``"well_diversified"``, ``"moderate"``, or ``"concentrated"``.
    """

    herfindahl_index: float
    top_position_pct: float
    top_position_symbol: str
    sector_count: int
    sector_weights: dict[str, float] = field(default_factory=dict)
    rating: str = "moderate"


@dataclass(frozen=True)
class CritiqueResult:
    """Complete portfolio critique from the financial analyst agent.

    Produced by ``critique_portfolio()`` — a pure function that
    examines risk, diversification, and inflation-beating potential.

    Attributes:
        portfolio_return_pct: Weighted portfolio return over the window.
        inflation_target_pct: Annualised inflation benchmark used.
        beats_inflation: Whether the portfolio return exceeds inflation.
        return_vs_inflation_pct: Excess return over inflation.
        portfolio_volatility_pct: Weighted annualised portfolio volatility.
        portfolio_sharpe: Portfolio-level Sharpe-like ratio.
        diversification: Diversification assessment.
        risk_metrics: Per-instrument risk metrics.
        cash_allocation_pct: Percentage of portfolio held as cash.
        suggestions: Actionable suggestions for improvement.
    """

    portfolio_return_pct: float
    inflation_target_pct: float
    beats_inflation: bool
    return_vs_inflation_pct: float
    portfolio_volatility_pct: float
    portfolio_sharpe: float
    diversification: DiversificationAssessment
    risk_metrics: list[RiskMetrics] = field(default_factory=list)
    cash_allocation_pct: float = 0.0
    suggestions: list[str] = field(default_factory=list)
