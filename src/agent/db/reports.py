"""Report and recommendation CRUD operations against SurrealDB.

A *report* is the main output of each agent run.  It links to a
``portfolio_snapshot`` and contains LLM-generated commentary plus a list
of ``recommendation`` records (one per actionable instrument).

Reports are keyed by ``run_id`` (unique).  Recommendations are auto-keyed
and reference both their parent report and the relevant instrument.
"""

from __future__ import annotations

from typing import Any

import structlog
from surrealdb import RecordID
from surrealdb.connections.sync_template import SyncTemplate

from agent.db.utils import first_or_none, normalise_response
from agent.types import RunType

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Report CRUD
# ---------------------------------------------------------------------------


def create_report(
    db: SyncTemplate,
    *,
    run_id: str,
    run_type: RunType,
    snapshot_id: str,
    commentary: str,
    summary: str,
    report_markdown: str,
    recommendations: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Create a new report record.

    The ``recommendations`` list stored on the report itself is an
    informational summary array.  The full recommendation records
    (with instrument FK, conviction, reasoning, analysis FK) are
    created separately via ``create_recommendation()``.

    Args:
        db: An open SurrealDB connection.
        run_id: Unique run identifier (UUID string).
        run_type: ``"market_open"`` or ``"market_close"``.
        snapshot_id: Record ID string of the portfolio snapshot
            (e.g. ``"portfolio_snapshot:abc123"``).
        commentary: LLM-generated market commentary.
        summary: One-line headline summary.
        report_markdown: Full rendered markdown report.
        recommendations: Optional summary array of recommendation dicts.

    Returns:
        The created report record dict.
    """
    data: dict[str, Any] = {
        "run_id": run_id,
        "run_type": run_type,
        "portfolio_snapshot": _to_record_id(snapshot_id),
        "recommendations": recommendations or [],
        "commentary": commentary,
        "summary": summary,
        "report_markdown": report_markdown,
    }

    logger.debug("report_create", run_id=run_id, run_type=run_type)
    result = db.create("report", data)
    created = first_or_none(result)
    if created is None:
        logger.error(
            "report_create_failed",
            run_id=run_id,
            run_type=run_type,
            raw_result=result,
        )
        raise RuntimeError("Failed to create report record in SurrealDB")
    return created


def get_report_by_run_id(db: SyncTemplate, run_id: str) -> dict[str, Any] | None:
    """Look up a report by its unique run ID.

    Args:
        db: An open SurrealDB connection.
        run_id: The unique run identifier.

    Returns:
        The report record dict, or ``None`` if not found.
    """
    result = db.query(
        "SELECT * FROM report WHERE run_id = $run_id LIMIT 1;",
        {"run_id": run_id},
    )
    return first_or_none(result)


def get_latest_report(db: SyncTemplate) -> dict[str, Any] | None:
    """Retrieve the most recent report.

    Args:
        db: An open SurrealDB connection.

    Returns:
        The latest report record dict, or ``None`` if no reports exist.
    """
    result = db.query(
        "SELECT * FROM report ORDER BY created_at DESC LIMIT 1;"
    )
    return first_or_none(result)


def query_reports(
    db: SyncTemplate,
    run_type: RunType | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Query reports, optionally filtered by run type.

    Args:
        db: An open SurrealDB connection.
        run_type: If provided, filter to only this run type.
        limit: Maximum number of results (default 50).

    Returns:
        A list of report record dicts, newest first.
    """
    params: dict[str, Any] = {"limit": limit}

    if run_type is not None:
        sql = (
            "SELECT * FROM report "
            "WHERE run_type = $run_type "
            "ORDER BY created_at DESC LIMIT $limit;"
        )
        params["run_type"] = run_type
    else:
        sql = (
            "SELECT * FROM report "
            "ORDER BY created_at DESC LIMIT $limit;"
        )

    result = db.query(sql, params)
    return normalise_response(result)


# ---------------------------------------------------------------------------
# Recommendation CRUD
# ---------------------------------------------------------------------------


def create_recommendation(
    db: SyncTemplate,
    *,
    report_id: str,
    instrument_etoro_id: int,
    action: str,
    conviction: str,
    reasoning: str,
    analysis_id: str,
) -> dict[str, Any]:
    """Create a recommendation record linked to a report and instrument.

    Args:
        db: An open SurrealDB connection.
        report_id: Record ID string of the parent report.
        instrument_etoro_id: eToro instrument ID for the FK.
        action: One of ``buy``, ``sell``, ``hold``, ``reduce``, ``increase``.
        conviction: One of ``high``, ``medium``, ``low``.
        reasoning: Free-text explanation.
        analysis_id: Record ID string of the related analysis record.

    Returns:
        The created recommendation record dict.
    """
    data: dict[str, Any] = {
        "report": _to_record_id(report_id),
        "instrument": RecordID("instrument", instrument_etoro_id),
        "action": action,
        "conviction": conviction,
        "reasoning": reasoning,
        "analysis": _to_record_id(analysis_id),
    }

    logger.debug(
        "recommendation_create",
        action=action,
        conviction=conviction,
        instrument_etoro_id=instrument_etoro_id,
    )
    result = db.create("recommendation", data)
    created = first_or_none(result)
    if not isinstance(created, dict) or "id" not in created:
        logger.error(
            "recommendation_create_failed",
            report_id=report_id,
            instrument_etoro_id=instrument_etoro_id,
            action=action,
            conviction=conviction,
            raw_result=result,
        )
        raise ValueError("Failed to create recommendation record in SurrealDB")
    return created


def get_recommendations_for_report(
    db: SyncTemplate,
    report_id: str,
) -> list[dict[str, Any]]:
    """Retrieve all recommendations belonging to a report.

    Args:
        db: An open SurrealDB connection.
        report_id: Record ID string of the report.

    Returns:
        A list of recommendation record dicts.
    """
    result = db.query(
        "SELECT * FROM recommendation WHERE report = $report;",
        {"report": _to_record_id(report_id)},
    )
    return normalise_response(result)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _to_record_id(id_str: str) -> RecordID:
    """Convert a ``"table:id"`` string into a ``RecordID``.

    The input must be in ``table:id`` format. Both the table and id parts
    must be non-empty; otherwise a ``ValueError`` is raised.

    Args:
        id_str: A record ID string like ``"report:abc123"``.

    Returns:
        A ``RecordID`` instance.

    Raises:
        ValueError: If ``id_str`` is not in ``table:id`` format or either
            component is empty.
    """
    if ":" not in id_str:
        raise ValueError(f"Invalid record id format (expected 'table:id'): {id_str!r}")

    table, key = id_str.split(":", 1)
    if not table or not key:
        raise ValueError(f"Invalid record id components in {id_str!r}: table={table!r}, id={key!r}")

    return RecordID(table, key)
