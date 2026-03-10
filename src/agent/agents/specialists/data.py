"""Data specialist agent.

Responsible for fetching the eToro portfolio, resolving instrument
metadata, and downloading candle history.  The internal LLM can decide
to fetch extra candles for volatile instruments.
"""

from __future__ import annotations

from typing import Any

import structlog
from langchain_core.tools import tool as langchain_tool
from pydantic import ValidationError

from agent.agents.base import AgentContext, BaseSpecialist
from agent.db.candles import bulk_insert_candles
from agent.db.instruments import upsert_instrument
from agent.db.snapshots import create_snapshot, get_latest_snapshot
from agent.etoro.client import EToroError
from agent.etoro.market_data import get_candles
from agent.etoro.models import Instrument, InstrumentSearchResponse
from agent.etoro.portfolio import get_portfolio

logger = structlog.get_logger(__name__)


class DataSpecialist(BaseSpecialist):
    """Fetches portfolio positions, resolves instruments, downloads candles."""

    @property
    def name(self) -> str:
        return "data"

    @property
    def description(self) -> str:
        return (
            "Fetches the eToro portfolio, resolves instrument metadata, "
            "and downloads OHLCV candle history for each position. "
            "Call this first to populate market data."
        )

    def get_system_prompt(self) -> str:
        return (
            "You are the data collection specialist for a trading portfolio agent. "
            "Your job is to:\n"
            "1. Fetch the current portfolio using fetch_portfolio\n"
            "2. Resolve instrument metadata using resolve_instruments\n"
            "3. Fetch candle history for each instrument using fetch_candles\n\n"
            "Call fetch_portfolio first, then resolve_instruments with the "
            "instrument IDs, then fetch_candles for each instrument. "
            "For instruments in highly volatile sectors (crypto), consider "
            "fetching more candles (up to 200) for better analysis.\n\n"
            "Always call all three tools to ensure complete data collection."
        )

    def create_tools(self, ctx: AgentContext) -> list[Any]:
        @langchain_tool
        def fetch_portfolio() -> str:
            """Fetch the current eToro portfolio and save a snapshot to the database.

            Returns a summary of the portfolio including position count,
            credit, and list of instrument IDs.
            """
            try:
                portfolio_resp = get_portfolio(ctx.client)
            except EToroError as exc:
                logger.error("portfolio_fetch_failed", error=str(exc))
                return f"ERROR: Portfolio fetch failed: {exc}"

            portfolio = portfolio_resp.client_portfolio
            snapshot = create_snapshot(ctx.db, portfolio, ctx.run_type)
            snapshot_id = str(snapshot.get("id", ""))

            instrument_ids = sorted(
                {pos.instrument_id for pos in portfolio.positions}
            )

            logger.info(
                "portfolio_fetched",
                snapshot_id=snapshot_id,
                positions=len(portfolio.positions),
                credit=portfolio.credit,
            )

            return (
                f"Portfolio snapshot created: {snapshot_id}\n"
                f"Positions: {len(portfolio.positions)}\n"
                f"Credit: {portfolio.credit}\n"
                f"Instrument IDs: {instrument_ids}"
            )

        @langchain_tool
        def resolve_instruments(instrument_ids: str) -> str:
            """Resolve instrument metadata from the eToro API and save to database.

            Args:
                instrument_ids: Comma-separated list of instrument IDs (e.g. "1010,1191,2009")

            Returns a summary of resolved instruments.
            """
            try:
                ids = [int(x.strip()) for x in instrument_ids.split(",") if x.strip()]
            except ValueError:
                return "ERROR: instrument_ids must be comma-separated integers"

            try:
                response = ctx.client.get("/market-data/instruments")
                parsed = InstrumentSearchResponse.model_validate(response.json())
            except (EToroError, ValidationError) as exc:
                logger.warning("instrument_resolution_failed", error=str(exc))
                return f"ERROR: Failed to fetch instrument catalog: {exc}"

            wanted = set(ids)
            resolved: list[str] = []

            for item in parsed.items:
                iid = item.get("instrumentID")
                if iid in wanted:
                    try:
                        instrument = Instrument.model_validate(item)
                        upsert_instrument(ctx.db, instrument)
                        resolved.append(f"{instrument.symbol} (ID:{iid})")
                    except ValidationError:
                        logger.warning("instrument_parse_failed", instrument_id=iid)

            logger.info(
                "instruments_resolved",
                wanted=len(wanted),
                found=len(resolved),
            )

            return (
                f"Resolved {len(resolved)}/{len(wanted)} instruments:\n"
                + "\n".join(resolved)
            )

        @langchain_tool
        def fetch_candles(instrument_id: int, count: int = 100) -> str:
            """Fetch OHLCV candle history for an instrument and store in the database.

            Args:
                instrument_id: The eToro instrument ID to fetch candles for.
                count: Number of daily candles to fetch (default 100, max 1000).

            Returns the number of new candles inserted.
            """
            count = max(1, min(count, 1000))
            try:
                candles = get_candles(ctx.client, instrument_id, count=count)
                inserted = bulk_insert_candles(ctx.db, candles, instrument_id, "1d")
                logger.info(
                    "candles_fetched",
                    instrument_id=instrument_id,
                    fetched=len(candles),
                    inserted=len(inserted),
                )
                return (
                    f"Instrument {instrument_id}: fetched {len(candles)} candles, "
                    f"{len(inserted)} new candles inserted."
                )
            except Exception as exc:
                logger.warning(
                    "candle_fetch_failed",
                    instrument_id=instrument_id,
                    error=str(exc),
                )
                return f"ERROR: Failed to fetch candles for {instrument_id}: {exc}"

        return [fetch_portfolio, resolve_instruments, fetch_candles]

    def run_procedural(
        self,
        state: dict[str, Any],
        ctx: AgentContext,
    ) -> None:
        """Fetch portfolio, resolve instruments, download candles."""
        # 1. Portfolio (fatal on failure)
        try:
            portfolio_resp = get_portfolio(ctx.client)
        except EToroError as exc:
            raise RuntimeError(f"Portfolio fetch failed: {exc}") from exc
        portfolio = portfolio_resp.client_portfolio
        create_snapshot(ctx.db, portfolio, ctx.run_type)

        instrument_ids = sorted(
            {pos.instrument_id for pos in portfolio.positions}
        )
        if not instrument_ids:
            return

        # 2. Resolve instruments (best-effort)
        # Track volatile instruments for adaptive candle counts
        volatile_ids: set[int] = set()
        try:
            response = ctx.client.get("/market-data/instruments")
            parsed = InstrumentSearchResponse.model_validate(response.json())
            wanted = set(instrument_ids)
            for item in parsed.items:
                if item.get("instrumentID") in wanted:
                    try:
                        instrument = Instrument.model_validate(item)
                        upsert_instrument(ctx.db, instrument)
                        if instrument.asset_class == "Crypto":
                            volatile_ids.add(instrument.instrument_id)
                    except ValidationError:
                        pass
        except Exception as exc:
            logger.warning("instrument_resolution_failed", error=str(exc))

        if volatile_ids:
            logger.info(
                "adaptive_candle_count",
                volatile_instruments=sorted(volatile_ids),
                count=200,
                reason="Crypto instruments get extra history for better analysis",
            )

        # 3. Candles (per-instrument, non-fatal)
        # Adaptive: fetch 200 candles for volatile instruments, 100 for others
        errors = state.get("errors", [])
        for iid in instrument_ids:
            count = 200 if iid in volatile_ids else 100
            try:
                candles = get_candles(ctx.client, iid, count=count)
                bulk_insert_candles(ctx.db, candles, iid, "1d")
            except Exception as exc:
                errors.append({
                    "instrument_id": iid,
                    "error": str(exc),
                    "step": "candles",
                })
        state["errors"] = errors

    def process_results(
        self,
        state: dict[str, Any],
        ctx: AgentContext,
    ) -> dict[str, Any]:
        """Read the latest snapshot from DB and build instrument map."""
        from agent.db.instruments import list_instruments

        snapshot = get_latest_snapshot(ctx.db)
        if snapshot is None:
            return {
                "snapshot_id": "",
                "portfolio": {},
                "instrument_ids": [],
                "instrument_map": {},
                "candle_counts": {},
            }

        snapshot_id = str(snapshot.get("id", ""))

        # Extract instrument IDs from snapshot positions
        instrument_ids = sorted({
            pos.get("instrument_id", pos.get("instrumentID", 0))
            for pos in snapshot.get("positions", [])
        })

        # Build instrument map from DB
        db_instruments = list_instruments(ctx.db)
        instrument_map: dict[int, Any] = {}
        for inst in db_instruments:
            eid = inst.get("etoro_id")
            if eid is not None and int(eid) in instrument_ids:
                instrument_map[int(eid)] = inst

        # Count candles per instrument
        from agent.db.candles import count_candles

        candle_counts: dict[int, int] = {}
        for iid in instrument_ids:
            candle_counts[iid] = count_candles(ctx.db, iid, "1d")

        return {
            "snapshot_id": snapshot_id,
            "portfolio": snapshot,
            "instrument_ids": instrument_ids,
            "instrument_map": instrument_map,
            "candle_counts": candle_counts,
        }
