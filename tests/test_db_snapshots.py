"""Tests for db/snapshots.py — portfolio snapshot CRUD against in-memory SurrealDB."""

from __future__ import annotations

from surrealdb.connections.sync_template import SyncTemplate

from agent.db.snapshots import (
    create_snapshot_raw,
    get_latest_snapshot,
    query_snapshots,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_snapshot_data(
    run_type: str = "market_open",
    total_value: float = 10_000.0,
    cash_available: float = 5_000.0,
    open_positions: int = 3,
    total_pnl: float = 500.0,
) -> dict:
    """Create a minimal snapshot data dict for testing."""
    return {
        "total_value": total_value,
        "cash_available": cash_available,
        "open_positions": open_positions,
        "total_pnl": total_pnl,
        "positions": [],
        "run_type": run_type,
    }


# ---------------------------------------------------------------------------
# create_snapshot_raw
# ---------------------------------------------------------------------------


def test_create_snapshot_raw(db: SyncTemplate) -> None:
    """A snapshot is created and returned with the correct fields."""
    data = _make_snapshot_data()
    result = create_snapshot_raw(db, data)

    assert result["total_value"] == 10_000.0
    assert result["cash_available"] == 5_000.0
    assert result["open_positions"] == 3
    assert result["run_type"] == "market_open"


def test_create_multiple_snapshots(db: SyncTemplate) -> None:
    """Multiple snapshots can be created independently."""
    create_snapshot_raw(db, _make_snapshot_data(run_type="market_open"))
    create_snapshot_raw(db, _make_snapshot_data(run_type="market_close"))

    all_snapshots = query_snapshots(db)
    assert len(all_snapshots) == 2


# ---------------------------------------------------------------------------
# get_latest_snapshot
# ---------------------------------------------------------------------------


def test_get_latest_snapshot_returns_most_recent(db: SyncTemplate) -> None:
    """The latest snapshot by captured_at is returned."""
    create_snapshot_raw(db, _make_snapshot_data(total_value=10_000.0))
    create_snapshot_raw(db, _make_snapshot_data(total_value=20_000.0))

    latest = get_latest_snapshot(db)
    assert latest is not None
    # The second snapshot should be the latest (larger total_value)
    assert latest["total_value"] == 20_000.0


def test_get_latest_snapshot_empty(db: SyncTemplate) -> None:
    """Returns None when no snapshots exist."""
    result = get_latest_snapshot(db)
    assert result is None


# ---------------------------------------------------------------------------
# query_snapshots
# ---------------------------------------------------------------------------


def test_query_snapshots_all(db: SyncTemplate) -> None:
    """Returns all snapshots when no filter is applied."""
    create_snapshot_raw(db, _make_snapshot_data(run_type="market_open"))
    create_snapshot_raw(db, _make_snapshot_data(run_type="market_close"))

    results = query_snapshots(db)
    assert len(results) == 2


def test_query_snapshots_filter_by_run_type(db: SyncTemplate) -> None:
    """Returns only snapshots matching the run_type filter."""
    create_snapshot_raw(db, _make_snapshot_data(run_type="market_open"))
    create_snapshot_raw(db, _make_snapshot_data(run_type="market_close"))
    create_snapshot_raw(db, _make_snapshot_data(run_type="market_open"))

    opens = query_snapshots(db, run_type="market_open")
    closes = query_snapshots(db, run_type="market_close")

    assert len(opens) == 2
    assert len(closes) == 1


def test_query_snapshots_respects_limit(db: SyncTemplate) -> None:
    """The limit parameter caps the number of results."""
    for _ in range(5):
        create_snapshot_raw(db, _make_snapshot_data())

    results = query_snapshots(db, limit=3)
    assert len(results) == 3


def test_query_snapshots_empty(db: SyncTemplate) -> None:
    """Returns empty list when no snapshots exist."""
    results = query_snapshots(db)
    assert results == []
