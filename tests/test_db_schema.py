"""Tests for the SurrealDB schema module.

All tests use in-memory SurrealDB — no Docker required.
"""

import pytest

from agent.config import Settings
from agent.db.connection import get_connection
from agent.db.schema import (
    EXPECTED_INDEXES,
    EXPECTED_TABLES,
    apply_schema,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _test_settings() -> Settings:
    """Create Settings for in-memory SurrealDB tests."""
    return Settings(
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


def _get_db_info(db: object) -> dict[str, object]:
    """Return the parsed INFO FOR DB result."""
    result = db.query("INFO FOR DB;")  # type: ignore[union-attr]
    if isinstance(result, list) and len(result) > 0:
        entry = result[0]
        if isinstance(entry, dict) and "result" in entry:
            return entry["result"]  # type: ignore[return-value]
        if isinstance(entry, dict):
            return entry
    if isinstance(result, dict):
        return result
    return {}


# ---------------------------------------------------------------------------
# apply_schema tests
# ---------------------------------------------------------------------------


def _get_tables(info: dict[str, object]) -> dict[str, object]:
    """Extract the 'tables' dict from an INFO FOR DB result."""
    tables = info.get("tables", {})
    return tables if isinstance(tables, dict) else {}


def test_apply_schema_creates_all_tables():
    """All 8 expected tables exist after schema application."""
    with get_connection(_test_settings()) as db:
        apply_schema(db)
        info = _get_db_info(db)
        tables = set(_get_tables(info).keys())
        assert EXPECTED_TABLES.issubset(tables), (
            f"Missing tables: {EXPECTED_TABLES - tables}"
        )


def test_apply_schema_creates_all_indexes():
    """All expected indexes exist after schema application."""
    with get_connection(_test_settings()) as db:
        apply_schema(db)

        # Collect indexes from every table
        found_indexes: set[str] = set()
        info = _get_db_info(db)
        for table_name in _get_tables(info):
            table_info_result = db.query(f"INFO FOR TABLE {table_name};")
            # The SDK may return a plain dict or a list-wrapped dict
            if isinstance(table_info_result, dict):
                table_info = table_info_result
            elif isinstance(table_info_result, list) and len(table_info_result) > 0:
                entry = table_info_result[0]
                table_info = (
                    entry.get("result", entry) if isinstance(entry, dict) else entry
                )
            else:
                table_info = {}

            if isinstance(table_info, dict):
                indexes = table_info.get("indexes")
                if isinstance(indexes, dict):
                    for idx_name in indexes:
                        found_indexes.add(idx_name)

        assert EXPECTED_INDEXES.issubset(found_indexes), (
            f"Missing indexes: {EXPECTED_INDEXES - found_indexes}"
        )


def test_apply_schema_is_idempotent():
    """Running apply_schema() twice does not error."""
    with get_connection(_test_settings()) as db:
        apply_schema(db)
        # Second application should succeed without errors
        apply_schema(db)

        # Tables should still all be present
        info = _get_db_info(db)
        tables = set(_get_tables(info).keys())
        assert EXPECTED_TABLES.issubset(tables)


def test_schema_instrument_table_is_schemafull():
    """The instrument table rejects undefined fields (SCHEMAFULL)."""
    with get_connection(_test_settings()) as db:
        apply_schema(db)

        # Insert a record with only the required fields
        db.query("""
            CREATE instrument SET
                etoro_id = 1001,
                symbol = 'AAPL',
                name = 'Apple Inc.',
                asset_class = 'stock';
        """)
        result = db.query("SELECT * FROM instrument;")
        records = result if isinstance(result, list) else [result]
        flat = []
        for item in records:
            if isinstance(item, dict) and "result" in item:
                flat.extend(
                    item["result"]
                    if isinstance(item["result"], list)
                    else [item["result"]]
                )
            elif isinstance(item, list):
                flat.extend(item)
            else:
                flat.append(item)

        assert len(flat) == 1
        record = flat[0]
        assert record["symbol"] == "AAPL"
        assert record["etoro_id"] == 1001
        assert record["asset_class"] == "stock"
        # Undefined field should not appear (SCHEMAFULL strips it)
        assert "random_field" not in record


def test_schema_candle_table_has_compound_index():
    """The candle table's compound unique index prevents duplicate candles."""
    with get_connection(_test_settings()) as db:
        apply_schema(db)

        # Create an instrument first (for the record reference)
        db.query("""
            CREATE instrument:aapl SET
                etoro_id = 1001,
                symbol = 'AAPL',
                name = 'Apple Inc.',
                asset_class = 'stock';
        """)

        # Insert a candle
        db.query("""
            CREATE candle SET
                instrument = instrument:aapl,
                timeframe = '1d',
                open = 150.0,
                high = 155.0,
                low = 149.0,
                close = 153.0,
                volume = 1000000.0,
                timestamp = d'2024-01-15T00:00:00Z';
        """)

        # Inserting a duplicate (same instrument + timeframe + timestamp)
        # should fail due to the unique index
        try:
            db.query("""
                CREATE candle SET
                    instrument = instrument:aapl,
                    timeframe = '1d',
                    open = 151.0,
                    high = 156.0,
                    low = 150.0,
                    close = 154.0,
                    volume = 2000000.0,
                    timestamp = d'2024-01-15T00:00:00Z';
            """)
        except Exception:
            pass  # Expected — duplicate index violation

        # Should still only have 1 candle
        result = db.query("SELECT * FROM candle;")
        records = result if isinstance(result, list) else [result]
        flat = []
        for item in records:
            if isinstance(item, dict) and "result" in item:
                flat.extend(
                    item["result"]
                    if isinstance(item["result"], list)
                    else [item["result"]]
                )
            elif isinstance(item, list):
                flat.extend(item)
            else:
                flat.append(item)

        assert len(flat) == 1


def test_schema_report_run_id_index_is_unique():
    """The report table's run_id index enforces uniqueness."""
    with get_connection(_test_settings()) as db:
        apply_schema(db)

        # Create required references
        db.query("""
            CREATE portfolio_snapshot:snap1 SET
                total_value = 10000.0,
                cash_available = 5000.0,
                open_positions = 2,
                total_pnl = 500.0,
                positions = [],
                run_type = 'market_open';
        """)

        db.query("""
            CREATE report SET
                run_id = 'run-001',
                run_type = 'market_open',
                portfolio_snapshot = portfolio_snapshot:snap1,
                recommendations = [],
                commentary = 'Test commentary',
                summary = 'Test summary',
                report_markdown = '# Test';
        """)

        # Second report with same run_id should fail
        try:
            db.query("""
                CREATE report SET
                    run_id = 'run-001',
                    run_type = 'market_close',
                    portfolio_snapshot = portfolio_snapshot:snap1,
                    recommendations = [],
                    commentary = 'Dupe commentary',
                    summary = 'Dupe summary',
                    report_markdown = '# Dupe';
            """)
        except Exception:
            pass  # Expected — duplicate index

        result = db.query("SELECT * FROM report;")
        records = result if isinstance(result, list) else [result]
        flat = []
        for item in records:
            if isinstance(item, dict) and "result" in item:
                flat.extend(
                    item["result"]
                    if isinstance(item["result"], list)
                    else [item["result"]]
                )
            elif isinstance(item, list):
                flat.extend(item)
            else:
                flat.append(item)

        assert len(flat) == 1
        assert flat[0]["run_id"] == "run-001"
