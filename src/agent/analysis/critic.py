"""Portfolio risk analysis — the financial analyst's toolkit.

Pure-function module that computes per-instrument risk metrics
(annualised volatility, maximum drawdown, risk-adjusted return),
portfolio-level diversification metrics (Herfindahl-Hirschman Index,
concentration rating, top-position weight), and an inflation-adjusted
return comparison.

This module contains **no** API or DB calls — every function takes
data in, returns results out.
"""

from __future__ import annotations

import math
from typing import Any

from agent.analysis.types import (
    CriticResult,
    DiversificationAssessment,
    InstrumentRiskMetrics,
    PortfolioRiskSummary,
)

# UK CPI approximation used as the inflation benchmark (annualised %).
_DEFAULT_INFLATION_RATE_PCT = 3.5

# Thresholds for position concentration warnings.
_CONCENTRATION_WARN_PCT = 15.0

# Trading days in a year (used to annualise daily returns).
_TRADING_DAYS_PER_YEAR = 252


# =====================================================================
# Per-instrument risk metrics
# =====================================================================


def compute_instrument_risk(
    candles: list[dict[str, Any]],
) -> InstrumentRiskMetrics:
    """Compute risk metrics for a single instrument from its candle history.

    Args:
        candles: OHLCV dicts sorted by timestamp ascending.  Each dict
            must have at least a ``close`` key.

    Returns:
        ``InstrumentRiskMetrics`` with volatility, max-drawdown, simple
        return, and risk-adjusted return.
    """
    closes = [float(c["close"]) for c in candles if "close" in c]

    if len(closes) < 2:
        return InstrumentRiskMetrics(
            annualised_volatility=0.0,
            max_drawdown_pct=0.0,
            simple_return_pct=0.0,
            risk_adjusted_return=0.0,
        )

    # Daily log-returns
    daily_returns: list[float] = []
    for i in range(1, len(closes)):
        if closes[i - 1] > 0:
            daily_returns.append(math.log(closes[i] / closes[i - 1]))

    if not daily_returns:
        return InstrumentRiskMetrics(
            annualised_volatility=0.0,
            max_drawdown_pct=0.0,
            simple_return_pct=0.0,
            risk_adjusted_return=0.0,
        )

    # Annualised volatility
    mean_ret = sum(daily_returns) / len(daily_returns)
    variance = sum((r - mean_ret) ** 2 for r in daily_returns) / len(daily_returns)
    daily_vol = math.sqrt(variance)
    ann_vol = daily_vol * math.sqrt(_TRADING_DAYS_PER_YEAR)

    # Maximum drawdown
    peak = closes[0]
    max_dd = 0.0
    for price in closes[1:]:
        if price > peak:
            peak = price
        dd = (peak - price) / peak * 100.0 if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd

    # Simple return
    simple_return = ((closes[-1] - closes[0]) / closes[0]) * 100.0 if closes[0] > 0 else 0.0

    # Risk-adjusted return (Sharpe-like: annualised return / annualised vol)
    ann_return = mean_ret * _TRADING_DAYS_PER_YEAR
    risk_adj = (ann_return / ann_vol) if ann_vol > 0 else 0.0

    return InstrumentRiskMetrics(
        annualised_volatility=round(ann_vol * 100.0, 4),  # as percentage
        max_drawdown_pct=round(max_dd, 4),
        simple_return_pct=round(simple_return, 4),
        risk_adjusted_return=round(risk_adj, 4),
    )


# =====================================================================
# Portfolio diversification
# =====================================================================


def assess_diversification(
    positions: list[dict[str, Any]],
    total_value: float,
) -> DiversificationAssessment:
    """Assess portfolio diversification from position data.

    Args:
        positions: List of position dicts.  Each must have ``amount``
            (monetary value invested) and ``instrument_id``.
        total_value: Total portfolio value (cash + invested).

    Returns:
        ``DiversificationAssessment`` with HHI, concentration rating,
        top-position weight, and any overweight warnings.
    """
    if not positions or total_value <= 0:
        return DiversificationAssessment(
            hhi=0.0,
            concentration_rating="n/a",
            top_position_weight_pct=0.0,
            overweight_positions=[],
        )

    weights: list[tuple[int, float]] = []
    for pos in positions:
        amount = float(pos.get("amount", 0.0))
        iid = pos.get("instrument_id", 0)
        weight_pct = (amount / total_value) * 100.0 if total_value > 0 else 0.0
        weights.append((iid, weight_pct))

    # HHI (sum of squared weights, normalised to 0–10,000 scale)
    hhi = sum(w ** 2 for _, w in weights)

    # Top position weight
    top_weight = max((w for _, w in weights), default=0.0)

    # Concentration rating
    if hhi < 1500:
        rating = "well-diversified"
    elif hhi < 2500:
        rating = "moderate"
    else:
        rating = "concentrated"

    # Overweight warnings
    overweight: list[int] = [
        iid for iid, w in weights if w > _CONCENTRATION_WARN_PCT
    ]

    return DiversificationAssessment(
        hhi=round(hhi, 2),
        concentration_rating=rating,
        top_position_weight_pct=round(top_weight, 4),
        overweight_positions=overweight,
    )


# =====================================================================
# Inflation comparison
# =====================================================================


def compute_portfolio_risk_summary(
    instrument_risks: dict[int, InstrumentRiskMetrics],
    positions: list[dict[str, Any]],
    total_value: float,
    cash_available: float,
    inflation_rate_pct: float = _DEFAULT_INFLATION_RATE_PCT,
) -> PortfolioRiskSummary:
    """Compute portfolio-level risk summary with inflation comparison.

    Args:
        instrument_risks: Mapping of ``instrument_id`` → risk metrics.
        positions: List of position dicts (``instrument_id``, ``amount``).
        total_value: Total portfolio value.
        cash_available: Cash held in the portfolio.
        inflation_rate_pct: Annualised inflation rate for comparison.

    Returns:
        ``PortfolioRiskSummary`` with weighted return, inflation delta,
        and cash allocation percentage.
    """
    if not positions or total_value <= 0:
        return PortfolioRiskSummary(
            weighted_return_pct=0.0,
            inflation_rate_pct=inflation_rate_pct,
            beats_inflation=False,
            inflation_delta_pct=-inflation_rate_pct,
            cash_allocation_pct=100.0 if total_value > 0 else 0.0,
        )

    # Weighted return across positions
    total_weighted_return = 0.0
    total_invested = 0.0
    for pos in positions:
        iid = pos.get("instrument_id", 0)
        amount = float(pos.get("amount", 0.0))
        risk = instrument_risks.get(iid)
        if risk is not None and amount > 0:
            total_weighted_return += risk.simple_return_pct * amount
            total_invested += amount

    weighted_return = (
        total_weighted_return / total_invested if total_invested > 0 else 0.0
    )

    inflation_delta = weighted_return - inflation_rate_pct
    beats = weighted_return > inflation_rate_pct

    cash_pct = (cash_available / total_value) * 100.0 if total_value > 0 else 0.0

    return PortfolioRiskSummary(
        weighted_return_pct=round(weighted_return, 4),
        inflation_rate_pct=inflation_rate_pct,
        beats_inflation=beats,
        inflation_delta_pct=round(inflation_delta, 4),
        cash_allocation_pct=round(cash_pct, 4),
    )


# =====================================================================
# Entry point — full critic analysis
# =====================================================================


def analyse_risk(
    candle_map: dict[int, list[dict[str, Any]]],
    positions: list[dict[str, Any]],
    total_value: float,
    cash_available: float,
    inflation_rate_pct: float = _DEFAULT_INFLATION_RATE_PCT,
) -> CriticResult:
    """Run the full financial-analyst risk assessment.

    This is the main entry point.  It computes per-instrument risk
    metrics, assesses diversification, and produces an overall
    portfolio risk summary with an inflation comparison.

    Args:
        candle_map: Mapping of ``instrument_id`` → OHLCV candle list.
        positions: List of position dicts (``instrument_id``, ``amount``).
        total_value: Total portfolio value (cash + invested).
        cash_available: Cash held in the portfolio.
        inflation_rate_pct: Annualised inflation rate for comparison.

    Returns:
        ``CriticResult`` containing per-instrument risks, diversification
        assessment, and portfolio risk summary.
    """
    # Per-instrument risk
    instrument_risks: dict[int, InstrumentRiskMetrics] = {}
    for iid, candles in candle_map.items():
        instrument_risks[iid] = compute_instrument_risk(candles)

    # Diversification
    diversification = assess_diversification(positions, total_value)

    # Portfolio summary
    portfolio_summary = compute_portfolio_risk_summary(
        instrument_risks=instrument_risks,
        positions=positions,
        total_value=total_value,
        cash_available=cash_available,
        inflation_rate_pct=inflation_rate_pct,
    )

    return CriticResult(
        instrument_risks=instrument_risks,
        diversification=diversification,
        portfolio_summary=portfolio_summary,
    )
