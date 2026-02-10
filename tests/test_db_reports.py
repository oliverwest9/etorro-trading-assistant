"""Tests for db/reports.py — report & recommendation CRUD against in-memory SurrealDB."""

from __future__ import annotations

from surrealdb.connections.sync_template import SyncTemplate

from agent.db.reports import (
    create_recommendation,
    create_report,
    get_latest_report,
    get_recommendations_for_report,
    get_report_by_run_id,
    query_reports,
)
from agent.db.instruments import upsert_instrument
from agent.db.snapshots import create_snapshot_raw
from agent.etoro.models import Instrument


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_snapshot(db: SyncTemplate) -> str:
    """Create a portfolio snapshot and return its record ID string."""
    result = create_snapshot_raw(
        db,
        {
            "total_value": 10_000.0,
            "cash_available": 5_000.0,
            "open_positions": 2,
            "total_pnl": 500.0,
            "positions": [],
            "run_type": "market_open",
        },
    )
    record_id = result.get("id", "")
    # Record ID may be a RecordID object — convert to string
    return str(record_id)


def _seed_instrument(db: SyncTemplate, etoro_id: int = 1001) -> None:
    """Create an instrument record for FK references."""
    inst = Instrument.model_validate(
        {
            "instrumentID": etoro_id,
            "symbolFull": "AAPL",
            "instrumentDisplayName": "Apple Inc.",
            "instrumentTypeID": 5,
            "exchangeID": 1,
        }
    )
    upsert_instrument(db, inst)


def _seed_analysis(db: SyncTemplate, instrument_etoro_id: int = 1001) -> str:
    """Create an analysis record and return its record ID string."""
    from surrealdb import RecordID

    result = db.create(
        "analysis",
        {
            "instrument": RecordID("instrument", instrument_etoro_id),
            "run_id": "run-001",
            "trend": "bullish",
            "trend_strength": 0.8,
            "price_action": {"support": 145.0, "resistance": 160.0},
            "raw_data": {"candles": 30},
        },
    )
    # Handle both dict and list returns
    record: dict  # type: ignore[type-arg]
    if isinstance(result, list) and result:
        record = result[0]  # type: ignore[assignment]
    elif isinstance(result, dict):
        record = result
    else:
        return "analysis:unknown"
    return str(record.get("id", "analysis:unknown"))


# ---------------------------------------------------------------------------
# create_report
# ---------------------------------------------------------------------------


def test_create_report(db: SyncTemplate) -> None:
    """A report is created with the correct fields."""
    snapshot_id = _seed_snapshot(db)
    result = create_report(
        db,
        run_id="run-001",
        run_type="market_open",
        snapshot_id=snapshot_id,
        commentary="Markets looking bullish today.",
        summary="Bull run continues",
        report_markdown="# Report\n\nBull run continues.",
    )

    assert result["run_id"] == "run-001"
    assert result["run_type"] == "market_open"
    assert result["commentary"] == "Markets looking bullish today."
    assert result["summary"] == "Bull run continues"


def test_create_report_with_recommendations_list(db: SyncTemplate) -> None:
    """A report can store a summary recommendations array."""
    snapshot_id = _seed_snapshot(db)
    recs = [{"action": "buy", "symbol": "AAPL", "conviction": "high"}]
    result = create_report(
        db,
        run_id="run-002",
        run_type="market_close",
        snapshot_id=snapshot_id,
        commentary="Commentary",
        summary="Summary",
        report_markdown="# Report",
        recommendations=recs,
    )

    assert len(result["recommendations"]) == 1
    assert result["recommendations"][0]["action"] == "buy"


# ---------------------------------------------------------------------------
# get_report_by_run_id
# ---------------------------------------------------------------------------


def test_get_report_by_run_id_found(db: SyncTemplate) -> None:
    """Returns the report when run_id exists."""
    snapshot_id = _seed_snapshot(db)
    create_report(
        db,
        run_id="run-001",
        run_type="market_open",
        snapshot_id=snapshot_id,
        commentary="Commentary",
        summary="Summary",
        report_markdown="# Report",
    )

    result = get_report_by_run_id(db, "run-001")
    assert result is not None
    assert result["run_id"] == "run-001"


def test_get_report_by_run_id_not_found(db: SyncTemplate) -> None:
    """Returns None when run_id does not exist."""
    result = get_report_by_run_id(db, "nonexistent")
    assert result is None


# ---------------------------------------------------------------------------
# get_latest_report
# ---------------------------------------------------------------------------


def test_get_latest_report(db: SyncTemplate) -> None:
    """Returns the most recent report."""
    snapshot_id = _seed_snapshot(db)
    create_report(
        db,
        run_id="run-001",
        run_type="market_open",
        snapshot_id=snapshot_id,
        commentary="First",
        summary="First",
        report_markdown="# First",
    )
    create_report(
        db,
        run_id="run-002",
        run_type="market_close",
        snapshot_id=snapshot_id,
        commentary="Second",
        summary="Second",
        report_markdown="# Second",
    )

    latest = get_latest_report(db)
    assert latest is not None
    assert latest["run_id"] == "run-002"


def test_get_latest_report_empty(db: SyncTemplate) -> None:
    """Returns None when no reports exist."""
    assert get_latest_report(db) is None


# ---------------------------------------------------------------------------
# query_reports
# ---------------------------------------------------------------------------


def test_query_reports_all(db: SyncTemplate) -> None:
    """Returns all reports without a filter."""
    snapshot_id = _seed_snapshot(db)
    create_report(db, run_id="r1", run_type="market_open", snapshot_id=snapshot_id, commentary="c", summary="s", report_markdown="m")
    create_report(db, run_id="r2", run_type="market_close", snapshot_id=snapshot_id, commentary="c", summary="s", report_markdown="m")

    results = query_reports(db)
    assert len(results) == 2


def test_query_reports_filter_by_run_type(db: SyncTemplate) -> None:
    """Returns only reports matching the run_type."""
    snapshot_id = _seed_snapshot(db)
    create_report(db, run_id="r1", run_type="market_open", snapshot_id=snapshot_id, commentary="c", summary="s", report_markdown="m")
    create_report(db, run_id="r2", run_type="market_close", snapshot_id=snapshot_id, commentary="c", summary="s", report_markdown="m")
    create_report(db, run_id="r3", run_type="market_open", snapshot_id=snapshot_id, commentary="c", summary="s", report_markdown="m")

    opens = query_reports(db, run_type="market_open")
    assert len(opens) == 2


def test_query_reports_respects_limit(db: SyncTemplate) -> None:
    """The limit parameter caps the number of results."""
    snapshot_id = _seed_snapshot(db)
    for i in range(5):
        create_report(db, run_id=f"r{i}", run_type="market_open", snapshot_id=snapshot_id, commentary="c", summary="s", report_markdown="m")

    results = query_reports(db, limit=3)
    assert len(results) == 3


# ---------------------------------------------------------------------------
# create_recommendation & get_recommendations_for_report
# ---------------------------------------------------------------------------


def test_create_and_get_recommendations(db: SyncTemplate) -> None:
    """Recommendations are linked to their parent report."""
    _seed_instrument(db, etoro_id=1001)
    snapshot_id = _seed_snapshot(db)
    analysis_id = _seed_analysis(db, instrument_etoro_id=1001)

    report = create_report(
        db,
        run_id="run-rec",
        run_type="market_open",
        snapshot_id=snapshot_id,
        commentary="c",
        summary="s",
        report_markdown="m",
    )
    report_id = str(report.get("id", ""))

    # Create two recommendations
    create_recommendation(
        db,
        report_id=report_id,
        instrument_etoro_id=1001,
        action="buy",
        conviction="high",
        reasoning="Strong bullish trend",
        analysis_id=analysis_id,
    )
    create_recommendation(
        db,
        report_id=report_id,
        instrument_etoro_id=1001,
        action="hold",
        conviction="medium",
        reasoning="Stable position",
        analysis_id=analysis_id,
    )

    recs = get_recommendations_for_report(db, report_id)
    assert len(recs) == 2
    actions = {r["action"] for r in recs}
    assert actions == {"buy", "hold"}


def test_get_recommendations_empty(db: SyncTemplate) -> None:
    """Returns empty list when no recommendations exist for the report."""
    snapshot_id = _seed_snapshot(db)
    report = create_report(
        db,
        run_id="run-empty",
        run_type="market_open",
        snapshot_id=snapshot_id,
        commentary="c",
        summary="s",
        report_markdown="m",
    )
    report_id = str(report.get("id", ""))

    recs = get_recommendations_for_report(db, report_id)
    assert recs == []
