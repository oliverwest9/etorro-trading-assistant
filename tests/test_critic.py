"""Tests for the financial-analyst risk analysis module (critic.py).

Covers per-instrument risk metrics, portfolio diversification assessment,
portfolio risk summary with inflation comparison, and the full
``analyse_risk`` entry point.
"""

from __future__ import annotations

from typing import Any

import pytest

from agent.analysis.critic import (
    analyse_risk,
    assess_diversification,
    compute_instrument_risk,
    compute_portfolio_risk_summary,
)
from agent.analysis.types import (
    CriticResult,
    DiversificationAssessment,
    InstrumentRiskMetrics,
    PortfolioRiskSummary,
)


# ---------------------------------------------------------------------------
# Helpers — synthetic candle builders
# ---------------------------------------------------------------------------


def _make_candles(
    start_close: float,
    end_close: float,
    n: int = 30,
) -> list[dict[str, Any]]:
    """Build linearly interpolated candle dicts."""
    candles = []
    step = (end_close - start_close) / max(n - 1, 1)
    for i in range(n):
        price = start_close + step * i
        candles.append({
            "open": price - 0.5,
            "high": price + 1.0,
            "low": price - 1.0,
            "close": price,
            "volume": 1000.0,
            "timestamp": f"2024-01-{10 + i:02d}T00:00:00Z",
        })
    return candles


def _volatile_candles(n: int = 30) -> list[dict[str, Any]]:
    """Build candles with high volatility (large swings)."""
    candles = []
    base = 100.0
    for i in range(n):
        offset = 10.0 if i % 2 == 0 else -10.0
        price = base + offset
        candles.append({
            "open": price - 5.0,
            "high": price + 5.0,
            "low": price - 5.0,
            "close": price,
            "volume": 1000.0,
            "timestamp": f"2024-01-{10 + i:02d}T00:00:00Z",
        })
    return candles


# ---------------------------------------------------------------------------
# Per-instrument risk metrics
# ---------------------------------------------------------------------------


class TestComputeInstrumentRisk:
    def test_uptrend_positive_return(self) -> None:
        candles = _make_candles(100.0, 120.0)
        risk = compute_instrument_risk(candles)

        assert isinstance(risk, InstrumentRiskMetrics)
        assert risk.simple_return_pct == pytest.approx(20.0, abs=0.1)
        assert risk.annualised_volatility > 0
        assert risk.max_drawdown_pct >= 0

    def test_downtrend_negative_return(self) -> None:
        candles = _make_candles(100.0, 80.0)
        risk = compute_instrument_risk(candles)

        assert risk.simple_return_pct == pytest.approx(-20.0, abs=0.1)
        assert risk.max_drawdown_pct > 0

    def test_flat_market_low_volatility(self) -> None:
        candles = _make_candles(100.0, 100.0)
        risk = compute_instrument_risk(candles)

        assert risk.simple_return_pct == pytest.approx(0.0, abs=0.1)

    def test_volatile_candles_higher_volatility(self) -> None:
        calm = _make_candles(100.0, 110.0, n=30)
        volatile = _volatile_candles(n=30)

        risk_calm = compute_instrument_risk(calm)
        risk_volatile = compute_instrument_risk(volatile)

        assert risk_volatile.annualised_volatility > risk_calm.annualised_volatility

    def test_empty_candles_returns_zeros(self) -> None:
        risk = compute_instrument_risk([])
        assert risk.annualised_volatility == 0.0
        assert risk.max_drawdown_pct == 0.0
        assert risk.simple_return_pct == 0.0
        assert risk.risk_adjusted_return == 0.0

    def test_single_candle_returns_zeros(self) -> None:
        risk = compute_instrument_risk([{"close": 100.0}])
        assert risk.simple_return_pct == 0.0

    def test_max_drawdown_captures_peak_to_trough(self) -> None:
        # Price goes 100 → 120 → 90 → 95
        candles = [
            {"close": 100.0}, {"close": 110.0}, {"close": 120.0},
            {"close": 100.0}, {"close": 90.0}, {"close": 95.0},
        ]
        risk = compute_instrument_risk(candles)
        # Max drawdown should be from 120 → 90 = 25%
        assert risk.max_drawdown_pct == pytest.approx(25.0, abs=0.5)

    def test_risk_adjusted_return_sign(self) -> None:
        up = _make_candles(100.0, 130.0)
        down = _make_candles(100.0, 70.0)

        assert compute_instrument_risk(up).risk_adjusted_return > 0
        assert compute_instrument_risk(down).risk_adjusted_return < 0


# ---------------------------------------------------------------------------
# Portfolio diversification
# ---------------------------------------------------------------------------


class TestAssessDiversification:
    def test_well_diversified(self) -> None:
        positions = [
            {"instrument_id": 1, "amount": 100.0},
            {"instrument_id": 2, "amount": 100.0},
            {"instrument_id": 3, "amount": 100.0},
            {"instrument_id": 4, "amount": 100.0},
            {"instrument_id": 5, "amount": 100.0},
            {"instrument_id": 6, "amount": 100.0},
            {"instrument_id": 7, "amount": 100.0},
            {"instrument_id": 8, "amount": 100.0},
            {"instrument_id": 9, "amount": 100.0},
            {"instrument_id": 10, "amount": 100.0},
        ]
        result = assess_diversification(positions, total_value=1000.0)

        assert isinstance(result, DiversificationAssessment)
        assert result.concentration_rating == "well-diversified"
        assert result.hhi == pytest.approx(1000.0, abs=1.0)
        assert result.top_position_weight_pct == pytest.approx(10.0, abs=0.1)
        assert result.overweight_positions == []

    def test_concentrated_portfolio(self) -> None:
        positions = [
            {"instrument_id": 1, "amount": 800.0},
            {"instrument_id": 2, "amount": 200.0},
        ]
        result = assess_diversification(positions, total_value=1000.0)

        assert result.concentration_rating == "concentrated"
        assert result.top_position_weight_pct == pytest.approx(80.0, abs=0.1)
        assert 1 in result.overweight_positions

    def test_moderate_concentration(self) -> None:
        # 5 positions with uneven weights → HHI between 1500 and 2500
        positions = [
            {"instrument_id": 1, "amount": 350.0},
            {"instrument_id": 2, "amount": 250.0},
            {"instrument_id": 3, "amount": 200.0},
            {"instrument_id": 4, "amount": 100.0},
            {"instrument_id": 5, "amount": 100.0},
        ]
        result = assess_diversification(positions, total_value=1000.0)
        # HHI = 35² + 25² + 20² + 10² + 10² = 1225 + 625 + 400 + 100 + 100 = 2450
        assert result.concentration_rating == "moderate"

    def test_overweight_flag(self) -> None:
        positions = [
            {"instrument_id": 1, "amount": 200.0},
            {"instrument_id": 2, "amount": 50.0},
        ]
        result = assess_diversification(positions, total_value=250.0)

        # 200/250 = 80% → flagged; 50/250 = 20% → also flagged
        assert 1 in result.overweight_positions

    def test_empty_positions(self) -> None:
        result = assess_diversification([], total_value=1000.0)

        assert result.hhi == 0.0
        assert result.concentration_rating == "n/a"

    def test_zero_total_value(self) -> None:
        positions = [{"instrument_id": 1, "amount": 100.0}]
        result = assess_diversification(positions, total_value=0.0)

        assert result.concentration_rating == "n/a"


# ---------------------------------------------------------------------------
# Portfolio risk summary
# ---------------------------------------------------------------------------


class TestComputePortfolioRiskSummary:
    def test_beats_inflation(self) -> None:
        risks = {
            1: InstrumentRiskMetrics(
                annualised_volatility=15.0,
                max_drawdown_pct=10.0,
                simple_return_pct=10.0,
                risk_adjusted_return=1.0,
            ),
        }
        positions = [{"instrument_id": 1, "amount": 500.0}]
        result = compute_portfolio_risk_summary(
            risks, positions, total_value=1000.0, cash_available=500.0,
        )

        assert isinstance(result, PortfolioRiskSummary)
        assert result.beats_inflation is True
        assert result.weighted_return_pct == pytest.approx(10.0, abs=0.1)
        assert result.inflation_delta_pct > 0
        assert result.cash_allocation_pct == pytest.approx(50.0, abs=0.1)

    def test_below_inflation(self) -> None:
        risks = {
            1: InstrumentRiskMetrics(
                annualised_volatility=5.0,
                max_drawdown_pct=2.0,
                simple_return_pct=2.0,
                risk_adjusted_return=0.5,
            ),
        }
        positions = [{"instrument_id": 1, "amount": 800.0}]
        result = compute_portfolio_risk_summary(
            risks, positions, total_value=1000.0, cash_available=200.0,
        )

        assert result.beats_inflation is False
        assert result.inflation_delta_pct < 0

    def test_weighted_return_across_positions(self) -> None:
        risks = {
            1: InstrumentRiskMetrics(0, 0, 20.0, 0),
            2: InstrumentRiskMetrics(0, 0, 10.0, 0),
        }
        positions = [
            {"instrument_id": 1, "amount": 300.0},
            {"instrument_id": 2, "amount": 700.0},
        ]
        result = compute_portfolio_risk_summary(
            risks, positions, total_value=1000.0, cash_available=0.0,
        )

        # Weighted: (20*300 + 10*700) / 1000 = 13.0
        assert result.weighted_return_pct == pytest.approx(13.0, abs=0.1)

    def test_empty_positions(self) -> None:
        result = compute_portfolio_risk_summary(
            {}, [], total_value=1000.0, cash_available=1000.0,
        )

        assert result.beats_inflation is False
        assert result.cash_allocation_pct == pytest.approx(100.0)

    def test_custom_inflation_rate(self) -> None:
        risks = {
            1: InstrumentRiskMetrics(0, 0, 5.0, 0),
        }
        positions = [{"instrument_id": 1, "amount": 500.0}]
        result = compute_portfolio_risk_summary(
            risks, positions, total_value=1000.0, cash_available=500.0,
            inflation_rate_pct=4.0,
        )

        assert result.inflation_rate_pct == 4.0
        assert result.beats_inflation is True


# ---------------------------------------------------------------------------
# Full analyse_risk entry point
# ---------------------------------------------------------------------------


class TestAnalyseRisk:
    def test_full_analysis(self) -> None:
        candle_map = {
            1: _make_candles(100.0, 120.0),
            2: _make_candles(50.0, 45.0),
        }
        positions = [
            {"instrument_id": 1, "amount": 600.0},
            {"instrument_id": 2, "amount": 400.0},
        ]
        result = analyse_risk(
            candle_map=candle_map,
            positions=positions,
            total_value=1000.0,
            cash_available=0.0,
        )

        assert isinstance(result, CriticResult)
        assert 1 in result.instrument_risks
        assert 2 in result.instrument_risks
        assert result.diversification is not None
        assert result.portfolio_summary is not None

    def test_empty_candle_map(self) -> None:
        result = analyse_risk(
            candle_map={},
            positions=[{"instrument_id": 1, "amount": 100.0}],
            total_value=100.0,
            cash_available=0.0,
        )

        assert result.instrument_risks == {}
        assert result.diversification is not None
        assert result.portfolio_summary is not None

    def test_empty_positions(self) -> None:
        result = analyse_risk(
            candle_map={1: _make_candles(100.0, 110.0)},
            positions=[],
            total_value=0.0,
            cash_available=0.0,
        )

        assert 1 in result.instrument_risks
        assert result.diversification is not None
        assert result.diversification.concentration_rating == "n/a"
