"""Cache report objects as JSON for rapid development iteration.

Provides functions to serialize Report dataclasses to JSON files and
deserialize them back for testing message formatting without running
the full pipeline.

This enables fast iteration on Telegram message formats and report layouts
during development.
"""

import json
import structlog
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from agent.reporting.generator import Report

logger = structlog.get_logger(__name__)


def _serialize_value(obj: Any) -> Any:
    """Recursively convert non-JSON-serializable types to JSON-compatible forms."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    elif is_dataclass(obj) and not isinstance(obj, type):
        return _serialize_value(asdict(obj))  # type: ignore
    elif isinstance(obj, dict):
        return {k: _serialize_value(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [_serialize_value(item) for item in obj]
    else:
        return obj


def _parse_generated_at(raw_value: Any) -> datetime:
    """Parse generated_at strictly to avoid silently masking cache corruption."""
    if not isinstance(raw_value, str):
        raise ValueError("generated_at must be an ISO datetime string")

    try:
        return datetime.fromisoformat(raw_value)
    except ValueError as exc:
        raise ValueError(f"invalid generated_at value: {raw_value}") from exc


def cache_report(report: Report, cache_dir: Path = Path("reports/cache")) -> Path:
    """Serialize a Report to a JSON cache file.

    Args:
        report: The Report instance to cache.
        cache_dir: Directory to store cache files (default: reports/cache).

    Returns:
        Path to the created cache file.

    Raises:
        IOError: If cache file cannot be written.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)

    # Use run_id as cache filename
    cache_file = cache_dir / f"{report.run_id}.json"

    # Serialize the report
    report_dict = asdict(report)
    serialized = _serialize_value(report_dict)

    # Write to JSON
    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump(serialized, f, indent=2)

    logger.info("report_cached", cache_file=str(cache_file), run_id=report.run_id)
    return cache_file


def load_cached_report(run_id: str, cache_dir: Path = Path("reports/cache")) -> Report | None:
    """Load a cached Report from JSON.

    Args:
        run_id: The run_id of the report to load.
        cache_dir: Directory containing cache files (default: reports/cache).

    Returns:
        Deserialized Report instance, or None if not found.

    Raises:
        ValueError: If cache file is malformed.
    """
    cache_file = cache_dir / f"{run_id}.json"

    if not cache_file.exists():
        logger.debug("cache_miss", run_id=run_id, cache_file=str(cache_file))
        return None

    try:
        with open(cache_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Reconstruct the Report (and nested dataclasses)
        from agent.reporting.generator import (
            AnalysisSummary,
            CommentarySummary,
            InstrumentSummary,
            PositionSummary,
            ReportDiff,
            RecommendationChange,
            RecommendationSummary,
            SnapshotSummary,
        )

        # Rebuild nested structures
        data["generated_at"] = _parse_generated_at(data.get("generated_at"))

        # Snapshot
        snapshot_data = data["snapshot"]
        snapshot_data["positions"] = [
            PositionSummary(**pos) for pos in snapshot_data.get("positions", [])
        ]
        data["snapshot"] = SnapshotSummary(**snapshot_data)

        # Instruments
        data["instruments"] = [
            InstrumentSummary(**inst) for inst in data.get("instruments", [])
        ]

        # Analyses
        data["analyses"] = [
            AnalysisSummary(**ana) for ana in data.get("analyses", [])
        ]

        # Commentary
        if data.get("commentary") is not None:
            comm_data = data["commentary"]
            comm_data["recommendations"] = [
                RecommendationSummary(**rec) for rec in comm_data.get("recommendations", [])
            ]
            data["commentary"] = CommentarySummary(**comm_data)

        # Report diff (optional)
        if data.get("diff") is not None:
            diff_data = data["diff"]
            diff_data["major_changes"] = [
                RecommendationChange(**change) for change in diff_data.get("major_changes", [])
            ]
            diff_data["minor_changes"] = [
                RecommendationChange(**change) for change in diff_data.get("minor_changes", [])
            ]
            diff_data["new_symbols"] = [
                RecommendationSummary(**rec) for rec in diff_data.get("new_symbols", [])
            ]
            data["diff"] = ReportDiff(**diff_data)

        report = Report(**data)
        logger.info("cache_hit", run_id=run_id, cache_file=str(cache_file))
        return report

    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
        logger.error("cache_load_failed", run_id=run_id, cache_file=str(cache_file), error=str(e))
        raise ValueError(f"Failed to load cache {cache_file}: {e}") from e


def list_cached_reports(cache_dir: Path = Path("reports/cache")) -> list[str]:
    """List all cached report run_ids."""
    if not cache_dir.exists():
        return []
    return [f.stem for f in cache_dir.glob("*.json")]
