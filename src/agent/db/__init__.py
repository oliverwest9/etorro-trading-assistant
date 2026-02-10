"""SurrealDB connection, schema, and data access layer."""

from agent.db.connection import get_connection, parse_info_result
from agent.db.schema import (
    EXPECTED_INDEXES,
    EXPECTED_TABLES,
    SCHEMA,
    apply_schema,
)
from agent.db.utils import first_or_none, normalise_response
from agent.db.instruments import (
    get_instrument_by_etoro_id,
    get_instrument_by_symbol,
    list_instruments,
    upsert_instrument,
    upsert_instruments,
)
from agent.db.candles import bulk_insert_candles, count_candles, query_candles
from agent.db.snapshots import (
    create_snapshot,
    create_snapshot_raw,
    get_latest_snapshot,
    query_snapshots,
)
from agent.db.reports import (
    create_recommendation,
    create_report,
    get_latest_report,
    get_recommendations_for_report,
    get_report_by_run_id,
    query_reports,
)

__all__ = [
    # Connection & schema
    "get_connection",
    "parse_info_result",
    "apply_schema",
    "SCHEMA",
    "EXPECTED_TABLES",
    "EXPECTED_INDEXES",
    # Utils
    "first_or_none",
    "normalise_response",
    # Instruments
    "get_instrument_by_etoro_id",
    "get_instrument_by_symbol",
    "list_instruments",
    "upsert_instrument",
    "upsert_instruments",
    # Candles
    "bulk_insert_candles",
    "count_candles",
    "query_candles",
    # Snapshots
    "create_snapshot",
    "create_snapshot_raw",
    "get_latest_snapshot",
    "query_snapshots",
    # Reports
    "create_recommendation",
    "create_report",
    "get_latest_report",
    "get_recommendations_for_report",
    "get_report_by_run_id",
    "query_reports",
]
