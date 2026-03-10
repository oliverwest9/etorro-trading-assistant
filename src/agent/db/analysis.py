"""Analysis CRUD operations against SurrealDB.

Persists per-instrument analysis results produced by the analysis engine.
Each record links to an instrument (via ``record<instrument>``) and a
``run_id`` so that all analyses from a single pipeline run can be queried
together.

Follows the same patterns as ``db/candles.py`` and ``db/reports.py``.
"""

from __future__ import annotations

from typing import Any

import structlog
from surrealdb import RecordID
from surrealdb.connections.sync_template import SyncTemplate

from agent.db.utils import first_or_none, normalise_response

logger = structlog.get_logger(__name__)


def create_analysis(
    db: SyncTemplate,
    *,
    instrument_etoro_id: int,
    run_id: str,
    trend: str,
    trend_strength: float,
    price_action: dict[str, Any],
    sector_context: dict[str, Any] | None = None,
    risk_metrics: dict[str, Any] | None = None,
    raw_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a new analysis record.

    Args:
        db: An open SurrealDB connection.
        instrument_etoro_id: eToro instrument ID for the FK.
        run_id: Unique run identifier (UUID string).
        trend: Overall trend direction (``"bullish"`` / ``"bearish"`` /
            ``"neutral"``).
        trend_strength: Trend strength from 0.0 to 1.0.
        price_action: Object with indicator details, support/resistance, etc.
        sector_context: Optional sector grouping context.
        risk_metrics: Optional per-instrument risk metrics (volatility, drawdown, etc.).
        raw_data: Arbitrary additional analysis data.

    Returns:
        The created analysis record dict.

    Raises:
        RuntimeError: If the insert fails.
    """
    data: dict[str, Any] = {
        "instrument": RecordID("instrument", instrument_etoro_id),
        "run_id": run_id,
        "trend": trend,
        "trend_strength": trend_strength,
        "price_action": price_action,
        "sector_context": sector_context or {},
        "risk_metrics": risk_metrics,
        "raw_data": raw_data or {},
    }

    logger.debug(
        "analysis_create",
        instrument_etoro_id=instrument_etoro_id,
        run_id=run_id,
        trend=trend,
    )

    result = db.create("analysis", data)
    created = first_or_none(result)
    if created is None:
        logger.error(
            "analysis_create_failed",
            instrument_etoro_id=instrument_etoro_id,
            run_id=run_id,
            raw_result=result,
        )
        raise RuntimeError(
            f"Failed to create analysis record for instrument "
            f"{instrument_etoro_id}, run {run_id}"
        )
    return created


def get_analyses_by_run_id(
    db: SyncTemplate,
    run_id: str,
) -> list[dict[str, Any]]:
    """Retrieve all analysis records for a given run.

    Args:
        db: An open SurrealDB connection.
        run_id: The unique run identifier.

    Returns:
        List of analysis record dicts, ordered by creation time.
    """
    result = db.query(
        "SELECT * FROM analysis WHERE run_id = $run_id ORDER BY created_at ASC;",
        {"run_id": run_id},
    )
    return normalise_response(result)


def get_analysis_for_instrument(
    db: SyncTemplate,
    instrument_etoro_id: int,
    run_id: str | None = None,
) -> dict[str, Any] | None:
    """Retrieve analysis for a specific instrument.

    If ``run_id`` is provided, returns the analysis from that specific
    run.  Otherwise returns the most recent analysis for the instrument.

    Args:
        db: An open SurrealDB connection.
        instrument_etoro_id: eToro instrument ID.
        run_id: Optional run ID to filter by.

    Returns:
        The analysis record dict, or ``None`` if not found.
    """
    params: dict[str, Any] = {"etoro_id": instrument_etoro_id}

    if run_id is not None:
        sql = (
            "SELECT * FROM analysis "
            "WHERE instrument = type::thing('instrument', $etoro_id) "
            "AND run_id = $run_id "
            "LIMIT 1;"
        )
        params["run_id"] = run_id
    else:
        sql = (
            "SELECT * FROM analysis "
            "WHERE instrument = type::thing('instrument', $etoro_id) "
            "ORDER BY created_at DESC LIMIT 1;"
        )

    result = db.query(sql, params)
    return first_or_none(result)
