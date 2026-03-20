from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from agent.reporting.cache import cache_report, load_cached_report
from agent.reporting.generator import Report, SnapshotSummary


def test_cache_report_round_trip_preserves_optional_fields_and_candle_count_keys(
    tmp_path: Path,
) -> None:
    report = Report(
        run_id="market_open_2026_03_20_050000",
        run_type="market_open",
        generated_at=datetime(2026, 3, 20, 5, 0, tzinfo=timezone.utc),
        snapshot=SnapshotSummary(
            total_value=1000.0,
            cash_available=250.0,
            open_positions=0,
            total_pnl=12.5,
            run_type="market_open",
            captured_at="2026-03-20T05:00:00+00:00",
            positions=[],
        ),
        instruments=[],
        analyses=[],
        commentary=None,
        candle_counts={42: 120, 99: 5},
        errors=[],
        diff=None,
    )

    cache_file = cache_report(report, tmp_path)
    loaded_report = load_cached_report(report.run_id, tmp_path)

    assert cache_file == tmp_path / f"{report.run_id}.json"
    assert loaded_report is not None
    assert loaded_report == report
    assert loaded_report.commentary is None
    assert loaded_report.diff is None
    assert loaded_report.candle_counts == {42: 120, 99: 5}
    assert all(isinstance(key, int) for key in loaded_report.candle_counts)
