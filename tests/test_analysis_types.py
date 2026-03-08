"""Tests for AnalysisResult.to_db_fields() serialisation."""

from __future__ import annotations

from agent.analysis.types import (
    AnalysisResult,
    IndicatorResult,
    PriceActionResult,
    SectorGroupResult,
)


class TestAnalysisResultToDbFields:
    def test_basic_serialisation(self) -> None:
        pa = PriceActionResult(
            trend="bullish",
            trend_strength=0.75,
            support=145.0,
            resistance=160.0,
            momentum_signal="bullish",
            indicators=[
                IndicatorResult(name="trend", signal="bullish", strength=0.8),
                IndicatorResult(name="momentum", signal="bullish", strength=0.7),
            ],
        )
        result = AnalysisResult(
            instrument_etoro_id=1001,
            price_action=pa,
        )
        fields = result.to_db_fields()

        assert fields["trend"] == "bullish"
        assert fields["trend_strength"] == 0.75
        assert fields["price_action"]["support"] == 145.0
        assert fields["price_action"]["resistance"] == 160.0
        assert fields["price_action"]["momentum_signal"] == "bullish"
        assert len(fields["price_action"]["indicators"]) == 2
        assert fields["sector_context"] is None
        assert fields["raw_data"]["price_action"] is not None

    def test_with_sector_context(self) -> None:
        pa = PriceActionResult(
            trend="neutral",
            trend_strength=0.0,
            support=None,
            resistance=None,
            momentum_signal="neutral",
        )
        sector = SectorGroupResult(
            group_name="US",
            instrument_count=5,
            avg_return_pct=3.5,
        )
        result = AnalysisResult(
            instrument_etoro_id=1001,
            price_action=pa,
            sector_context=sector,
        )
        fields = result.to_db_fields()

        assert fields["sector_context"] is not None
        assert fields["sector_context"]["group_name"] == "US"
        assert fields["sector_context"]["instrument_count"] == 5
        assert fields["sector_context"]["avg_return_pct"] == 3.5

    def test_indicator_details_preserved(self) -> None:
        pa = PriceActionResult(
            trend="bearish",
            trend_strength=0.6,
            support=None,
            resistance=None,
            momentum_signal="bearish",
            indicators=[
                IndicatorResult(
                    name="levels",
                    signal="neutral",
                    strength=0.0,
                    details={"support_levels": [95.0, 100.0]},
                ),
            ],
        )
        result = AnalysisResult(instrument_etoro_id=1001, price_action=pa)
        fields = result.to_db_fields()

        ind = fields["price_action"]["indicators"][0]
        assert ind["name"] == "levels"
        assert ind["details"]["support_levels"] == [95.0, 100.0]
