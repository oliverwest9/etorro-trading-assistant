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
    get_previous_report,
    get_recommendations_for_report,
    get_report_by_run_id,
    query_reports,
)
from agent.db.analysis import (
    create_analysis,
    get_analyses_by_run_id,
    get_analysis_for_instrument,
)
from agent.db.run_log import (
    complete_run_log,
    create_run_log,
    fail_run_log,
    get_run_log,
    query_run_logs,
)
from agent.db.config import (
    delete_config,
    get_config,
    set_config,
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
    "get_previous_report",
    "get_recommendations_for_report",
    "get_report_by_run_id",
    "query_reports",
    # Analysis
    "create_analysis",
    "get_analyses_by_run_id",
    "get_analysis_for_instrument",
    # Run log
    "complete_run_log",
    "create_run_log",
    "fail_run_log",
    "get_run_log",
    "query_run_logs",
    # Config
    "delete_config",
    "get_config",
    "set_config",
]
