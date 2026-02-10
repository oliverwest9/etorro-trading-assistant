"""Tests for db/instruments.py — instrument CRUD against in-memory SurrealDB."""

from __future__ import annotations

from surrealdb.connections.sync_template import SyncTemplate

from agent.db.instruments import (
    get_instrument_by_etoro_id,
    get_instrument_by_symbol,
    list_instruments,
    upsert_instrument,
    upsert_instruments,
)
from agent.etoro.models import Instrument


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_instrument(
    instrument_id: int = 1001,
    symbol: str = "AAPL",
    name: str = "Apple Inc.",
    instrument_type_id: int = 5,
    exchange_id: int | None = 1,
) -> Instrument:
    """Create a minimal Instrument model for testing."""
    return Instrument.model_validate(
        {
            "instrumentID": instrument_id,
            "symbolFull": symbol,
            "instrumentDisplayName": name,
            "instrumentTypeID": instrument_type_id,
            "exchangeID": exchange_id,
        }
    )


# ---------------------------------------------------------------------------
# upsert_instrument
# ---------------------------------------------------------------------------


def test_upsert_instrument_creates_record(db: SyncTemplate) -> None:
    """A new instrument is created via upsert."""
    inst = _make_instrument()
    result = upsert_instrument(db, inst)

    assert result["symbol"] == "AAPL"
    assert result["etoro_id"] == 1001
    assert result["asset_class"] == "Stocks"


def test_upsert_instrument_updates_existing(db: SyncTemplate) -> None:
    """Upserting the same instrument_id overwrites all fields."""
    inst_v1 = _make_instrument(name="Apple Inc.")
    upsert_instrument(db, inst_v1)

    inst_v2 = _make_instrument(name="Apple Corporation")
    result = upsert_instrument(db, inst_v2)

    assert result["name"] == "Apple Corporation"

    # Only one record should exist
    all_instruments = list_instruments(db)
    assert len(all_instruments) == 1


def test_upsert_instrument_different_ids(db: SyncTemplate) -> None:
    """Upserting two instruments with different IDs creates two records."""
    upsert_instrument(db, _make_instrument(instrument_id=1001, symbol="AAPL"))
    upsert_instrument(db, _make_instrument(instrument_id=1002, symbol="MSFT", name="Microsoft Corp."))

    all_instruments = list_instruments(db)
    assert len(all_instruments) == 2


# ---------------------------------------------------------------------------
# upsert_instruments (batch)
# ---------------------------------------------------------------------------


def test_upsert_instruments_batch(db: SyncTemplate) -> None:
    """Batch upsert creates multiple records."""
    instruments = [
        _make_instrument(instrument_id=1001, symbol="AAPL"),
        _make_instrument(instrument_id=1002, symbol="MSFT", name="Microsoft Corp."),
        _make_instrument(instrument_id=1003, symbol="TSLA", name="Tesla Inc."),
    ]
    results = upsert_instruments(db, instruments)

    assert len(results) == 3
    assert len(list_instruments(db)) == 3


# ---------------------------------------------------------------------------
# get_instrument_by_symbol
# ---------------------------------------------------------------------------


def test_get_instrument_by_symbol_found(db: SyncTemplate) -> None:
    """Returns the instrument when the symbol exists."""
    upsert_instrument(db, _make_instrument())

    result = get_instrument_by_symbol(db, "AAPL")
    assert result is not None
    assert result["symbol"] == "AAPL"
    assert result["etoro_id"] == 1001


def test_get_instrument_by_symbol_not_found(db: SyncTemplate) -> None:
    """Returns None when the symbol does not exist."""
    result = get_instrument_by_symbol(db, "NONEXISTENT")
    assert result is None


# ---------------------------------------------------------------------------
# get_instrument_by_etoro_id
# ---------------------------------------------------------------------------


def test_get_instrument_by_etoro_id_found(db: SyncTemplate) -> None:
    """Returns the instrument when the eToro ID exists."""
    upsert_instrument(db, _make_instrument())

    result = get_instrument_by_etoro_id(db, 1001)
    assert result is not None
    assert result["etoro_id"] == 1001
    assert result["symbol"] == "AAPL"


def test_get_instrument_by_etoro_id_not_found(db: SyncTemplate) -> None:
    """Returns None when the eToro ID does not exist."""
    result = get_instrument_by_etoro_id(db, 9999)
    assert result is None


# ---------------------------------------------------------------------------
# list_instruments
# ---------------------------------------------------------------------------


def test_list_instruments_empty(db: SyncTemplate) -> None:
    """Returns an empty list when no instruments exist."""
    assert list_instruments(db) == []


def test_list_instruments_returns_all(db: SyncTemplate) -> None:
    """Returns all instruments in the database."""
    upsert_instrument(db, _make_instrument(instrument_id=1001, symbol="AAPL"))
    upsert_instrument(db, _make_instrument(instrument_id=1002, symbol="MSFT", name="Microsoft"))

    instruments = list_instruments(db)
    assert len(instruments) == 2
    symbols = {i["symbol"] for i in instruments}
    assert symbols == {"AAPL", "MSFT"}


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_instrument_exchange_id_none_stored_as_none(db: SyncTemplate) -> None:
    """An instrument with no exchange_id stores None for exchange."""
    inst = _make_instrument(exchange_id=None)
    upsert_instrument(db, inst)

    result = get_instrument_by_etoro_id(db, 1001)
    assert result is not None
    assert result["exchange"] is None


def test_instrument_asset_class_mapping(db: SyncTemplate) -> None:
    """Different instrument_type_ids produce different asset_class values."""
    upsert_instrument(db, _make_instrument(instrument_id=1, symbol="STK", instrument_type_id=5))
    upsert_instrument(db, _make_instrument(instrument_id=2, symbol="ETF", instrument_type_id=6))
    upsert_instrument(db, _make_instrument(instrument_id=3, symbol="BTC", instrument_type_id=10))

    stk = get_instrument_by_etoro_id(db, 1)
    etf = get_instrument_by_etoro_id(db, 2)
    btc = get_instrument_by_etoro_id(db, 3)

    assert stk is not None and stk["asset_class"] == "Stocks"
    assert etf is not None and etf["asset_class"] == "ETF"
    assert btc is not None and btc["asset_class"] == "Crypto"
