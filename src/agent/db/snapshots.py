"""Portfolio snapshot CRUD operations against SurrealDB.

Snapshots are created with auto-generated record IDs (one per run).
They capture the full portfolio state at a point in time so that
historical comparisons can be made across runs.
"""

from __future__ import annotations

from typing import Any

import structlog
from surrealdb.connections.sync_template import SyncTemplate

from agent.db.utils import first_or_none, normalise_response
from agent.etoro.models import ClientPortfolio
from agent.types import RunType

logger = structlog.get_logger(__name__)


def _portfolio_to_record(
    portfolio: ClientPortfolio,
    run_type: RunType,
) -> dict[str, Any]:
    """Map an eToro ``ClientPortfolio`` model to a SurrealDB record dict.

    The ``positions`` field is stored as a plain JSON array of dicts so
    that individual position data is preserved but the schema stays simple
    (``TYPE array``).

    Args:
        portfolio: An eToro ``ClientPortfolio`` Pydantic model.
        run_type: ``"market_open"`` or ``"market_close"``.

    Returns:
        A dict suitable for ``db.create()``.
    """
    # Serialize positions to plain dicts for storage
    positions_data = [
        pos.model_dump(mode="json") for pos in portfolio.positions
    ]

    total_pnl = portfolio.unrealized_pnl or 0.0
    total_value = portfolio.credit + total_pnl

    return {
        "total_value": total_value,
        "cash_available": portfolio.credit,
        "open_positions": len(portfolio.positions),
        "total_pnl": total_pnl,
        "positions": positions_data,
        "run_type": run_type,
    }


def create_snapshot(
    db: SyncTemplate,
    portfolio: ClientPortfolio,
    run_type: RunType,
) -> dict[str, Any]:
    """Create a new portfolio snapshot record.

    Each invocation creates a new record with an auto-generated ID.

    Args:
        db: An open SurrealDB connection.
        portfolio: An eToro ``ClientPortfolio`` model.
        run_type: ``"market_open"`` or ``"market_close"``.

    Returns:
        The created snapshot record dict.
    """
    data = _portfolio_to_record(portfolio, run_type)
    logger.debug(
        "snapshot_create",
        run_type=run_type,
        open_positions=data["open_positions"],
        total_value=data["total_value"],
    )
    result = db.create("portfolio_snapshot", data)
    created = first_or_none(result)
    if created is None:
        logger.error(
            "snapshot_create_failed",
            run_type=run_type,
            total_value=data.get("total_value"),
            raw_result=result,
        )
        raise RuntimeError("Failed to create portfolio snapshot in SurrealDB")
    return created


def create_snapshot_raw(
    db: SyncTemplate,
    data: dict[str, Any],
) -> dict[str, Any]:
    """Create a snapshot from a pre-built dict (useful when not using models).

    Args:
        db: An open SurrealDB connection.
        data: A dict with the snapshot fields.

    Returns:
        The created snapshot record dict.
    """
    logger.debug("snapshot_create_raw", run_type=data.get("run_type"))
    result = db.create("portfolio_snapshot", data)
    created = first_or_none(result)
    if created is None:
        logger.error(
            "snapshot_create_raw_failed",
            run_type=data.get("run_type"),
            raw_result=result,
        )
        raise RuntimeError("Failed to create portfolio snapshot in SurrealDB")
    return created


def get_latest_snapshot(db: SyncTemplate) -> dict[str, Any] | None:
    """Retrieve the most recent portfolio snapshot.

    Args:
        db: An open SurrealDB connection.

    Returns:
        The latest snapshot record dict, or ``None`` if no snapshots exist.
    """
    result = db.query(
        "SELECT * FROM portfolio_snapshot ORDER BY captured_at DESC LIMIT 1;"
    )
    return first_or_none(result)


def query_snapshots(
    db: SyncTemplate,
    run_type: RunType | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Query portfolio snapshots, optionally filtered by run type.

    Args:
        db: An open SurrealDB connection.
        run_type: If provided, filter to only this run type.
        limit: Maximum number of results to return (default 50).

    Returns:
        A list of snapshot record dicts, ordered by ``captured_at`` descending.
    """
    params: dict[str, Any] = {"limit": limit}

    if run_type is not None:
        sql = (
            "SELECT * FROM portfolio_snapshot "
            "WHERE run_type = $run_type "
            "ORDER BY captured_at DESC LIMIT $limit;"
        )
        params["run_type"] = run_type
    else:
        sql = (
            "SELECT * FROM portfolio_snapshot "
            "ORDER BY captured_at DESC LIMIT $limit;"
        )

    result = db.query(sql, params)
    return normalise_response(result)
