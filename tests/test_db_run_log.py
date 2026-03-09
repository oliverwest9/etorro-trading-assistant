"""Tests for db/run_log.py — run lifecycle CRUD against in-memory SurrealDB."""

from __future__ import annotations

from surrealdb.connections.sync_template import SyncTemplate

from agent.db.run_log import (
    complete_run_log,
    create_run_log,
    fail_run_log,
    get_run_log,
    query_run_logs,
)


# ---------------------------------------------------------------------------
# create_run_log
# ---------------------------------------------------------------------------


def test_create_run_log(db: SyncTemplate) -> None:
    """A run_log is created with status 'started'."""
    result = create_run_log(db, run_id="run-001", run_type="market_open")

    assert result["run_id"] == "run-001"
    assert result["run_type"] == "market_open"
    assert result["status"] == "started"
    assert result["instruments_analysed"] == 0
    assert result["recommendations_made"] == 0


def test_create_run_log_has_started_at(db: SyncTemplate) -> None:
    """The created record includes a started_at timestamp."""
    result = create_run_log(db, run_id="run-002", run_type="market_close")

    assert "started_at" in result
    assert result["started_at"] is not None


# ---------------------------------------------------------------------------
# complete_run_log
# ---------------------------------------------------------------------------


def test_complete_run_log(db: SyncTemplate) -> None:
    """Completing a run sets status, counts, duration, and completed_at."""
    create_run_log(db, run_id="run-010", run_type="market_open")

    updated = complete_run_log(
        db,
        run_id="run-010",
        instruments_analysed=5,
        recommendations_made=3,
        duration_ms=1234,
    )

    assert updated is not None
    assert updated["status"] == "completed"
    assert updated["instruments_analysed"] == 5
    assert updated["recommendations_made"] == 3
    assert updated["duration_ms"] == 1234
    assert updated["completed_at"] is not None


def test_complete_run_log_not_found(db: SyncTemplate) -> None:
    """Completing a nonexistent run returns None."""
    result = complete_run_log(
        db,
        run_id="nonexistent",
        instruments_analysed=0,
        recommendations_made=0,
        duration_ms=100,
    )
    assert result is None


# ---------------------------------------------------------------------------
# fail_run_log
# ---------------------------------------------------------------------------


def test_fail_run_log(db: SyncTemplate) -> None:
    """Failing a run sets status='failed', stores errors, and sets completed_at."""
    create_run_log(db, run_id="run-020", run_type="market_close")

    errors = [{"step": "portfolio", "error": "API timeout"}]
    updated = fail_run_log(
        db,
        run_id="run-020",
        errors=errors,
        duration_ms=500,
    )

    assert updated is not None
    assert updated["status"] == "failed"
    assert updated["duration_ms"] == 500
    assert updated["completed_at"] is not None
    assert len(updated["errors"]) == 1
    assert updated["errors"][0]["step"] == "portfolio"


def test_fail_run_log_not_found(db: SyncTemplate) -> None:
    """Failing a nonexistent run returns None."""
    result = fail_run_log(
        db,
        run_id="nonexistent",
        errors=[],
        duration_ms=100,
    )
    assert result is None


# ---------------------------------------------------------------------------
# get_run_log
# ---------------------------------------------------------------------------


def test_get_run_log_by_run_id(db: SyncTemplate) -> None:
    """Fetch a run_log record by run_id."""
    create_run_log(db, run_id="run-030", run_type="market_open")

    result = get_run_log(db, "run-030")

    assert result is not None
    assert result["run_id"] == "run-030"


def test_get_run_log_not_found(db: SyncTemplate) -> None:
    """Returns None when run_id does not exist."""
    result = get_run_log(db, "nonexistent")
    assert result is None


# ---------------------------------------------------------------------------
# query_run_logs
# ---------------------------------------------------------------------------


def test_query_run_logs_returns_all(db: SyncTemplate) -> None:
    """Returns all run_log records when under the limit."""
    create_run_log(db, run_id="run-a", run_type="market_open")
    create_run_log(db, run_id="run-b", run_type="market_close")

    results = query_run_logs(db)

    assert len(results) == 2


def test_query_run_logs_respects_limit(db: SyncTemplate) -> None:
    """The limit parameter caps results."""
    for i in range(5):
        create_run_log(db, run_id=f"run-{i}", run_type="market_open")

    results = query_run_logs(db, limit=3)
    assert len(results) == 3


def test_query_run_logs_empty(db: SyncTemplate) -> None:
    """Returns empty list when no run_logs exist."""
    results = query_run_logs(db)
    assert results == []


# ---------------------------------------------------------------------------
# Lifecycle: started → completed
# ---------------------------------------------------------------------------


def test_full_lifecycle_started_to_completed(db: SyncTemplate) -> None:
    """A run transitions from started → completed correctly."""
    create_run_log(db, run_id="run-lc1", run_type="market_open")

    record = get_run_log(db, "run-lc1")
    assert record is not None
    assert record["status"] == "started"

    complete_run_log(
        db,
        run_id="run-lc1",
        instruments_analysed=10,
        recommendations_made=8,
        duration_ms=5000,
    )

    record = get_run_log(db, "run-lc1")
    assert record is not None
    assert record["status"] == "completed"
    assert record["instruments_analysed"] == 10


# ---------------------------------------------------------------------------
# Lifecycle: started → failed
# ---------------------------------------------------------------------------


def test_full_lifecycle_started_to_failed(db: SyncTemplate) -> None:
    """A run transitions from started → failed correctly."""
    create_run_log(db, run_id="run-lc2", run_type="market_close")

    fail_run_log(
        db,
        run_id="run-lc2",
        errors=[{"step": "fetch", "error": "timeout"}],
        duration_ms=200,
    )

    record = get_run_log(db, "run-lc2")
    assert record is not None
    assert record["status"] == "failed"
    assert len(record["errors"]) == 1
