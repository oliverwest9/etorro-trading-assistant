#!/usr/bin/env python
"""Backfill historical daily candles for all tracked instruments.

Usage::

    python scripts/backfill_candles.py

Reads the list of instruments from the ``config`` table
(key ``tracked_instruments``).  If that config key is not set, it falls
back to all instruments already stored in the ``instrument`` table.

For each instrument, fetches daily candles from the eToro API and inserts
them into SurrealDB (duplicates are skipped automatically).
"""

from __future__ import annotations

import sys

import structlog

from agent.config import get_settings
from agent.db.candles import bulk_insert_candles
from agent.db.config import get_config
from agent.db.connection import get_connection
from agent.db.instruments import list_instruments
from agent.db.schema import apply_schema
from agent.etoro.client import EToroClient
from agent.etoro.market_data import get_candles
from agent.utils.logging import configure_logging

logger = structlog.get_logger(__name__)


def main() -> int:
    configure_logging(json=False)

    settings = get_settings()

    with get_connection(settings) as db, EToroClient(settings) as client:
        apply_schema(db)

        # Determine which instruments to backfill
        tracked = get_config(db, "tracked_instruments")
        if tracked and "ids" in tracked:
            instrument_ids: list[int] = tracked["ids"]
            logger.info("backfill_from_config", count=len(instrument_ids))
        else:
            instruments = list_instruments(db)
            instrument_ids = [
                inst.get("etoro_id") or inst.get("instrument_id")
                for inst in instruments
                if inst.get("etoro_id") or inst.get("instrument_id")
            ]
            logger.info("backfill_from_db", count=len(instrument_ids))

        if not instrument_ids:
            logger.warning("no_instruments_to_backfill")
            print("No instruments found. Run the pipeline first or set "
                  "'tracked_instruments' in the config table.")
            return 1

        total_inserted = 0
        for iid in instrument_ids:
            try:
                candles = get_candles(client, iid)
                inserted = bulk_insert_candles(db, candles, iid, "1d")
                total_inserted += len(inserted)
                logger.info(
                    "backfill_instrument",
                    instrument_id=iid,
                    fetched=len(candles),
                    inserted=len(inserted),
                )
            except Exception as exc:
                logger.error(
                    "backfill_instrument_failed",
                    instrument_id=iid,
                    error=str(exc),
                )

        print(f"\nBackfill complete: {total_inserted} candles inserted "
              f"across {len(instrument_ids)} instruments.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
