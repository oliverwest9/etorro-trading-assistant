"""Report model and assembly logic.

Defines the ``Report`` dataclass that aggregates all pipeline data
(snapshot, instruments, analyses, LLM commentary) into a single object,
and the ``generate_report()`` function that builds it from a pipeline
summary dict plus DB queries.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import structlog
from surrealdb.connections.sync_template import SyncTemplate

from agent.db.analysis import get_analyses_by_run_id
from agent.db.candles import count_candles
from agent.db.instruments import list_instruments
from agent.db.reports import get_previous_report
from agent.db.snapshots import get_latest_snapshot

logger = structlog.get_logger(__name__)


# =====================================================================
# Supporting dataclasses
# =====================================================================


@dataclass(frozen=True)
class PositionSummary:
    """A single open position in the portfolio snapshot."""

    instrument_id: int
    symbol: str
    direction: str  # "Long" or "Short"
    open_rate: float
    amount: float
    units: float
    pnl: float | None


@dataclass(frozen=True)
class SnapshotSummary:
    """Portfolio snapshot overview."""

    total_value: float
    cash_available: float
    open_positions: int
    total_pnl: float
    run_type: str
    captured_at: str
    positions: list[PositionSummary]


@dataclass(frozen=True)
class InstrumentSummary:
    """Instrument metadata with candle count."""

    etoro_id: int
    symbol: str
    asset_class: str
    exchange: str | None
    candle_count: int


@dataclass(frozen=True)
class AnalysisSummary:
    """Per-instrument analysis results."""

    symbol: str
    etoro_id: int
    trend: str
    trend_strength: float
    support: float | None
    resistance: float | None
    momentum: str


@dataclass(frozen=True)
class RecommendationSummary:
    """A single actionable recommendation."""

    symbol: str
    action: str
    conviction: str
    reasoning: str


@dataclass(frozen=True)
class CommentarySummary:
    """LLM-generated commentary and recommendations."""

    summary: str
    market_context: str
    position_commentaries: list[dict[str, str]]
    recommendations: list[RecommendationSummary]


@dataclass(frozen=True)
class RecommendationChange:
    """A recommendation whose action changed between runs."""

    symbol: str
    previous_action: str
    new_action: str
    previous_conviction: str
    new_conviction: str
    reasoning: str


@dataclass(frozen=True)
class ReportDiff:
    """Differences between the current and previous reports."""

    previous_run_id: str
    previous_run_type: str
    major_changes: list[RecommendationChange]
    minor_changes: list[RecommendationChange]
    new_symbols: list[RecommendationSummary]
    removed_symbols: list[str]
    unchanged_count: int


@dataclass
class Report:
    """Aggregated report combining all pipeline output."""

    run_id: str
    run_type: str
    generated_at: datetime
    snapshot: SnapshotSummary
    instruments: list[InstrumentSummary]
    analyses: list[AnalysisSummary]
    commentary: CommentarySummary | None
    candle_counts: dict[int, int]
    errors: list[dict[str, Any]]
    diff: ReportDiff | None = None


# =====================================================================
# Assembly
# =====================================================================


def _build_symbol_lookup(
    instruments: list[dict[str, Any]],
) -> dict[str, str]:
    """Map instrument record ID strings to symbols."""
    lookup: dict[str, str] = {}
    for inst in instruments:
        lookup[str(inst.get("id", ""))] = inst.get("symbol", "?")
    return lookup


def _build_etoro_id_symbol_lookup(
    instruments: list[dict[str, Any]],
) -> dict[int, str]:
    """Map eToro instrument IDs to symbols."""
    lookup: dict[int, str] = {}
    for inst in instruments:
        eid = inst.get("etoro_id")
        if eid is not None:
            lookup[int(eid)] = inst.get("symbol", "?")
    return lookup


def _build_snapshot_summary(
    snapshot: dict[str, Any],
    etoro_symbol_map: dict[int, str],
) -> SnapshotSummary:
    """Convert a raw SurrealDB snapshot dict into a SnapshotSummary."""
    positions: list[PositionSummary] = []
    for pos in snapshot.get("positions", []):
        iid = pos.get("instrumentID", pos.get("instrument_id", 0))
        direction = "Long" if pos.get("isBuy", pos.get("is_buy")) else "Short"
        pnl_data = pos.get("unrealizedPnL", pos.get("unrealized_pnl", {}))
        pnl: float | None = None
        if isinstance(pnl_data, dict) and pnl_data:
            pnl = pnl_data.get("pnL", pnl_data.get("pnl"))
        positions.append(
            PositionSummary(
                instrument_id=iid,
                symbol=etoro_symbol_map.get(iid, f"ID:{iid}"),
                direction=direction,
                open_rate=pos.get("openRate", pos.get("open_rate", 0.0)),
                amount=pos.get("amount", 0.0),
                units=pos.get("units", 0.0),
                pnl=pnl,
            )
        )
    return SnapshotSummary(
        total_value=snapshot.get("total_value", 0.0),
        cash_available=snapshot.get("cash_available", 0.0),
        open_positions=snapshot.get("open_positions", 0),
        total_pnl=snapshot.get("total_pnl", 0.0),
        run_type=snapshot.get("run_type", "?"),
        captured_at=str(snapshot.get("captured_at", "?")),
        positions=positions,
    )


def _build_analysis_summaries(
    analyses: list[dict[str, Any]],
    record_id_lookup: dict[str, str],
) -> list[AnalysisSummary]:
    """Convert raw analysis dicts into AnalysisSummary list."""
    result: list[AnalysisSummary] = []
    for a in analyses:
        inst_ref = a.get("instrument", "")
        symbol = record_id_lookup.get(str(inst_ref), str(inst_ref))

        # Extract etoro_id from the instrument ref
        etoro_id = 0
        if hasattr(inst_ref, "id"):
            etoro_id = inst_ref.id
        elif isinstance(inst_ref, str) and ":" in inst_ref:
            try:
                etoro_id = int(inst_ref.split(":", 1)[1])
            except (ValueError, IndexError):
                pass

        pa = a.get("price_action", {})
        result.append(
            AnalysisSummary(
                symbol=symbol,
                etoro_id=etoro_id,
                trend=a.get("trend", "?"),
                trend_strength=a.get("trend_strength", 0.0),
                support=pa.get("support"),
                resistance=pa.get("resistance"),
                momentum=pa.get("momentum_signal", "?"),
            )
        )
    return result


def _build_commentary_summary(
    commentary: dict[str, Any],
) -> CommentarySummary:
    """Convert the commentary dict from the pipeline summary."""
    recs = [
        RecommendationSummary(
            symbol=r["symbol"],
            action=r["action"],
            conviction=r["conviction"],
            reasoning=r["reasoning"],
        )
        for r in commentary.get("recommendations", [])
    ]
    return CommentarySummary(
        summary=commentary["summary"],
        market_context=commentary["market_context"],
        position_commentaries=commentary.get("position_commentaries", []),
        recommendations=recs,
    )


_CONVICTION_RANK = {"low": 0, "medium": 1, "high": 2}


def _is_major_change(prev_action: str, new_action: str, prev_conviction: str, new_conviction: str) -> bool:
    """Return True if the change is major (action flip or 2-level conviction jump)."""
    if prev_action != new_action:
        return True
    prev_rank = _CONVICTION_RANK.get(prev_conviction, 1)
    new_rank = _CONVICTION_RANK.get(new_conviction, 1)
    return abs(new_rank - prev_rank) >= 2


def _compute_diff(
    current_recs: list[RecommendationSummary],
    previous_report: dict[str, Any],
) -> ReportDiff:
    """Compare current recommendations against the previous report."""
    prev_by_symbol: dict[str, dict[str, str]] = {}
    for rec in previous_report.get("recommendations", []):
        prev_by_symbol[rec["symbol"]] = rec

    current_by_symbol: dict[str, RecommendationSummary] = {}
    for rec in current_recs:
        current_by_symbol[rec.symbol] = rec

    major_changes: list[RecommendationChange] = []
    minor_changes: list[RecommendationChange] = []
    new_symbols: list[RecommendationSummary] = []
    unchanged_count = 0

    for rec in current_recs:
        prev = prev_by_symbol.get(rec.symbol)
        if prev is None:
            new_symbols.append(rec)
        elif prev["action"] != rec.action or prev["conviction"] != rec.conviction:
            change = RecommendationChange(
                symbol=rec.symbol,
                previous_action=prev["action"],
                new_action=rec.action,
                previous_conviction=prev["conviction"],
                new_conviction=rec.conviction,
                reasoning=rec.reasoning,
            )
            if _is_major_change(prev["action"], rec.action, prev["conviction"], rec.conviction):
                major_changes.append(change)
            else:
                minor_changes.append(change)
        else:
            unchanged_count += 1

    removed_symbols = [
        sym for sym in prev_by_symbol if sym not in current_by_symbol
    ]

    return ReportDiff(
        previous_run_id=previous_report.get("run_id", "?"),
        previous_run_type=previous_report.get("run_type", "?"),
        major_changes=major_changes,
        minor_changes=minor_changes,
        new_symbols=new_symbols,
        removed_symbols=removed_symbols,
        unchanged_count=unchanged_count,
    )


def generate_report(
    pipeline_summary: dict[str, Any],
    db: SyncTemplate,
) -> Report:
    """Assemble a ``Report`` from the pipeline summary and DB state.

    Args:
        pipeline_summary: The dict returned by
            ``Orchestrator.run_data_pipeline()``.
        db: An open SurrealDB connection for additional queries.

    Returns:
        A fully populated ``Report`` instance.
    """
    run_id = pipeline_summary["run_id"]
    run_type = pipeline_summary["run_type"]

    # Fetch latest snapshot from DB (has full position data)
    snapshot_raw = get_latest_snapshot(db)
    if snapshot_raw is None:
        snapshot_raw = {
            "total_value": 0.0,
            "cash_available": 0.0,
            "open_positions": 0,
            "total_pnl": 0.0,
            "run_type": run_type,
            "captured_at": "",
            "positions": [],
        }

    # Fetch instruments for symbol resolution
    instruments_raw = list_instruments(db)
    record_id_lookup = _build_symbol_lookup(instruments_raw)
    etoro_symbol_map = _build_etoro_id_symbol_lookup(instruments_raw)

    # Build instrument summaries with candle counts
    instrument_summaries: list[InstrumentSummary] = []
    for inst in sorted(instruments_raw, key=lambda i: i.get("symbol", "")):
        eid = inst.get("etoro_id", 0)
        instrument_summaries.append(
            InstrumentSummary(
                etoro_id=eid,
                symbol=inst.get("symbol", "?"),
                asset_class=inst.get("asset_class", "?"),
                exchange=inst.get("exchange") or None,
                candle_count=count_candles(db, eid, "1d"),
            )
        )

    # Fetch and build analysis summaries
    analyses_raw = get_analyses_by_run_id(db, run_id)
    analysis_summaries = _build_analysis_summaries(analyses_raw, record_id_lookup)

    # Build snapshot summary
    snapshot_summary = _build_snapshot_summary(snapshot_raw, etoro_symbol_map)

    # Build commentary summary (if present)
    commentary_data = pipeline_summary.get("commentary")
    commentary_summary = (
        _build_commentary_summary(commentary_data) if commentary_data else None
    )

    # Compute diff against previous report
    diff: ReportDiff | None = None
    if commentary_summary and commentary_summary.recommendations:
        try:
            prev = get_previous_report(db, current_run_id=run_id)
            if prev is not None:
                diff = _compute_diff(commentary_summary.recommendations, prev)
        except Exception:
            logger.warning("diff_computation_failed", exc_info=True)

    return Report(
        run_id=run_id,
        run_type=run_type,
        generated_at=datetime.now(tz=timezone.utc),
        snapshot=snapshot_summary,
        instruments=instrument_summaries,
        analyses=analysis_summaries,
        commentary=commentary_summary,
        candle_counts=pipeline_summary.get("candle_counts", {}),
        errors=pipeline_summary.get("errors", []),
        diff=diff,
    )
