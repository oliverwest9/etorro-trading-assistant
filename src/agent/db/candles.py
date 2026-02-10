"""OHLCV candle CRUD operations against SurrealDB.

Candles are stored with auto-generated record IDs because each candle is
uniquely identified by the compound index ``(instrument, timeframe, timestamp)``
rather than a single business key.

The ``bulk_insert_candles`` function uses SurrealQL ``INSERT INTO`` which
silently skips rows that violate the unique compound index — perfect for
idempotent daily ingestion where some candles may already exist.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import structlog
from surrealdb.connections.sync_template import SyncTemplate

from agent.db.utils import first_or_none, normalise_response
from agent.etoro.models import Candle

logger = structlog.get_logger(__name__)


def bulk_insert_candles(
    db: SyncTemplate,
    candles: list[Candle],
    instrument_etoro_id: int,
    timeframe: str,
) -> list[dict[str, Any]]:
    """Insert candles in bulk, silently skipping duplicates.

    Uses SurrealQL ``INSERT INTO`` which silently skips records that
    would violate the ``idx_candle_lookup`` unique compound index.

    Args:
        db: An open SurrealDB connection.
        candles: eToro ``Candle`` models to insert.
        instrument_etoro_id: The eToro instrument ID for the FK reference.
        timeframe: The candle period (e.g. ``"1d"``).

    Returns:
        List of successfully inserted record dicts (excludes skipped dupes).
    """
    if not candles:
        return []

    logger.debug(
        "candles_bulk_insert",
        instrument_etoro_id=instrument_etoro_id,
        timeframe=timeframe,
        count=len(candles),
    )

    # Build a list of row objects for a single bulk INSERT.
    rows: list[dict[str, Any]] = []
    for candle in candles:
        rows.append(
            {
                "instrument": f"instrument:{instrument_etoro_id}",
                "timeframe": timeframe,
                "open": candle.open,
                "high": candle.high,
                "low": candle.low,
                "close": candle.close,
                "volume": candle.volume,
                "timestamp": candle.timestamp,
            }
        )

    result = db.query("INSERT INTO candle $data;", {"data": rows})
    inserted: list[dict[str, Any]] = normalise_response(result)
    return inserted


def query_candles(
    db: SyncTemplate,
    instrument_etoro_id: int,
    timeframe: str,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[dict[str, Any]]:
    """Query candles for a given instrument and timeframe.

    Optionally filters by a ``[start, end]`` date range (inclusive).

    Args:
        db: An open SurrealDB connection.
        instrument_etoro_id: The eToro instrument ID.
        timeframe: The candle period (e.g. ``"1d"``).
        start: Inclusive lower bound for ``timestamp`` (optional).
        end: Inclusive upper bound for ``timestamp`` (optional).

    Returns:
        A list of candle record dicts ordered by timestamp ascending.
    """
    params: dict[str, Any] = {
        "etoro_id": instrument_etoro_id,
        "timeframe": timeframe,
    }

    sql = (
        "SELECT * FROM candle "
        "WHERE instrument = type::thing('instrument', $etoro_id) "
        "AND timeframe = $timeframe"
    )

    if start is not None:
        sql += " AND timestamp >= <datetime>$start"
        params["start"] = start.isoformat()

    if end is not None:
        sql += " AND timestamp <= <datetime>$end"
        params["end"] = end.isoformat()

    sql += " ORDER BY timestamp ASC;"

    result = db.query(sql, params)
    return normalise_response(result)


def count_candles(
    db: SyncTemplate,
    instrument_etoro_id: int,
    timeframe: str,
) -> int:
    """Return the number of stored candles for an instrument + timeframe.

    Args:
        db: An open SurrealDB connection.
        instrument_etoro_id: The eToro instrument ID.
        timeframe: The candle period (e.g. ``"1d"``).

    Returns:
        Integer count of matching candle records.
    """
    result = db.query(
        "SELECT count() AS total FROM candle "
        "WHERE instrument = type::thing('instrument', $etoro_id) "
        "AND timeframe = $timeframe "
        "GROUP ALL;",
        {"etoro_id": instrument_etoro_id, "timeframe": timeframe},
    )
    rows = normalise_response(result)
    if rows and isinstance(rows[0], dict):
        return int(rows[0].get("total", 0))
    return 0
