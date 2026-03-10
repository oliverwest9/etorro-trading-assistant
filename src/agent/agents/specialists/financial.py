"""Financial analyst specialist agent.

Responsible for computing per-instrument risk metrics (volatility,
drawdown, Sharpe-like ratio), assessing portfolio diversification
(HHI, concentration warnings), and comparing portfolio returns
against an inflation benchmark.  Runs after the ``analysis``
specialist so that candle data is available.
"""

from __future__ import annotations

from typing import Any

import structlog
from langchain_core.tools import tool as langchain_tool

from agent.agents.base import AgentContext, BaseSpecialist
from agent.analysis.critic import (
    analyse_risk,
    assess_diversification,
    compute_instrument_risk,
    compute_portfolio_risk_summary,
)
from agent.analysis.types import CriticResult
from agent.db.candles import query_candles
from agent.db.snapshots import get_latest_snapshot

logger = structlog.get_logger(__name__)


class FinancialAnalystSpecialist(BaseSpecialist):
    """Risk assessment, diversification analysis, and inflation-adjusted returns."""

    @property
    def name(self) -> str:
        return "financial"

    @property
    def description(self) -> str:
        return (
            "Computes per-instrument risk metrics (volatility, max drawdown, "
            "Sharpe ratio), assesses portfolio diversification (HHI, "
            "concentration warnings), and compares returns against inflation. "
            "Call after analysis is complete."
        )

    def get_system_prompt(self) -> str:
        return (
            "You are the financial analyst specialist for a trading portfolio agent. "
            "Your focus is on risk management, capital preservation, and long-term "
            "inflation-beating returns.\n\n"
            "Your job is to:\n"
            "1. Compute per-instrument risk metrics using compute_risk_metrics\n"
            "2. Assess portfolio diversification using assess_portfolio_diversification\n"
            "3. Generate the portfolio risk summary using generate_risk_summary\n\n"
            "Call these tools in order. The risk summary depends on instrument metrics."
        )

    def create_tools(self, ctx: AgentContext) -> list[Any]:

        @langchain_tool
        def compute_risk_metrics(instrument_ids: str) -> str:
            """Compute risk metrics for each instrument.

            Args:
                instrument_ids: Comma-separated instrument IDs (e.g. "1010,1191")

            Returns per-instrument volatility, max drawdown, return, and risk-adjusted return.
            """
            try:
                ids = [int(x.strip()) for x in instrument_ids.split(",") if x.strip()]
            except ValueError:
                return "ERROR: instrument_ids must be comma-separated integers"

            results: list[str] = []
            instrument_risks = {}
            for iid in ids:
                try:
                    candles = query_candles(ctx.db, iid, "1d")
                    risk = compute_instrument_risk(candles)
                    instrument_risks[iid] = risk
                    results.append(
                        f"{iid}: vol={risk.annualised_volatility:.1f}%, "
                        f"dd={risk.max_drawdown_pct:.1f}%, "
                        f"ret={risk.simple_return_pct:.1f}%, "
                        f"sharpe={risk.risk_adjusted_return:.2f}"
                    )
                except Exception as exc:
                    results.append(f"{iid}: ERROR {exc}")

            self._instrument_risks = instrument_risks
            return f"Risk metrics computed for {len(instrument_risks)} instruments:\n" + "\n".join(results)

        @langchain_tool
        def assess_portfolio_diversification() -> str:
            """Assess portfolio diversification from the latest snapshot.

            Returns HHI, concentration rating, top position weight, and overweight warnings.
            """
            snapshot = get_latest_snapshot(ctx.db)
            if snapshot is None:
                return "SKIP: No portfolio snapshot found."

            total_value = snapshot.get("total_value", 0.0)
            positions = snapshot.get("positions", [])

            pos_list = []
            for pos in positions:
                pos_list.append({
                    "instrument_id": pos.get("instrument_id", pos.get("instrumentID", 0)),
                    "amount": pos.get("amount", 0.0),
                })

            result = assess_diversification(pos_list, total_value)
            self._diversification = result
            self._positions = pos_list
            self._total_value = total_value
            self._cash_available = snapshot.get("cash_available", 0.0)

            lines = [
                f"HHI: {result.hhi:.0f} ({result.concentration_rating})",
                f"Top position weight: {result.top_position_weight_pct:.1f}%",
            ]
            if result.overweight_positions:
                lines.append(f"Overweight positions (>{15}%): {result.overweight_positions}")
            return "\n".join(lines)

        @langchain_tool
        def generate_risk_summary() -> str:
            """Generate portfolio-level risk summary with inflation comparison.

            Must be called after compute_risk_metrics and assess_portfolio_diversification.
            """
            instrument_risks = getattr(self, "_instrument_risks", {})
            positions = getattr(self, "_positions", [])
            total_value = getattr(self, "_total_value", 0.0)
            cash_available = getattr(self, "_cash_available", 0.0)

            if not instrument_risks:
                return "ERROR: Must call compute_risk_metrics first."

            summary = compute_portfolio_risk_summary(
                instrument_risks=instrument_risks,
                positions=positions,
                total_value=total_value,
                cash_available=cash_available,
            )
            self._portfolio_summary = summary

            status = "BEATING" if summary.beats_inflation else "BELOW"
            return (
                f"Portfolio weighted return: {summary.weighted_return_pct:+.2f}%\n"
                f"Inflation benchmark: {summary.inflation_rate_pct:.1f}%\n"
                f"Status: {status} inflation (delta: {summary.inflation_delta_pct:+.2f}%)\n"
                f"Cash allocation: {summary.cash_allocation_pct:.1f}%"
            )

        return [compute_risk_metrics, assess_portfolio_diversification, generate_risk_summary]

    def run_procedural(
        self,
        state: dict[str, Any],
        ctx: AgentContext,
    ) -> None:
        """Run the full risk assessment procedurally."""
        instrument_ids = state.get("instrument_ids", [])
        candle_counts = state.get("candle_counts", {})

        ids_with_candles = [
            iid for iid in instrument_ids if candle_counts.get(iid, 0) > 0
        ]
        if not ids_with_candles:
            return

        # Build candle map
        candle_map: dict[int, list[dict[str, Any]]] = {}
        for iid in ids_with_candles:
            try:
                candle_map[iid] = query_candles(ctx.db, iid, "1d")
            except Exception:
                candle_map[iid] = []

        # Get portfolio snapshot for positions
        snapshot = get_latest_snapshot(ctx.db)
        if snapshot is None:
            return

        total_value = snapshot.get("total_value", 0.0)
        cash_available = snapshot.get("cash_available", 0.0)

        positions = []
        for pos in snapshot.get("positions", []):
            positions.append({
                "instrument_id": pos.get("instrument_id", pos.get("instrumentID", 0)),
                "amount": pos.get("amount", 0.0),
            })

        # Run full risk analysis
        try:
            critic_result = analyse_risk(
                candle_map=candle_map,
                positions=positions,
                total_value=total_value,
                cash_available=cash_available,
            )
            self._critic_result = critic_result
        except Exception as exc:
            logger.warning("risk_analysis_failed", error=str(exc))

    def process_results(
        self,
        state: dict[str, Any],
        ctx: AgentContext,
    ) -> dict[str, Any]:
        """Return the critic result for downstream consumers."""
        critic_result: CriticResult | None = getattr(self, "_critic_result", None)

        # Clean up
        self._critic_result = None
        self._instrument_risks = None
        self._diversification = None
        self._positions = None
        self._total_value = None
        self._cash_available = None
        self._portfolio_summary = None

        if critic_result is None:
            return {"risk_assessment": None}

        # Serialise to a dict for the pipeline state
        risk_dict: dict[str, Any] = {
            "instrument_risks": {
                iid: {
                    "annualised_volatility": r.annualised_volatility,
                    "max_drawdown_pct": r.max_drawdown_pct,
                    "simple_return_pct": r.simple_return_pct,
                    "risk_adjusted_return": r.risk_adjusted_return,
                }
                for iid, r in critic_result.instrument_risks.items()
            },
        }
        if critic_result.diversification is not None:
            d = critic_result.diversification
            risk_dict["diversification"] = {
                "hhi": d.hhi,
                "concentration_rating": d.concentration_rating,
                "top_position_weight_pct": d.top_position_weight_pct,
                "overweight_positions": d.overweight_positions,
            }
        if critic_result.portfolio_summary is not None:
            ps = critic_result.portfolio_summary
            risk_dict["portfolio_summary"] = {
                "weighted_return_pct": ps.weighted_return_pct,
                "inflation_rate_pct": ps.inflation_rate_pct,
                "beats_inflation": ps.beats_inflation,
                "inflation_delta_pct": ps.inflation_delta_pct,
                "cash_allocation_pct": ps.cash_allocation_pct,
            }

        return {"risk_assessment": risk_dict}
