"""SurrealDB connection and schema management."""

from agent.db.connection import get_connection
from agent.db.schema import (
    EXPECTED_INDEXES,
    EXPECTED_TABLES,
    SCHEMA,
    apply_schema,
)

__all__ = [
    "get_connection",
    "apply_schema",
    "SCHEMA",
    "EXPECTED_TABLES",
    "EXPECTED_INDEXES",
]
