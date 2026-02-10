"""Instrument CRUD operations against SurrealDB.

Instruments are keyed by their eToro ``instrument_id`` so that
``instrument:1001`` maps directly to eToro ID 1001.  This makes foreign-key
references from candles and recommendations straightforward.

All public functions accept a ready-to-use ``SyncTemplate`` handle obtained
from ``get_connection()``.
"""

from __future__ import annotations

from typing import Any

import structlog
from surrealdb import RecordID
from surrealdb.connections.sync_template import SyncTemplate

from agent.db.utils import first_or_none, normalise_response
from agent.etoro.models import Instrument

logger = structlog.get_logger(__name__)

# Fields defined as ``option<T>`` in the schema that the SDK may omit from
# results when their value is NONE.  We normalise them to explicit ``None``
# so callers can reliably use ``record["field"]`` without KeyError.
_OPTIONAL_FIELDS: dict[str, object] = {
    "exchange": None,
    "industry": None,
    "metadata": None,
}


def _normalise_instrument(record: dict[str, Any] | None) -> dict[str, Any] | None:
    """Ensure optional fields are present (defaulting to ``None``)."""
    if record is None:
        return None
    for field, default in _OPTIONAL_FIELDS.items():
        record.setdefault(field, default)
    return record


def _instrument_to_record(instrument: Instrument) -> dict[str, Any]:
    """Map an eToro ``Instrument`` model to a SurrealDB record dict.

    The ``exchange_id`` (int | None) is stored as a string in the
    ``exchange`` field because the DB schema expects ``option<string>``.
    """
    return {
        "etoro_id": instrument.instrument_id,
        "symbol": instrument.symbol,
        "name": instrument.name,
        "asset_class": instrument.asset_class,
        "exchange": str(instrument.exchange_id) if instrument.exchange_id is not None else None,
        "is_active": True,
    }


def upsert_instrument(db: SyncTemplate, instrument: Instrument) -> dict[str, Any]:
    """Insert or fully replace an instrument record.

    The record ID is ``instrument:<etoro_id>`` so that repeated calls for the
    same instrument overwrite the previous data.

    Args:
        db: An open SurrealDB connection.
        instrument: An eToro ``Instrument`` Pydantic model.

    Returns:
        The upserted record as a ``dict``.
    """
    record_id = RecordID("instrument", instrument.instrument_id)
    data = _instrument_to_record(instrument)

    logger.debug("instrument_upsert", etoro_id=instrument.instrument_id, symbol=instrument.symbol)
    result = db.upsert(record_id, data)
    record = first_or_none(result) or data
    return _normalise_instrument(record) or record


def upsert_instruments(db: SyncTemplate, instruments: list[Instrument]) -> list[dict[str, Any]]:
    """Upsert a batch of instruments.

    Args:
        db: An open SurrealDB connection.
        instruments: List of eToro ``Instrument`` models.

    Returns:
        List of upserted record dicts.
    """
    results: list[dict[str, Any]] = []
    for inst in instruments:
        results.append(upsert_instrument(db, inst))
    return results


def get_instrument_by_symbol(db: SyncTemplate, symbol: str) -> dict[str, Any] | None:
    """Look up an instrument by its ticker symbol.

    Args:
        db: An open SurrealDB connection.
        symbol: The ticker symbol (e.g. ``"AAPL"``).

    Returns:
        The instrument record dict or ``None`` if not found.
    """
    result = db.query(
        "SELECT * FROM instrument WHERE symbol = $symbol LIMIT 1;",
        {"symbol": symbol},
    )
    return _normalise_instrument(first_or_none(result))


def get_instrument_by_etoro_id(db: SyncTemplate, etoro_id: int) -> dict[str, Any] | None:
    """Look up an instrument by its eToro instrument ID.

    Uses a direct record select since the record ID **is** the eToro ID.

    Args:
        db: An open SurrealDB connection.
        etoro_id: The eToro instrument ID (e.g. ``1001``).

    Returns:
        The instrument record dict or ``None`` if not found.
    """
    record_id = RecordID("instrument", etoro_id)
    result = db.select(record_id)
    return _normalise_instrument(first_or_none(result))


def list_instruments(db: SyncTemplate) -> list[dict[str, Any]]:
    """Return all instrument records.

    Args:
        db: An open SurrealDB connection.

    Returns:
        A list of instrument record dicts (may be empty).
    """
    from surrealdb import Table

    result = db.select(Table("instrument"))
    records = normalise_response(result)
    return [_normalise_instrument(r) or r for r in records]
