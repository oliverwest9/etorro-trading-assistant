"""Tests for db/config.py — configuration CRUD against in-memory SurrealDB."""

from __future__ import annotations

from surrealdb.connections.sync_template import SyncTemplate

from agent.db.config import delete_config, get_config, set_config


# ---------------------------------------------------------------------------
# get_config
# ---------------------------------------------------------------------------


def test_get_config_returns_none_when_missing(db: SyncTemplate) -> None:
    """Returns None for a key that does not exist."""
    result = get_config(db, "nonexistent")
    assert result is None


def test_get_config_returns_value(db: SyncTemplate) -> None:
    """Returns the stored value dict for an existing key."""
    set_config(db, "test_key", {"instruments": [1001, 1002]})

    result = get_config(db, "test_key")
    assert result == {"instruments": [1001, 1002]}


# ---------------------------------------------------------------------------
# set_config
# ---------------------------------------------------------------------------


def test_set_config_creates_new_key(db: SyncTemplate) -> None:
    """Creates a new config record."""
    record = set_config(db, "my_key", {"data": "hello"})

    assert record["key"] == "my_key"
    assert record["value"] == {"data": "hello"}


def test_set_config_updates_existing_key(db: SyncTemplate) -> None:
    """Updates the value when the key already exists."""
    set_config(db, "my_key", {"version": 1})
    set_config(db, "my_key", {"version": 2})

    result = get_config(db, "my_key")
    assert result == {"version": 2}


def test_set_config_does_not_duplicate(db: SyncTemplate) -> None:
    """Setting the same key multiple times does not create duplicates."""
    set_config(db, "dup", {"a": 1})
    set_config(db, "dup", {"a": 2})
    set_config(db, "dup", {"a": 3})

    # Query all config records with this key
    from agent.db.utils import normalise_response

    result = normalise_response(
        db.query("SELECT * FROM config WHERE key = 'dup'")
    )
    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["value"] == {"a": 3}


# ---------------------------------------------------------------------------
# delete_config
# ---------------------------------------------------------------------------


def test_delete_config_existing_key(db: SyncTemplate) -> None:
    """Deleting an existing key returns True and removes it."""
    set_config(db, "del_me", {"x": 1})

    assert delete_config(db, "del_me") is True
    assert get_config(db, "del_me") is None


def test_delete_config_nonexistent_key(db: SyncTemplate) -> None:
    """Deleting a nonexistent key returns False."""
    assert delete_config(db, "nope") is False


# ---------------------------------------------------------------------------
# Multiple keys coexist
# ---------------------------------------------------------------------------


def test_multiple_keys_independent(db: SyncTemplate) -> None:
    """Different config keys are stored independently."""
    set_config(db, "key_a", {"val": "a"})
    set_config(db, "key_b", {"val": "b"})

    assert get_config(db, "key_a") == {"val": "a"}
    assert get_config(db, "key_b") == {"val": "b"}
