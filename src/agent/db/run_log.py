"""Run-log CRUD operations against SurrealDB.

Each agent run creates a ``run_log`` record that tracks its lifecycle:

* **started** — written at the very beginning of the pipeline
* **completed** — written when the pipeline finishes successfully
* **failed** — written when the pipeline encounters a fatal error

The record also captures timing (``duration_ms``), counts
(``instruments_analysed``, ``recommendations_made``), and any errors
that occurred during the run.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import structlog
from surrealdb.connections.sync_template import SyncTemplate

from agent.db.utils import first_or_none, normalise_response

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


def create_run_log(
    db: SyncTemplate,
    *,
    run_id: str,
    run_type: str,
) -> dict[str, Any]:
    """Insert a new run_log record with ``status='started'``.

    Args:
        db: An open SurrealDB connection.
        run_id: Unique run identifier (UUID string).
        run_type: ``"market_open"`` or ``"market_close"``.

    Returns:
        The created run_log record dict.
    """
    data = {
        "run_id": run_id,
        "run_type": run_type,
        "status": "started",
        "instruments_analysed": 0,
        "recommendations_made": 0,
    }
    result = db.create("run_log", data)
    created = first_or_none(result)
    if created is None:
        raise RuntimeError("Failed to create run_log record in SurrealDB")
    logger.debug("run_log_created", run_id=run_id, status="started")
    return created


# ---------------------------------------------------------------------------
# Update helpers
# ---------------------------------------------------------------------------


def complete_run_log(
    db: SyncTemplate,
    *,
    run_id: str,
    instruments_analysed: int,
    recommendations_made: int,
    duration_ms: int,
) -> dict[str, Any] | None:
    """Mark a run as completed.

    Args:
        db: An open SurrealDB connection.
        run_id: The run identifier to update.
        instruments_analysed: Number of instruments analysed this run.
        recommendations_made: Number of recommendations generated.
        duration_ms: Wall-clock duration of the run in milliseconds.

    Returns:
        The updated run_log record, or ``None`` if not found.
    """
    result = normalise_response(
        db.query(
            "UPDATE run_log SET "
            "  status = $status, "
            "  instruments_analysed = $instruments_analysed, "
            "  recommendations_made = $recommendations_made, "
            "  duration_ms = $duration_ms, "
            "  completed_at = time::now() "
            "WHERE run_id = $run_id",
            {
                "status": "completed",
                "instruments_analysed": instruments_analysed,
                "recommendations_made": recommendations_made,
                "duration_ms": duration_ms,
                "run_id": run_id,
            },
        )
    )
    updated = first_or_none(result)
    logger.debug(
        "run_log_completed",
        run_id=run_id,
        duration_ms=duration_ms,
        instruments_analysed=instruments_analysed,
    )
    return updated


def fail_run_log(
    db: SyncTemplate,
    *,
    run_id: str,
    errors: list[dict[str, Any]],
    duration_ms: int,
) -> dict[str, Any] | None:
    """Mark a run as failed.

    Args:
        db: An open SurrealDB connection.
        run_id: The run identifier to update.
        errors: List of error dicts describing what went wrong.
        duration_ms: Wall-clock duration of the run in milliseconds.

    Returns:
        The updated run_log record, or ``None`` if not found.
    """
    result = normalise_response(
        db.query(
            "UPDATE run_log SET "
            "  status = $status, "
            "  errors = $errors, "
            "  duration_ms = $duration_ms, "
            "  completed_at = time::now() "
            "WHERE run_id = $run_id",
            {
                "status": "failed",
                "errors": errors,
                "duration_ms": duration_ms,
                "run_id": run_id,
            },
        )
    )
    updated = first_or_none(result)
    logger.debug("run_log_failed", run_id=run_id, error_count=len(errors))
    return updated


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------


def get_run_log(
    db: SyncTemplate,
    run_id: str,
) -> dict[str, Any] | None:
    """Fetch a single run_log record by ``run_id``.

    Returns:
        The run_log dict, or ``None`` if not found.
    """
    result = normalise_response(
        db.query(
            "SELECT * FROM run_log WHERE run_id = $run_id LIMIT 1",
            {"run_id": run_id},
        )
    )
    return first_or_none(result)


def query_run_logs(
    db: SyncTemplate,
    *,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Return recent run_log records, newest first.

    Args:
        db: An open SurrealDB connection.
        limit: Maximum number of records to return.

    Returns:
        A list of run_log record dicts.
    """
    result = normalise_response(
        db.query(
            "SELECT * FROM run_log ORDER BY started_at DESC LIMIT $limit",
            {"limit": limit},
        )
    )
    if isinstance(result, list):
        return result
    return []
