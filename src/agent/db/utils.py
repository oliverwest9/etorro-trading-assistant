"""Shared helpers for the SurrealDB data layer.

These utilities normalise the varying response shapes returned by the
SurrealDB Python SDK into predictable Python types so that every CRUD
module can rely on a single, well-tested flattening strategy.
"""

from __future__ import annotations

from typing import Any


def normalise_response(result: object) -> list[dict[str, Any]]:
    """Flatten an SDK response into a plain ``list[dict]``.

    The SDK returns different shapes depending on the method used:

    * ``select()`` → ``list[dict]``
    * ``create()`` / ``upsert()`` → ``dict`` (single record)
    * ``query()`` → sometimes ``list[dict]``, sometimes
      ``[{"result": [...]}]``
    * ``insert()`` → ``list[dict]``

    This function normalises **all** of them into ``list[dict]``.

    Args:
        result: The raw return value from any SDK method.

    Returns:
        A (possibly empty) ``list[dict]`` containing the record(s).
    """
    if result is None:
        return []

    # Unwrapped single record (create / upsert)
    if isinstance(result, dict):
        return [result]

    if isinstance(result, list):
        # Empty list — nothing to flatten
        if len(result) == 0:
            return []

        first = result[0]

        # SDK query() wrapper: [{"result": [...], "status": "OK", ...}]
        if isinstance(first, dict) and "result" in first:
            inner = first["result"]
            if isinstance(inner, list):
                return inner  # type: ignore[return-value]
            if isinstance(inner, dict):
                return [inner]
            return []

        # Already a plain list[dict] (select / insert)
        if isinstance(first, dict):
            return result  # type: ignore[return-value]

        return []

    # Fallback for completely unexpected shapes
    return []


def first_or_none(result: object) -> dict[str, Any] | None:
    """Return the first record from an SDK response, or ``None``.

    Convenient for single-record lookups (select by ID, query expecting
    at most one row, etc.).

    Args:
        result: The raw return value from any SDK method.

    Returns:
        The first record ``dict``, or ``None`` if no records are found.
    """
    records = normalise_response(result)
    return records[0] if records else None
