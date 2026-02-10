"""Tests for db/candles.py — candle CRUD against in-memory SurrealDB."""

from __future__ import annotations

from datetime import datetime, timezone

from surrealdb.connections.sync_template import SyncTemplate

from agent.db.candles import bulk_insert_candles, count_candles, query_candles
from agent.db.instruments import upsert_instrument
from agent.etoro.models import Candle, Instrument


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ETORO_ID = 1001


def _seed_instrument(db: SyncTemplate) -> None:
    """Create the instrument record that candles reference (FK)."""
    inst = Instrument.model_validate(
        {
            "instrumentID": ETORO_ID,
            "symbolFull": "AAPL",
            "instrumentDisplayName": "Apple Inc.",
            "instrumentTypeID": 5,
            "exchangeID": 1,
        }
    )
    upsert_instrument(db, inst)


def _make_candle(
    day: int = 15,
    open_: float = 150.0,
    high: float = 155.0,
    low: float = 149.0,
    close: float = 153.0,
    volume: float = 1_000_000.0,
) -> Candle:
    """Create a Candle model for 2024-01-{day}."""
    return Candle.model_validate(
        {
            "instrumentID": ETORO_ID,
            "fromDate": datetime(2024, 1, day, tzinfo=timezone.utc).isoformat(),
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }
    )


# ---------------------------------------------------------------------------
# bulk_insert_candles
# ---------------------------------------------------------------------------


def test_bulk_insert_candles_creates_records(db: SyncTemplate) -> None:
    """Inserting candles creates records in the database."""
    _seed_instrument(db)
    candles = [_make_candle(day=15), _make_candle(day=16), _make_candle(day=17)]

    result = bulk_insert_candles(db, candles, ETORO_ID, "1d")
    assert len(result) == 3


def test_bulk_insert_candles_empty_list(db: SyncTemplate) -> None:
    """Inserting an empty list returns an empty list."""
    result = bulk_insert_candles(db, [], ETORO_ID, "1d")
    assert result == []


def test_bulk_insert_candles_skips_duplicates(db: SyncTemplate) -> None:
    """Re-inserting the same candles does not create duplicates.

    The ``idx_candle_lookup`` unique index on (instrument, timeframe, timestamp)
    causes ``db.insert()`` to silently skip duplicates.
    """
    _seed_instrument(db)
    candle = _make_candle(day=15)

    # First insert
    bulk_insert_candles(db, [candle], ETORO_ID, "1d")
    assert count_candles(db, ETORO_ID, "1d") == 1

    # Second insert — same data, should be silently skipped
    bulk_insert_candles(db, [candle], ETORO_ID, "1d")
    assert count_candles(db, ETORO_ID, "1d") == 1


def test_bulk_insert_candles_mixed_new_and_duplicate(db: SyncTemplate) -> None:
    """A batch with some new and some duplicate candles inserts only the new ones."""
    _seed_instrument(db)

    # Insert first candle
    bulk_insert_candles(db, [_make_candle(day=15)], ETORO_ID, "1d")
    assert count_candles(db, ETORO_ID, "1d") == 1

    # Insert batch with the duplicate + two new ones
    mixed = [_make_candle(day=15), _make_candle(day=16), _make_candle(day=17)]
    bulk_insert_candles(db, mixed, ETORO_ID, "1d")
    assert count_candles(db, ETORO_ID, "1d") == 3


# ---------------------------------------------------------------------------
# query_candles
# ---------------------------------------------------------------------------


def test_query_candles_returns_all_for_instrument(db: SyncTemplate) -> None:
    """Query without date range returns all candles for the instrument."""
    _seed_instrument(db)
    bulk_insert_candles(
        db,
        [_make_candle(day=15), _make_candle(day=16), _make_candle(day=17)],
        ETORO_ID,
        "1d",
    )

    result = query_candles(db, ETORO_ID, "1d")
    assert len(result) == 3


def test_query_candles_filters_by_date_range(db: SyncTemplate) -> None:
    """Query with start/end filters candles by timestamp."""
    _seed_instrument(db)
    bulk_insert_candles(
        db,
        [_make_candle(day=d) for d in (10, 15, 20, 25)],
        ETORO_ID,
        "1d",
    )

    result = query_candles(
        db,
        ETORO_ID,
        "1d",
        start=datetime(2024, 1, 14, tzinfo=timezone.utc),
        end=datetime(2024, 1, 21, tzinfo=timezone.utc),
    )
    assert len(result) == 2  # day 15 and day 20


def test_query_candles_ordered_by_timestamp(db: SyncTemplate) -> None:
    """Results are ordered by timestamp ascending."""
    _seed_instrument(db)
    # Insert in reverse order
    bulk_insert_candles(
        db,
        [_make_candle(day=20), _make_candle(day=10), _make_candle(day=15)],
        ETORO_ID,
        "1d",
    )

    result = query_candles(db, ETORO_ID, "1d")
    timestamps = [r["timestamp"] for r in result]
    assert timestamps == sorted(timestamps)


def test_query_candles_empty_result(db: SyncTemplate) -> None:
    """Query with no matching candles returns an empty list."""
    _seed_instrument(db)
    result = query_candles(db, ETORO_ID, "1d")
    assert result == []


def test_query_candles_filters_by_timeframe(db: SyncTemplate) -> None:
    """Candles with different timeframes are separated by query."""
    _seed_instrument(db)
    bulk_insert_candles(db, [_make_candle(day=15)], ETORO_ID, "1d")
    bulk_insert_candles(db, [_make_candle(day=15)], ETORO_ID, "1w")

    daily = query_candles(db, ETORO_ID, "1d")
    weekly = query_candles(db, ETORO_ID, "1w")
    assert len(daily) == 1
    assert len(weekly) == 1


# ---------------------------------------------------------------------------
# count_candles
# ---------------------------------------------------------------------------


def test_count_candles_zero(db: SyncTemplate) -> None:
    """Count is 0 when no candles exist."""
    _seed_instrument(db)
    assert count_candles(db, ETORO_ID, "1d") == 0


def test_count_candles_correct(db: SyncTemplate) -> None:
    """Count matches the number of inserted candles."""
    _seed_instrument(db)
    bulk_insert_candles(
        db,
        [_make_candle(day=d) for d in (10, 15, 20)],
        ETORO_ID,
        "1d",
    )
    assert count_candles(db, ETORO_ID, "1d") == 3
