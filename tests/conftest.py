"""Shared test fixtures for the eToro trading agent test suite."""

from __future__ import annotations

from typing import Generator

import pytest
from surrealdb.connections.sync_template import SyncTemplate

from agent.config import Settings
from agent.db.connection import get_connection
from agent.db.schema import apply_schema


def _test_settings() -> Settings:
    """Create Settings suitable for in-memory SurrealDB tests."""
    return Settings(
        etoro_api_key="test-api-key",
        etoro_user_key="test-user-key",
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


@pytest.fixture()
def test_settings() -> Settings:
    """Provide test-safe Settings for use in tests that need the config."""
    return _test_settings()


@pytest.fixture()
def db() -> Generator[SyncTemplate, None, None]:
    """Provide a fresh in-memory SurrealDB connection with schema applied.

    Each test gets a completely clean database — no leftover data from
    previous tests — because every invocation opens a brand-new
    ``memory://`` connection.
    """
    with get_connection(_test_settings()) as conn:
        apply_schema(conn)
        yield conn
