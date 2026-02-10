"""Tests for the SurrealDB connection module."""

import pytest

from agent.config import Settings
from agent.db.connection import get_connection, _is_embedded


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _test_settings(**overrides: str) -> Settings:
    """Create Settings suitable for in-memory SurrealDB tests."""
    defaults = dict(
        etoro_api_key="test",
        etoro_user_key="test",
        etoro_base_url="https://example.com",
        surreal_url="memory",
        surreal_namespace="test_ns",
        surreal_database="test_db",
        surreal_user="root",
        surreal_pass="root",
        llm_provider="openai",
        llm_api_key="test",
        llm_model="gpt-4o",
    )
    defaults.update(overrides)
    return Settings(**defaults)


# ---------------------------------------------------------------------------
# _is_embedded
# ---------------------------------------------------------------------------


def test_is_embedded_memory():
    """'memory' URL is detected as embedded."""
    assert _is_embedded("memory") is True


def test_is_embedded_mem_scheme():
    """'mem://' URL is detected as embedded."""
    assert _is_embedded("mem://") is True


def test_is_embedded_file_scheme():
    """'file://' URL is detected as embedded."""
    assert _is_embedded("file:///data/trading.db") is True


def test_is_embedded_surrealkv_scheme():
    """'surrealkv://' URL is detected as embedded."""
    assert _is_embedded("surrealkv:///data/trading.db") is True


def test_is_embedded_ws_is_false():
    """WebSocket URL is not embedded."""
    assert _is_embedded("ws://localhost:8000/rpc") is False


def test_is_embedded_http_is_false():
    """HTTP URL is not embedded."""
    assert _is_embedded("http://localhost:8000") is False


# ---------------------------------------------------------------------------
# get_connection — uses in-memory SurrealDB (no Docker required)
# ---------------------------------------------------------------------------


def test_get_connection_returns_surreal_handle():
    """get_connection() yields a usable Surreal DB handle."""
    settings = _test_settings()
    with get_connection(settings) as db:
        # Simple query to prove the connection works
        result = db.query("RETURN 1;")
        assert result is not None


def test_get_connection_selects_namespace_and_database():
    """The connection uses the namespace/database from settings."""
    settings = _test_settings(surreal_namespace="my_ns", surreal_database="my_db")
    with get_connection(settings) as db:
        # INFO FOR DB should succeed — proves the ns/db was selected
        result = db.query("INFO FOR DB;")
        assert result is not None


def test_get_connection_closes_cleanly():
    """Connection closes without error when the context manager exits."""
    settings = _test_settings()
    with get_connection(settings) as db:
        db.query("RETURN true;")
    # If we get here without exception, the connection closed cleanly


def test_get_connection_can_write_and_read():
    """Verify basic CRUD works through the connection."""
    settings = _test_settings()
    with get_connection(settings) as db:
        # Define a quick table and insert a record
        db.query("DEFINE TABLE test_conn SCHEMALESS;")
        db.query("CREATE test_conn SET value = 42;")
        result = db.query("SELECT * FROM test_conn;")
        assert result is not None
        # Result should contain our record
        records = result if isinstance(result, list) else [result]
        # Flatten — the SDK may return nested lists
        flat = []
        for item in records:
            if isinstance(item, dict) and "result" in item:
                flat.extend(item["result"] if isinstance(item["result"], list) else [item["result"]])
            elif isinstance(item, list):
                flat.extend(item)
            else:
                flat.append(item)
        assert any(r.get("value") == 42 for r in flat if isinstance(r, dict))
