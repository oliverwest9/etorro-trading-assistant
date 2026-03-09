"""Financial analyst critic — portfolio-level risk and quality assessment.

Acts as an independent financial analyst who critiques the portfolio and
analysis, focusing on:

* **Risk management** — volatility, drawdown, risk-adjusted returns.
* **Diversification** — concentration risk across positions and sectors.
* **Inflation-beating potential** — whether the portfolio is on track
  to outperform a configurable inflation target over the long term.
* **Responsible position sizing** — flags oversized positions and
  excessive cash drag.

All functions are **pure** — no API or DB calls.  They consume the same
candle / instrument / snapshot dicts produced by the data pipeline.
"""

from __future__ import annotations

import math
from typing import Any

from agent.analysis.types import (
    CritiqueResult,
    DiversificationAssessment,
    RiskMetrics,
)

# Annualised UK CPI inflation target — used as the default benchmark.
DEFAULT_INFLATION_TARGET_PCT: float = 3.5

# Trading days per year (used to annualise daily volatility).
_TRADING_DAYS_PER_YEAR: int = 252

# Position-size thresholds for suggestions.
_MAX_POSITION_WEIGHT_PCT: float = 25.0
_HIGH_CASH_PCT: float = 30.0
_LOW_CASH_PCT: float = 5.0

# Herfindahl rating thresholds.
_HHI_CONCENTRATED: float = 0.25
_HHI_MODERATE: float = 0.15


# =====================================================================
# Per-instrument risk metrics
# =====================================================================


def compute_risk_metrics(
    etoro_id: int,
    symbol: str,
    candles: list[dict[str, Any]],
) -> RiskMetrics:
    """Compute risk metrics for a single instrument from candle data.

    Args:
        etoro_id: eToro instrument identifier.
        symbol: Ticker symbol (for display).
        candles: OHLCV candle dicts sorted by timestamp ascending.

    Returns:
        ``RiskMetrics`` with volatility, drawdown, return, and
        risk-adjusted return.
    """
    if len(candles) < 2:
        return RiskMetrics(
            etoro_id=etoro_id,
            symbol=symbol,
            daily_volatility_pct=0.0,
            max_drawdown_pct=0.0,
            simple_return_pct=0.0,
            risk_adjusted_return=0.0,
            data_points=len(candles),
        )

    closes = [float(c.get("close", 0.0)) for c in candles]

    # Daily returns
    daily_returns: list[float] = []
    for i in range(1, len(closes)):
        if closes[i - 1] != 0.0:
            daily_returns.append((closes[i] - closes[i - 1]) / closes[i - 1])

    # Volatility (annualised)
    if daily_returns:
        mean_ret = sum(daily_returns) / len(daily_returns)
        variance = sum((r - mean_ret) ** 2 for r in daily_returns) / len(daily_returns)
        daily_vol = math.sqrt(variance)
        annual_vol = daily_vol * math.sqrt(_TRADING_DAYS_PER_YEAR) * 100.0
    else:
        annual_vol = 0.0

    # Simple return
    first_close = closes[0]
    last_close = closes[-1]
    simple_return = (
        ((last_close - first_close) / first_close) * 100.0
        if first_close != 0.0
        else 0.0
    )

    # Maximum drawdown
    max_drawdown = _compute_max_drawdown(closes)

    # Risk-adjusted return (return / volatility, Sharpe-like)
    risk_adj = simple_return / annual_vol if annual_vol > 0 else 0.0

    return RiskMetrics(
        etoro_id=etoro_id,
        symbol=symbol,
        daily_volatility_pct=round(annual_vol, 4),
        max_drawdown_pct=round(max_drawdown, 4),
        simple_return_pct=round(simple_return, 4),
        risk_adjusted_return=round(risk_adj, 4),
        data_points=len(candles),
    )


def _compute_max_drawdown(closes: list[float]) -> float:
    """Return the maximum peak-to-trough decline as a positive percentage."""
    if len(closes) < 2:
        return 0.0

    peak = closes[0]
    max_dd = 0.0
    for price in closes[1:]:
        if price > peak:
            peak = price
        dd = ((peak - price) / peak) * 100.0 if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
    return max_dd


# =====================================================================
# Diversification assessment
# =====================================================================


def assess_diversification(
    positions: list[dict[str, Any]],
    total_value: float,
    sector_map: dict[int, str] | None = None,
) -> DiversificationAssessment:
    """Evaluate portfolio diversification from position data.

    Args:
        positions: List of position dicts (must have ``instrument_id``
            and ``amount``).
        total_value: Total portfolio value (cash + invested).
        sector_map: Optional mapping of instrument ID → sector/group name.

    Returns:
        ``DiversificationAssessment`` with Herfindahl index, top
        position details, and sector breakdown.
    """
    if not positions or total_value <= 0:
        return DiversificationAssessment(
            herfindahl_index=0.0,
            top_position_pct=0.0,
            top_position_symbol="N/A",
            sector_count=0,
            sector_weights={},
            rating="concentrated",
        )

    sector_map = sector_map or {}

    # Compute position weights
    weights: list[tuple[str, float, int]] = []  # (symbol, weight_pct, iid)
    for pos in positions:
        amount = float(pos.get("amount", 0.0))
        iid = pos.get("instrument_id", pos.get("instrumentID", 0))
        symbol = pos.get("symbol", f"ID:{iid}")
        weight_pct = (amount / total_value) * 100.0 if total_value > 0 else 0.0
        weights.append((symbol, weight_pct, iid))

    # Herfindahl-Hirschman Index (sum of squared weight fractions)
    weight_fractions = [w / 100.0 for _, w, _ in weights]
    hhi = sum(f * f for f in weight_fractions)

    # Top position
    top_symbol, top_pct, _ = max(weights, key=lambda t: t[1])

    # Sector weights
    sector_totals: dict[str, float] = {}
    for _symbol, pct, iid in weights:
        sector = sector_map.get(iid, "Unknown")
        sector_totals[sector] = sector_totals.get(sector, 0.0) + pct

    # Rating
    if hhi >= _HHI_CONCENTRATED:
        rating = "concentrated"
    elif hhi >= _HHI_MODERATE:
        rating = "moderate"
    else:
        rating = "well_diversified"

    return DiversificationAssessment(
        herfindahl_index=round(hhi, 4),
        top_position_pct=round(top_pct, 2),
        top_position_symbol=top_symbol,
        sector_count=len(sector_totals),
        sector_weights={k: round(v, 2) for k, v in sorted(sector_totals.items())},
        rating=rating,
    )


# =====================================================================
# Portfolio-level critique
# =====================================================================


def critique_portfolio(
    *,
    snapshot: dict[str, Any],
    candle_map: dict[int, list[dict[str, Any]]],
    instrument_map: dict[int, dict[str, Any]],
    sector_map: dict[int, str] | None = None,
    inflation_target_pct: float = DEFAULT_INFLATION_TARGET_PCT,
) -> CritiqueResult:
    """Produce a complete portfolio critique.

    This is the main entry point for the financial analyst critic.
    It combines per-instrument risk metrics with a portfolio-level
    diversification and inflation-beating assessment.

    Args:
        snapshot: Portfolio snapshot dict (keys: ``total_value``,
            ``cash_available``, ``positions``).
        candle_map: Mapping of eToro ID → OHLCV candle list.
        instrument_map: Mapping of eToro ID → instrument dict
            (must have ``symbol``).
        sector_map: Optional mapping of eToro ID → sector/group name.
        inflation_target_pct: Annualised inflation benchmark (default 3.5 %).

    Returns:
        ``CritiqueResult`` with risk data, diversification score,
        inflation comparison, and actionable suggestions.
    """
    total_value = float(snapshot.get("total_value", 0.0))
    cash = float(snapshot.get("cash_available", 0.0))
    positions = snapshot.get("positions", [])

    # Per-instrument risk metrics
    risk_metrics: list[RiskMetrics] = []
    for iid, candles in candle_map.items():
        inst = instrument_map.get(iid, {})
        symbol = inst.get("symbol", f"ID:{iid}")
        risk_metrics.append(compute_risk_metrics(iid, symbol, candles))

    # Diversification
    enriched_positions = _enrich_positions(positions, instrument_map)
    diversification = assess_diversification(
        enriched_positions, total_value, sector_map,
    )

    # Portfolio-level weighted return and volatility
    port_return, port_vol = _portfolio_weighted_stats(
        positions, total_value, risk_metrics,
    )

    # Sharpe-like ratio for the portfolio
    port_sharpe = port_return / port_vol if port_vol > 0 else 0.0

    # Inflation comparison
    beats = port_return > inflation_target_pct
    excess = port_return - inflation_target_pct

    # Cash allocation
    cash_pct = (cash / total_value * 100.0) if total_value > 0 else 0.0

    # Suggestions
    suggestions = _generate_suggestions(
        diversification=diversification,
        risk_metrics=risk_metrics,
        port_return=port_return,
        port_vol=port_vol,
        inflation_target=inflation_target_pct,
        cash_pct=cash_pct,
        beats_inflation=beats,
    )

    return CritiqueResult(
        portfolio_return_pct=round(port_return, 4),
        inflation_target_pct=inflation_target_pct,
        beats_inflation=beats,
        return_vs_inflation_pct=round(excess, 4),
        portfolio_volatility_pct=round(port_vol, 4),
        portfolio_sharpe=round(port_sharpe, 4),
        diversification=diversification,
        risk_metrics=risk_metrics,
        cash_allocation_pct=round(cash_pct, 2),
        suggestions=suggestions,
    )


# =====================================================================
# Internal helpers
# =====================================================================


def _enrich_positions(
    positions: list[dict[str, Any]],
    instrument_map: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Add ``symbol`` to position dicts where missing."""
    enriched: list[dict[str, Any]] = []
    for pos in positions:
        p = dict(pos)
        iid = p.get("instrument_id", p.get("instrumentID", 0))
        if "symbol" not in p:
            inst = instrument_map.get(iid, {})
            p["symbol"] = inst.get("symbol", f"ID:{iid}")
        enriched.append(p)
    return enriched


def _portfolio_weighted_stats(
    positions: list[dict[str, Any]],
    total_value: float,
    risk_metrics: list[RiskMetrics],
) -> tuple[float, float]:
    """Compute weighted portfolio return and volatility.

    Returns:
        ``(weighted_return_pct, weighted_volatility_pct)``
    """
    if not positions or total_value <= 0:
        return 0.0, 0.0

    metrics_by_id = {rm.etoro_id: rm for rm in risk_metrics}

    weighted_return = 0.0
    weighted_vol_sq = 0.0
    total_invested = 0.0

    for pos in positions:
        amount = float(pos.get("amount", 0.0))
        iid = pos.get("instrument_id", pos.get("instrumentID", 0))
        rm = metrics_by_id.get(iid)
        if rm is None or amount <= 0:
            continue

        weight = amount / total_value
        weighted_return += weight * rm.simple_return_pct
        # Simplified: assume uncorrelated positions for portfolio vol
        weighted_vol_sq += (weight * rm.daily_volatility_pct) ** 2
        total_invested += amount

    weighted_vol = math.sqrt(weighted_vol_sq) if weighted_vol_sq > 0 else 0.0
    return round(weighted_return, 4), round(weighted_vol, 4)


def _generate_suggestions(
    *,
    diversification: DiversificationAssessment,
    risk_metrics: list[RiskMetrics],
    port_return: float,
    port_vol: float,
    inflation_target: float,
    cash_pct: float,
    beats_inflation: bool,
) -> list[str]:
    """Generate actionable suggestions based on portfolio analysis."""
    suggestions: list[str] = []

    # Diversification
    if diversification.rating == "concentrated":
        suggestions.append(
            f"Portfolio is concentrated (HHI: {diversification.herfindahl_index:.2f}). "
            f"Consider spreading capital across more positions to reduce "
            f"single-stock risk."
        )
    if diversification.top_position_pct > _MAX_POSITION_WEIGHT_PCT:
        suggestions.append(
            f"{diversification.top_position_symbol} accounts for "
            f"{diversification.top_position_pct:.1f}% of the portfolio. "
            f"Consider trimming to below {_MAX_POSITION_WEIGHT_PCT:.0f}% "
            f"to limit concentration risk."
        )
    if diversification.sector_count <= 1 and diversification.sector_count > 0:
        suggestions.append(
            "All positions are in a single sector. Diversify across "
            "sectors or geographies to reduce correlated risk."
        )

    # Cash allocation
    if cash_pct > _HIGH_CASH_PCT:
        suggestions.append(
            f"Cash allocation is {cash_pct:.1f}% — high cash drag may "
            f"undermine long-term returns. Consider deploying into "
            f"diversified positions if conviction is present."
        )
    elif cash_pct < _LOW_CASH_PCT and cash_pct >= 0:
        suggestions.append(
            f"Cash reserves are only {cash_pct:.1f}%. Maintain a small "
            f"cash buffer to take advantage of future opportunities."
        )

    # Inflation
    if not beats_inflation:
        gap = inflation_target - port_return
        suggestions.append(
            f"Portfolio return ({port_return:+.2f}%) is below the "
            f"{inflation_target:.1f}% inflation target by {gap:.2f}pp. "
            f"Review underperforming positions or consider rebalancing "
            f"into higher-growth assets."
        )

    # High-volatility positions
    high_vol = [rm for rm in risk_metrics if rm.daily_volatility_pct > 40.0]
    if high_vol:
        symbols = ", ".join(rm.symbol for rm in high_vol)
        suggestions.append(
            f"High volatility detected in: {symbols}. For a long-term "
            f"inflation-beating strategy, consider whether these positions "
            f"align with your risk tolerance."
        )

    # Large drawdowns
    big_dd = [rm for rm in risk_metrics if rm.max_drawdown_pct > 20.0]
    if big_dd:
        symbols = ", ".join(rm.symbol for rm in big_dd)
        suggestions.append(
            f"Significant drawdowns (>20%) observed in: {symbols}. "
            f"Review stop-loss levels or consider reducing exposure."
        )

    # Negative risk-adjusted return
    neg_sharpe = [
        rm for rm in risk_metrics
        if rm.risk_adjusted_return < 0 and rm.data_points >= 5
    ]
    if neg_sharpe:
        symbols = ", ".join(rm.symbol for rm in neg_sharpe)
        suggestions.append(
            f"Negative risk-adjusted returns in: {symbols}. These "
            f"positions are losing money relative to their risk. "
            f"Evaluate whether the long-term thesis still holds."
        )

    if not suggestions:
        suggestions.append(
            "Portfolio appears well-balanced for a long-term "
            "inflation-beating strategy. Continue to monitor and "
            "rebalance periodically."
        )

    return suggestions
