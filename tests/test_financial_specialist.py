"""Tests for the FinancialAnalystSpecialist agent.

Covers specialist ABC contract, procedural execution with in-memory
SurrealDB, and process_results serialisation.
"""

from __future__ import annotations

from typing import Any, Generator
from unittest.mock import MagicMock, patch

import pytest
from surrealdb.connections.sync_template import SyncTemplate

from agent.agents.base import AgentContext, BaseSpecialist
from agent.agents.specialists.financial import FinancialAnalystSpecialist
from agent.config import Settings
from agent.db.candles import bulk_insert_candles
from agent.db.connection import get_connection
from agent.db.instruments import upsert_instrument
from agent.db.schema import apply_schema
from agent.db.snapshots import create_snapshot_raw
from agent.etoro.models import Candle, Instrument


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _test_settings() -> Settings:
    return Settings(
        etoro_api_key="test-api-key",
        etoro_user_key="test-user-key",
        etoro_base_url="https://example.com",
        surreal_url="memory",
        surreal_namespace="test_ns",
        surreal_database="test_db",
        surreal_user="root",
        surreal_pass="root",
        llm_provider="gemini",
        llm_api_key="",
        llm_model="gemini-2.0-flash",
    )


@pytest.fixture()
def db() -> Generator[SyncTemplate, None, None]:
    with get_connection(_test_settings()) as conn:
        apply_schema(conn)
        yield conn


def _make_candle_models(
    start: float, end: float, n: int = 20,
) -> list[Candle]:
    """Build Candle model instances for testing."""
    from datetime import datetime, timezone
    step = (end - start) / max(n - 1, 1)
    return [
        Candle.model_validate({
            "instrumentID": 1001,
            "fromDate": datetime(2024, 1, 10 + i, tzinfo=timezone.utc).isoformat(),
            "open": start + step * i - 0.5,
            "high": start + step * i + 1.0,
            "low": start + step * i - 1.0,
            "close": start + step * i,
            "volume": 1000.0,
        })
        for i in range(n)
    ]


def _seed_data(db: SyncTemplate) -> None:
    """Insert instrument, candles, and snapshot for testing."""
    instrument = Instrument.model_validate({
        "instrumentID": 1001,
        "symbolFull": "AAPL",
        "instrumentDisplayName": "Apple Inc",
        "instrumentTypeID": 5,
        "exchangeID": 5,
    })
    upsert_instrument(db, instrument)

    candles = _make_candle_models(150.0, 165.0, n=20)
    bulk_insert_candles(db, candles, 1001, "1d")

    # Create snapshot using the raw dict interface
    create_snapshot_raw(db, {
        "total_value": 5000.0,
        "cash_available": 2000.0,
        "open_positions": 1,
        "total_pnl": 150.0,
        "run_type": "market_open",
        "positions": [
            {
                "instrument_id": 1001,
                "instrumentID": 1001,
                "isBuy": True,
                "is_buy": True,
                "openRate": 155.0,
                "open_rate": 155.0,
                "amount": 3000.0,
                "units": 20.0,
                "unrealizedPnL": {"pnL": 150.0},
                "unrealized_pnl": {"pnl": 150.0},
            },
        ],
    })


# ---------------------------------------------------------------------------
# Specialist contract tests
# ---------------------------------------------------------------------------


class TestFinancialAnalystSpecialistContract:
    def test_name(self) -> None:
        s = FinancialAnalystSpecialist()
        assert s.name == "financial"

    def test_description(self) -> None:
        s = FinancialAnalystSpecialist()
        assert "risk" in s.description.lower()
        assert "diversification" in s.description.lower()

    def test_is_base_specialist(self) -> None:
        s = FinancialAnalystSpecialist()
        assert isinstance(s, BaseSpecialist)

    def test_system_prompt(self) -> None:
        s = FinancialAnalystSpecialist()
        prompt = s.get_system_prompt()
        assert "risk" in prompt.lower()

    def test_create_tools_returns_list(self) -> None:
        s = FinancialAnalystSpecialist()
        ctx = AgentContext(
            db=MagicMock(),
            client=MagicMock(),
            settings=MagicMock(),
            run_id="r1",
            run_type="market_open",
        )
        tools = s.create_tools(ctx)
        assert isinstance(tools, list)
        assert len(tools) == 3


# ---------------------------------------------------------------------------
# Procedural execution tests
# ---------------------------------------------------------------------------


class TestFinancialAnalystProcedural:
    def test_procedural_with_data(self, db: SyncTemplate) -> None:
        _seed_data(db)
        specialist = FinancialAnalystSpecialist()
        settings = _test_settings()
        ctx = AgentContext(
            db=db,
            client=MagicMock(),
            settings=settings,
            run_id="test-run-1",
            run_type="market_open",
        )

        state: dict[str, Any] = {
            "instrument_ids": [1001],
            "candle_counts": {1001: 20},
        }

        specialist.run_procedural(state, ctx)
        result = specialist.process_results(state, ctx)

        assert "risk_assessment" in result
        risk = result["risk_assessment"]
        assert risk is not None
        assert "instrument_risks" in risk
        assert 1001 in risk["instrument_risks"]
        assert "diversification" in risk
        assert "portfolio_summary" in risk

    def test_procedural_no_candles(self, db: SyncTemplate) -> None:
        specialist = FinancialAnalystSpecialist()
        ctx = AgentContext(
            db=db,
            client=MagicMock(),
            settings=_test_settings(),
            run_id="test-run-2",
            run_type="market_open",
        )

        state: dict[str, Any] = {
            "instrument_ids": [1001],
            "candle_counts": {},
        }

        specialist.run_procedural(state, ctx)
        result = specialist.process_results(state, ctx)

        assert result["risk_assessment"] is None

    def test_procedural_no_instruments(self, db: SyncTemplate) -> None:
        specialist = FinancialAnalystSpecialist()
        ctx = AgentContext(
            db=db,
            client=MagicMock(),
            settings=_test_settings(),
            run_id="test-run-3",
            run_type="market_open",
        )

        state: dict[str, Any] = {
            "instrument_ids": [],
            "candle_counts": {},
        }

        specialist.run_procedural(state, ctx)
        result = specialist.process_results(state, ctx)

        assert result["risk_assessment"] is None


# ---------------------------------------------------------------------------
# Process results serialisation
# ---------------------------------------------------------------------------


class TestProcessResultsSerialisation:
    def test_risk_metrics_serialised(self, db: SyncTemplate) -> None:
        _seed_data(db)
        specialist = FinancialAnalystSpecialist()
        ctx = AgentContext(
            db=db,
            client=MagicMock(),
            settings=_test_settings(),
            run_id="test-ser-1",
            run_type="market_open",
        )

        state: dict[str, Any] = {
            "instrument_ids": [1001],
            "candle_counts": {1001: 20},
        }

        specialist.run_procedural(state, ctx)
        result = specialist.process_results(state, ctx)

        risk = result["risk_assessment"]
        inst_risk = risk["instrument_risks"][1001]

        # Should have all four metric keys
        assert "annualised_volatility" in inst_risk
        assert "max_drawdown_pct" in inst_risk
        assert "simple_return_pct" in inst_risk
        assert "risk_adjusted_return" in inst_risk

    def test_diversification_serialised(self, db: SyncTemplate) -> None:
        _seed_data(db)
        specialist = FinancialAnalystSpecialist()
        ctx = AgentContext(
            db=db,
            client=MagicMock(),
            settings=_test_settings(),
            run_id="test-ser-2",
            run_type="market_open",
        )

        state: dict[str, Any] = {
            "instrument_ids": [1001],
            "candle_counts": {1001: 20},
        }

        specialist.run_procedural(state, ctx)
        result = specialist.process_results(state, ctx)

        div = result["risk_assessment"]["diversification"]
        assert "hhi" in div
        assert "concentration_rating" in div
        assert "top_position_weight_pct" in div

    def test_portfolio_summary_serialised(self, db: SyncTemplate) -> None:
        _seed_data(db)
        specialist = FinancialAnalystSpecialist()
        ctx = AgentContext(
            db=db,
            client=MagicMock(),
            settings=_test_settings(),
            run_id="test-ser-3",
            run_type="market_open",
        )

        state: dict[str, Any] = {
            "instrument_ids": [1001],
            "candle_counts": {1001: 20},
        }

        specialist.run_procedural(state, ctx)
        result = specialist.process_results(state, ctx)

        ps = result["risk_assessment"]["portfolio_summary"]
        assert "weighted_return_pct" in ps
        assert "inflation_rate_pct" in ps
        assert "beats_inflation" in ps
        assert "cash_allocation_pct" in ps
