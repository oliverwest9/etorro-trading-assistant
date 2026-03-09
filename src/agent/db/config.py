"""Configuration CRUD operations against the SurrealDB ``config`` table.

The ``config`` table stores runtime configuration as key/value pairs.
The ``value`` field is a JSON object, allowing arbitrary structured data
for each key.  Typical keys include ``tracked_instruments`` and
``llm_prompt``.
"""

from __future__ import annotations

from typing import Any

import structlog
from surrealdb.connections.sync_template import SyncTemplate

from agent.db.utils import first_or_none, normalise_response

logger = structlog.get_logger(__name__)


def get_config(
    db: SyncTemplate,
    key: str,
) -> dict[str, Any] | None:
    """Retrieve a configuration value by key.

    Args:
        db: An open SurrealDB connection.
        key: The configuration key to look up.

    Returns:
        The ``value`` object (dict) for the key, or ``None`` if not found.
    """
    result = normalise_response(
        db.query(
            "SELECT * FROM config WHERE key = $key LIMIT 1",
            {"key": key},
        )
    )
    record = first_or_none(result)
    if record is None:
        return None
    return record.get("value")


def set_config(
    db: SyncTemplate,
    key: str,
    value: dict[str, Any],
) -> dict[str, Any]:
    """Create or update a configuration value.

    Uses an UPSERT-style query: if a record with the given key exists
    it is updated; otherwise a new record is created.

    Args:
        db: An open SurrealDB connection.
        key: The configuration key.
        value: A dict to store as the value.

    Returns:
        The created/updated config record dict.
    """
    # Delete any existing record with this key, then create a new one
    db.query("DELETE FROM config WHERE key = $key", {"key": key})
    result = normalise_response(
        db.query(
            "CREATE config SET key = $key, value = $value, updated_at = time::now()",
            {"key": key, "value": value},
        )
    )
    record = first_or_none(result)
    if record is None:
        raise RuntimeError(f"Failed to set config key '{key}' in SurrealDB")
    logger.debug("config_set", key=key)
    return record


def delete_config(
    db: SyncTemplate,
    key: str,
) -> bool:
    """Delete a configuration key.

    Args:
        db: An open SurrealDB connection.
        key: The configuration key to remove.

    Returns:
        ``True`` if a record was deleted, ``False`` if the key didn't exist.
    """
    existing = get_config(db, key)
    if existing is None:
        return False
    db.query("DELETE FROM config WHERE key = $key", {"key": key})
    logger.debug("config_deleted", key=key)
    return True
