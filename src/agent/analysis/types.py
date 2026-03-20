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
    risk_metrics: "InstrumentRiskMetrics | None" = None

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

        risk_obj: dict[str, Any] | None = None
        if self.risk_metrics is not None:
            rm = self.risk_metrics
            risk_obj = {
                "annualised_volatility": rm.annualised_volatility,
                "max_drawdown_pct": rm.max_drawdown_pct,
                "simple_return_pct": rm.simple_return_pct,
                "risk_adjusted_return": rm.risk_adjusted_return,
            }

        raw_data: dict[str, Any] = {
            "price_action": price_action_obj,
            "sector_context": sector_obj,
            "risk_metrics": risk_obj,
        }

        return {
            "trend": pa.trend,
            "trend_strength": pa.trend_strength,
            "price_action": price_action_obj,
            "sector_context": sector_obj,
            "risk_metrics": risk_obj,
            "raw_data": raw_data,
        }


# =====================================================================
# Risk / critic result types
# =====================================================================


@dataclass(frozen=True)
class InstrumentRiskMetrics:
    """Risk metrics for a single instrument.

    Attributes:
        annualised_volatility: Annualised daily-return volatility (%).
        max_drawdown_pct: Maximum peak-to-trough drawdown (%).
        simple_return_pct: Simple return over the candle window (%).
        risk_adjusted_return: Annualised return / annualised volatility
            (Sharpe-like ratio, without risk-free rate adjustment).
    """

    annualised_volatility: float
    max_drawdown_pct: float
    simple_return_pct: float
    risk_adjusted_return: float


@dataclass(frozen=True)
class DiversificationAssessment:
    """Portfolio diversification metrics.

    Attributes:
        hhi: Herfindahl-Hirschman Index (0–10 000).
        concentration_rating: ``"well-diversified"``, ``"moderate"``,
            or ``"concentrated"``.
        top_position_weight_pct: Weight of the largest position (%).
        overweight_positions: Instrument IDs exceeding the 15 % threshold.
    """

    hhi: float
    concentration_rating: str
    top_position_weight_pct: float
    overweight_positions: list[int] = field(default_factory=list)


@dataclass(frozen=True)
class PortfolioRiskSummary:
    """Portfolio-level risk summary with inflation comparison.

    Attributes:
        weighted_return_pct: Position-weighted average return (%).
        inflation_rate_pct: Benchmark inflation rate (%).
        beats_inflation: Whether the portfolio return exceeds inflation.
        inflation_delta_pct: Return minus inflation (%).
        cash_allocation_pct: Cash as a percentage of total portfolio value.
    """

    weighted_return_pct: float
    inflation_rate_pct: float
    beats_inflation: bool
    inflation_delta_pct: float
    cash_allocation_pct: float


@dataclass(frozen=True)
class CriticResult:
    """Complete financial-analyst risk assessment.

    Attributes:
        instrument_risks: Per-instrument risk metrics keyed by eToro ID.
        diversification: Portfolio diversification assessment.
        portfolio_summary: Overall portfolio risk summary.
    """

    instrument_risks: dict[int, InstrumentRiskMetrics] = field(
        default_factory=dict
    )
    diversification: DiversificationAssessment | None = None
    portfolio_summary: PortfolioRiskSummary | None = None


# =====================================================================
# Backtesting result types
# =====================================================================


@dataclass(frozen=True)
class SignalEvent:
    """A single signal generated during a backtest walk-forward evaluation.

    Attributes:
        index: Position in the candle series where the signal was generated.
        signal: Direction — ``"bullish"``, ``"bearish"``, or ``"neutral"``.
        strength: Signal strength (0.0–1.0).
        entry_price: Close price at the time the signal was generated.
        exit_price: Close price ``forward_period`` candles later.
        forward_return_pct: Percentage return over the forward period.
        correct: Whether the signal direction matched the actual return
            (bullish → positive return, bearish → negative return).
    """

    index: int
    signal: str
    strength: float
    entry_price: float
    exit_price: float
    forward_return_pct: float
    correct: bool


@dataclass(frozen=True)
class BacktestResult:
    """Aggregate backtesting metrics from a walk-forward signal evaluation.

    Attributes:
        total_signals: Total number of signals generated.
        bullish_signals: Count of bullish signals.
        bearish_signals: Count of bearish signals.
        neutral_signals: Count of neutral signals.
        hit_rate_pct: Percentage of directional (non-neutral) signals
            where the predicted direction matched actual price movement.
        avg_forward_return_pct: Average forward return across all signals.
        avg_bullish_return_pct: Average forward return for bullish signals.
        avg_bearish_return_pct: Average forward return for bearish signals.
        profit_factor: Gross bullish-correct gains / gross bearish-incorrect
            losses (> 1.0 means the signals are net-profitable).
        events: Full list of individual signal events.
    """

    total_signals: int
    bullish_signals: int
    bearish_signals: int
    neutral_signals: int
    hit_rate_pct: float
    avg_forward_return_pct: float
    avg_bullish_return_pct: float
    avg_bearish_return_pct: float
    profit_factor: float
    events: list[SignalEvent] = field(default_factory=list)
