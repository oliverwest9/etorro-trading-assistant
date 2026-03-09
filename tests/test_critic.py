"""Tests for the financial analyst critic module.

Verifies risk metrics, diversification assessment, and portfolio critique
using synthetic data with known properties.
"""

from __future__ import annotations

from typing import Any

import pytest

from agent.analysis.critic import (
    DEFAULT_INFLATION_TARGET_PCT,
    _compute_max_drawdown,
    _generate_suggestions,
    assess_diversification,
    compute_risk_metrics,
    critique_portfolio,
)
from agent.analysis.types import (
    CritiqueResult,
    DiversificationAssessment,
    RiskMetrics,
)


# ---------------------------------------------------------------------------
# Helpers — synthetic data builders
# ---------------------------------------------------------------------------


def _make_candles(
    start_close: float,
    end_close: float,
    n: int = 20,
) -> list[dict[str, Any]]:
    """Build candles with linearly interpolated closes."""
    candles: list[dict[str, Any]] = []
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


def _make_volatile_candles(n: int = 20) -> list[dict[str, Any]]:
    """Build candles with high variance (zigzag pattern)."""
    candles: list[dict[str, Any]] = []
    for i in range(n):
        base = 100.0 + (10.0 if i % 2 == 0 else -10.0)
        candles.append({
            "open": base - 1.0,
            "high": base + 5.0,
            "low": base - 5.0,
            "close": base,
            "volume": 1000.0,
            "timestamp": f"2024-01-{10 + i:02d}T00:00:00Z",
        })
    return candles


def _make_position(
    instrument_id: int,
    symbol: str,
    amount: float,
) -> dict[str, Any]:
    return {
        "instrument_id": instrument_id,
        "symbol": symbol,
        "amount": amount,
        "is_buy": True,
        "open_rate": 100.0,
        "units": 1.0,
    }


def _make_snapshot(
    positions: list[dict[str, Any]],
    total_value: float = 10000.0,
    cash_available: float = 2000.0,
) -> dict[str, Any]:
    return {
        "total_value": total_value,
        "cash_available": cash_available,
        "open_positions": len(positions),
        "total_pnl": 0.0,
        "positions": positions,
    }


# ---------------------------------------------------------------------------
# compute_risk_metrics
# ---------------------------------------------------------------------------


class TestComputeRiskMetrics:
    def test_uptrend_positive_return(self) -> None:
        candles = _make_candles(100.0, 120.0, n=20)
        rm = compute_risk_metrics(1, "AAPL", candles)

        assert rm.etoro_id == 1
        assert rm.symbol == "AAPL"
        assert rm.simple_return_pct == pytest.approx(20.0, abs=0.1)
        assert rm.data_points == 20
        assert rm.daily_volatility_pct > 0.0
        assert rm.max_drawdown_pct == pytest.approx(0.0, abs=0.1)
        assert rm.risk_adjusted_return > 0.0

    def test_downtrend_negative_return(self) -> None:
        candles = _make_candles(100.0, 80.0, n=20)
        rm = compute_risk_metrics(2, "MSFT", candles)

        assert rm.simple_return_pct == pytest.approx(-20.0, abs=0.1)
        assert rm.max_drawdown_pct > 0.0
        assert rm.risk_adjusted_return < 0.0

    def test_flat_market_zero_volatility(self) -> None:
        candles = _make_candles(100.0, 100.0, n=20)
        rm = compute_risk_metrics(3, "FLAT", candles)

        assert rm.simple_return_pct == pytest.approx(0.0, abs=0.1)
        assert rm.daily_volatility_pct == pytest.approx(0.0, abs=0.01)

    def test_single_candle_returns_zeros(self) -> None:
        candles = [{"close": 100.0}]
        rm = compute_risk_metrics(4, "ONE", candles)

        assert rm.simple_return_pct == 0.0
        assert rm.daily_volatility_pct == 0.0
        assert rm.max_drawdown_pct == 0.0
        assert rm.data_points == 1

    def test_empty_candles_returns_zeros(self) -> None:
        rm = compute_risk_metrics(5, "EMPTY", [])

        assert rm.simple_return_pct == 0.0
        assert rm.daily_volatility_pct == 0.0
        assert rm.data_points == 0

    def test_volatile_candles_high_volatility(self) -> None:
        candles = _make_volatile_candles(n=20)
        rm = compute_risk_metrics(6, "VOL", candles)

        assert rm.daily_volatility_pct > 20.0  # Should be quite high


# ---------------------------------------------------------------------------
# _compute_max_drawdown
# ---------------------------------------------------------------------------


class TestComputeMaxDrawdown:
    def test_no_drawdown_in_uptrend(self) -> None:
        closes = [100.0, 110.0, 120.0, 130.0]
        assert _compute_max_drawdown(closes) == pytest.approx(0.0)

    def test_known_drawdown(self) -> None:
        # Peak at 200, drops to 100 = 50% drawdown
        closes = [100.0, 200.0, 100.0, 150.0]
        assert _compute_max_drawdown(closes) == pytest.approx(50.0)

    def test_single_value(self) -> None:
        assert _compute_max_drawdown([100.0]) == 0.0

    def test_empty_list(self) -> None:
        assert _compute_max_drawdown([]) == 0.0

    def test_drawdown_at_end(self) -> None:
        closes = [100.0, 150.0, 120.0]
        assert _compute_max_drawdown(closes) == pytest.approx(20.0)


# ---------------------------------------------------------------------------
# assess_diversification
# ---------------------------------------------------------------------------


class TestAssessDiversification:
    def test_single_position_concentrated(self) -> None:
        positions = [_make_position(1, "AAPL", 8000.0)]
        da = assess_diversification(positions, total_value=10000.0)

        assert da.rating == "concentrated"
        assert da.top_position_pct == pytest.approx(80.0)
        assert da.top_position_symbol == "AAPL"

    def test_many_equal_positions_well_diversified(self) -> None:
        positions = [
            _make_position(i, f"SYM{i}", 1000.0)
            for i in range(10)
        ]
        da = assess_diversification(positions, total_value=10000.0)

        assert da.rating == "well_diversified"
        # HHI for 10 equal positions = 10 * (0.1^2) = 0.10
        assert da.herfindahl_index == pytest.approx(0.10, abs=0.01)

    def test_moderate_concentration(self) -> None:
        positions = [
            _make_position(1, "AAPL", 3000.0),
            _make_position(2, "MSFT", 2000.0),
            _make_position(3, "GOOG", 2000.0),
            _make_position(4, "AMZN", 1000.0),
        ]
        da = assess_diversification(positions, total_value=10000.0)

        # HHI = 0.3^2 + 0.2^2 + 0.2^2 + 0.1^2 = 0.09 + 0.04 + 0.04 + 0.01 = 0.18
        assert da.herfindahl_index == pytest.approx(0.18, abs=0.01)
        assert da.rating == "moderate"

    def test_empty_positions(self) -> None:
        da = assess_diversification([], total_value=10000.0)
        assert da.rating == "concentrated"
        assert da.top_position_pct == 0.0

    def test_zero_total_value(self) -> None:
        positions = [_make_position(1, "AAPL", 1000.0)]
        da = assess_diversification(positions, total_value=0.0)
        assert da.rating == "concentrated"

    def test_sector_weights(self) -> None:
        positions = [
            _make_position(1, "AAPL", 5000.0),
            _make_position(2, "BP", 3000.0),
        ]
        sector_map = {1: "US", 2: "UK"}
        da = assess_diversification(
            positions, total_value=10000.0, sector_map=sector_map
        )

        assert da.sector_count == 2
        assert "US" in da.sector_weights
        assert "UK" in da.sector_weights
        assert da.sector_weights["US"] == pytest.approx(50.0, abs=0.1)
        assert da.sector_weights["UK"] == pytest.approx(30.0, abs=0.1)


# ---------------------------------------------------------------------------
# critique_portfolio
# ---------------------------------------------------------------------------


class TestCritiquePortfolio:
    def test_basic_critique_structure(self) -> None:
        positions = [
            _make_position(1, "AAPL", 4000.0),
            _make_position(2, "MSFT", 4000.0),
        ]
        snapshot = _make_snapshot(positions, total_value=10000.0, cash_available=2000.0)
        candle_map = {
            1: _make_candles(100.0, 110.0),
            2: _make_candles(100.0, 105.0),
        }
        inst_map = {
            1: {"symbol": "AAPL", "etoro_id": 1},
            2: {"symbol": "MSFT", "etoro_id": 2},
        }

        result = critique_portfolio(
            snapshot=snapshot,
            candle_map=candle_map,
            instrument_map=inst_map,
        )

        assert isinstance(result, CritiqueResult)
        assert result.inflation_target_pct == DEFAULT_INFLATION_TARGET_PCT
        assert len(result.risk_metrics) == 2
        assert isinstance(result.diversification, DiversificationAssessment)
        assert result.cash_allocation_pct == pytest.approx(20.0)
        assert isinstance(result.suggestions, list)

    def test_beats_inflation_when_returns_high(self) -> None:
        positions = [_make_position(1, "AAPL", 8000.0)]
        snapshot = _make_snapshot(positions, total_value=10000.0, cash_available=2000.0)
        candle_map = {1: _make_candles(100.0, 120.0)}
        inst_map = {1: {"symbol": "AAPL", "etoro_id": 1}}

        result = critique_portfolio(
            snapshot=snapshot,
            candle_map=candle_map,
            instrument_map=inst_map,
            inflation_target_pct=3.5,
        )

        assert result.beats_inflation is True
        assert result.return_vs_inflation_pct > 0.0

    def test_below_inflation_when_returns_low(self) -> None:
        positions = [_make_position(1, "AAPL", 8000.0)]
        snapshot = _make_snapshot(positions, total_value=10000.0, cash_available=2000.0)
        candle_map = {1: _make_candles(100.0, 101.0)}
        inst_map = {1: {"symbol": "AAPL", "etoro_id": 1}}

        result = critique_portfolio(
            snapshot=snapshot,
            candle_map=candle_map,
            instrument_map=inst_map,
            inflation_target_pct=3.5,
        )

        assert result.beats_inflation is False
        assert result.return_vs_inflation_pct < 0.0

    def test_custom_inflation_target(self) -> None:
        positions = [_make_position(1, "AAPL", 5000.0)]
        snapshot = _make_snapshot(positions, total_value=10000.0)
        candle_map = {1: _make_candles(100.0, 110.0)}
        inst_map = {1: {"symbol": "AAPL", "etoro_id": 1}}

        result = critique_portfolio(
            snapshot=snapshot,
            candle_map=candle_map,
            instrument_map=inst_map,
            inflation_target_pct=15.0,
        )

        assert result.inflation_target_pct == 15.0

    def test_empty_portfolio(self) -> None:
        snapshot = _make_snapshot([], total_value=10000.0, cash_available=10000.0)
        result = critique_portfolio(
            snapshot=snapshot,
            candle_map={},
            instrument_map={},
        )

        assert result.portfolio_return_pct == 0.0
        assert result.portfolio_volatility_pct == 0.0
        assert result.cash_allocation_pct == pytest.approx(100.0)

    def test_zero_total_value(self) -> None:
        snapshot = _make_snapshot([], total_value=0.0, cash_available=0.0)
        result = critique_portfolio(
            snapshot=snapshot,
            candle_map={},
            instrument_map={},
        )

        assert result.cash_allocation_pct == 0.0


# ---------------------------------------------------------------------------
# _generate_suggestions
# ---------------------------------------------------------------------------


class TestGenerateSuggestions:
    def test_concentrated_portfolio_flagged(self) -> None:
        div = DiversificationAssessment(
            herfindahl_index=0.50,
            top_position_pct=70.0,
            top_position_symbol="AAPL",
            sector_count=1,
            sector_weights={"US": 100.0},
            rating="concentrated",
        )
        suggestions = _generate_suggestions(
            diversification=div,
            risk_metrics=[],
            port_return=5.0,
            port_vol=10.0,
            inflation_target=3.5,
            cash_pct=10.0,
            beats_inflation=True,
        )

        assert any("concentrated" in s.lower() for s in suggestions)
        assert any("AAPL" in s for s in suggestions)
        assert any("single sector" in s.lower() for s in suggestions)

    def test_high_cash_flagged(self) -> None:
        div = DiversificationAssessment(
            herfindahl_index=0.10,
            top_position_pct=15.0,
            top_position_symbol="AAPL",
            sector_count=3,
            rating="well_diversified",
        )
        suggestions = _generate_suggestions(
            diversification=div,
            risk_metrics=[],
            port_return=5.0,
            port_vol=10.0,
            inflation_target=3.5,
            cash_pct=40.0,
            beats_inflation=True,
        )

        assert any("cash" in s.lower() for s in suggestions)

    def test_low_cash_flagged(self) -> None:
        div = DiversificationAssessment(
            herfindahl_index=0.10,
            top_position_pct=15.0,
            top_position_symbol="AAPL",
            sector_count=3,
            rating="well_diversified",
        )
        suggestions = _generate_suggestions(
            diversification=div,
            risk_metrics=[],
            port_return=5.0,
            port_vol=10.0,
            inflation_target=3.5,
            cash_pct=2.0,
            beats_inflation=True,
        )

        assert any("cash" in s.lower() for s in suggestions)

    def test_below_inflation_flagged(self) -> None:
        div = DiversificationAssessment(
            herfindahl_index=0.10,
            top_position_pct=15.0,
            top_position_symbol="AAPL",
            sector_count=3,
            rating="well_diversified",
        )
        suggestions = _generate_suggestions(
            diversification=div,
            risk_metrics=[],
            port_return=2.0,
            port_vol=10.0,
            inflation_target=3.5,
            cash_pct=10.0,
            beats_inflation=False,
        )

        assert any("inflation" in s.lower() for s in suggestions)

    def test_high_volatility_flagged(self) -> None:
        div = DiversificationAssessment(
            herfindahl_index=0.10,
            top_position_pct=15.0,
            top_position_symbol="AAPL",
            sector_count=3,
            rating="well_diversified",
        )
        rm = RiskMetrics(
            etoro_id=1,
            symbol="BTC",
            daily_volatility_pct=60.0,
            max_drawdown_pct=10.0,
            simple_return_pct=15.0,
            risk_adjusted_return=0.25,
            data_points=20,
        )
        suggestions = _generate_suggestions(
            diversification=div,
            risk_metrics=[rm],
            port_return=5.0,
            port_vol=30.0,
            inflation_target=3.5,
            cash_pct=10.0,
            beats_inflation=True,
        )

        assert any("volatility" in s.lower() for s in suggestions)

    def test_large_drawdown_flagged(self) -> None:
        div = DiversificationAssessment(
            herfindahl_index=0.10,
            top_position_pct=15.0,
            top_position_symbol="AAPL",
            sector_count=3,
            rating="well_diversified",
        )
        rm = RiskMetrics(
            etoro_id=1,
            symbol="TSLA",
            daily_volatility_pct=20.0,
            max_drawdown_pct=35.0,
            simple_return_pct=5.0,
            risk_adjusted_return=0.25,
            data_points=20,
        )
        suggestions = _generate_suggestions(
            diversification=div,
            risk_metrics=[rm],
            port_return=5.0,
            port_vol=10.0,
            inflation_target=3.5,
            cash_pct=10.0,
            beats_inflation=True,
        )

        assert any("drawdown" in s.lower() for s in suggestions)

    def test_negative_risk_adjusted_return_flagged(self) -> None:
        div = DiversificationAssessment(
            herfindahl_index=0.10,
            top_position_pct=15.0,
            top_position_symbol="AAPL",
            sector_count=3,
            rating="well_diversified",
        )
        rm = RiskMetrics(
            etoro_id=1,
            symbol="SNAP",
            daily_volatility_pct=20.0,
            max_drawdown_pct=10.0,
            simple_return_pct=-5.0,
            risk_adjusted_return=-0.25,
            data_points=20,
        )
        suggestions = _generate_suggestions(
            diversification=div,
            risk_metrics=[rm],
            port_return=5.0,
            port_vol=10.0,
            inflation_target=3.5,
            cash_pct=10.0,
            beats_inflation=True,
        )

        assert any("risk-adjusted" in s.lower() for s in suggestions)

    def test_well_balanced_portfolio_gets_positive_note(self) -> None:
        div = DiversificationAssessment(
            herfindahl_index=0.10,
            top_position_pct=15.0,
            top_position_symbol="AAPL",
            sector_count=3,
            rating="well_diversified",
        )
        rm = RiskMetrics(
            etoro_id=1,
            symbol="AAPL",
            daily_volatility_pct=15.0,
            max_drawdown_pct=5.0,
            simple_return_pct=10.0,
            risk_adjusted_return=0.67,
            data_points=20,
        )
        suggestions = _generate_suggestions(
            diversification=div,
            risk_metrics=[rm],
            port_return=5.0,
            port_vol=10.0,
            inflation_target=3.5,
            cash_pct=10.0,
            beats_inflation=True,
        )

        assert len(suggestions) == 1
        assert "well-balanced" in suggestions[0].lower()


# ---------------------------------------------------------------------------
# Type constructors
# ---------------------------------------------------------------------------


class TestRiskMetricsType:
    def test_frozen(self) -> None:
        rm = RiskMetrics(
            etoro_id=1,
            symbol="X",
            daily_volatility_pct=10.0,
            max_drawdown_pct=5.0,
            simple_return_pct=8.0,
            risk_adjusted_return=0.8,
            data_points=20,
        )
        with pytest.raises(AttributeError):
            rm.symbol = "Y"  # type: ignore[misc]


class TestDiversificationAssessmentType:
    def test_defaults(self) -> None:
        da = DiversificationAssessment(
            herfindahl_index=0.1,
            top_position_pct=20.0,
            top_position_symbol="X",
            sector_count=2,
        )
        assert da.sector_weights == {}
        assert da.rating == "moderate"


class TestCritiqueResultType:
    def test_defaults(self) -> None:
        div = DiversificationAssessment(
            herfindahl_index=0.1,
            top_position_pct=20.0,
            top_position_symbol="X",
            sector_count=2,
        )
        cr = CritiqueResult(
            portfolio_return_pct=5.0,
            inflation_target_pct=3.5,
            beats_inflation=True,
            return_vs_inflation_pct=1.5,
            portfolio_volatility_pct=10.0,
            portfolio_sharpe=0.5,
            diversification=div,
        )
        assert cr.risk_metrics == []
        assert cr.cash_allocation_pct == 0.0
        assert cr.suggestions == []
