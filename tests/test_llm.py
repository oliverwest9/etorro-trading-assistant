"""Tests for the LLM commentary module.

Covers payload construction (pure functions, no API key needed),
response parsing, and Gemini API integration with mocking.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from agent.reporting.llm import (
    CommentaryRequest,
    CommentaryResponse,
    NewsHeadline,
    PositionCommentary,
    PositionData,
    Recommendation,
    SectorOverview,
    build_commentary_request,
    format_prompt,
    generate_commentary,
)


# =====================================================================
# Fixtures — realistic data shaped like SurrealDB output
# =====================================================================


def _make_snapshot(
    *,
    total_value: float = 500.0,
    cash_available: float = 50.0,
    open_positions: int = 2,
    total_pnl: float = 12.50,
    positions: list | None = None,
) -> dict:
    if positions is None:
        positions = [
            {
                "instrument_id": 1010,
                "is_buy": True,
                "open_rate": 228.67,
                "amount": 20.0,
                "units": 0.0875,
                "unrealized_pnl": {"pnl": 1.36},
            },
            {
                "instrument_id": 100000,
                "is_buy": True,
                "open_rate": 90824.0,
                "amount": 49.95,
                "units": 0.0006,
                "unrealized_pnl": {"pnl": -12.23},
            },
        ]
    return {
        "total_value": total_value,
        "cash_available": cash_available,
        "open_positions": open_positions,
        "total_pnl": total_pnl,
        "positions": positions,
    }


def _make_analyses() -> list[dict]:
    return [
        {
            "instrument_etoro_id": 1010,
            "trend": "bullish",
            "trend_strength": 0.72,
            "price_action": {
                "support": 220.0,
                "resistance": 240.0,
                "momentum_signal": "bullish",
            },
            "sector_context": {
                "group_name": "US",
                "instrument_count": 5,
                "avg_return_pct": 1.25,
            },
        },
        {
            "instrument_etoro_id": 100000,
            "trend": "bearish",
            "trend_strength": 0.55,
            "price_action": {
                "support": 85000.0,
                "resistance": 95000.0,
                "momentum_signal": "bearish",
            },
            "sector_context": {
                "group_name": "Crypto",
                "instrument_count": 1,
                "avg_return_pct": -3.40,
            },
        },
    ]


def _make_instrument_map() -> dict[int, dict]:
    return {
        1010: {"etoro_id": 1010, "symbol": "BA", "name": "Boeing Co"},
        100000: {"etoro_id": 100000, "symbol": "BTC", "name": "Bitcoin"},
    }


def _make_valid_response_json() -> str:
    """JSON matching CommentaryResponse schema."""
    return json.dumps(
        {
            "summary": "Mixed signals: US equities trending up, crypto under pressure",
            "market_context": (
                "US equities show continued strength with Boeing leading gains. "
                "Crypto markets remain under selling pressure with Bitcoin below key resistance."
            ),
            "position_commentaries": [
                {
                    "instrument_id": 1010,
                    "symbol": "BA",
                    "commentary": (
                        "Boeing is in a bullish trend with support at $220. "
                        "Position is slightly profitable at $1.36."
                    ),
                },
                {
                    "instrument_id": 100000,
                    "symbol": "BTC",
                    "commentary": (
                        "Bitcoin is in a bearish trend, currently below resistance at $95,000. "
                        "Position is down $12.23."
                    ),
                },
            ],
            "recommendations": [
                {
                    "instrument_id": 1010,
                    "symbol": "BA",
                    "action": "hold",
                    "conviction": "medium",
                    "reasoning": (
                        "Bullish trend with 0.72 strength. Support at $220 is holding. "
                        "Small position size limits risk."
                    ),
                },
                {
                    "instrument_id": 100000,
                    "symbol": "BTC",
                    "action": "reduce",
                    "conviction": "low",
                    "reasoning": (
                        "Bearish momentum with support at $85,000 still distant. "
                        "Consider reducing exposure until trend reverses."
                    ),
                },
            ],
        }
    )


# =====================================================================
# build_commentary_request() tests
# =====================================================================


class TestBuildCommentaryRequest:
    def test_builds_request_with_positions(self):
        req = build_commentary_request(
            run_type="market_open",
            snapshot=_make_snapshot(),
            analyses=_make_analyses(),
            instrument_map=_make_instrument_map(),
        )

        assert req.run_type == "market_open"
        assert req.total_value == 500.0
        assert req.cash_available == 50.0
        assert req.open_positions_count == 2
        assert req.total_pnl == 12.50

        assert len(req.positions) == 2

        ba = req.positions[0]
        assert ba.instrument_id == 1010
        assert ba.symbol == "BA"
        assert ba.name == "Boeing Co"
        assert ba.direction == "Long"
        assert ba.trend == "bullish"
        assert ba.trend_strength == 0.72
        assert ba.support == 220.0
        assert ba.resistance == 240.0
        assert ba.momentum_signal == "bullish"
        assert ba.pnl == 1.36
        assert ba.sector_group == "US"
        assert ba.sector_avg_return_pct == 1.25

        btc = req.positions[1]
        assert btc.symbol == "BTC"
        assert btc.trend == "bearish"
        assert btc.pnl == -12.23
        assert btc.sector_group == "Crypto"

    def test_builds_sectors_from_analyses(self):
        req = build_commentary_request(
            run_type="market_close",
            snapshot=_make_snapshot(),
            analyses=_make_analyses(),
            instrument_map=_make_instrument_map(),
        )

        assert len(req.sectors) == 2
        sector_names = {s.group_name for s in req.sectors}
        assert sector_names == {"US", "Crypto"}

    def test_empty_portfolio(self):
        req = build_commentary_request(
            run_type="market_open",
            snapshot=_make_snapshot(
                total_value=0.0,
                cash_available=0.0,
                open_positions=0,
                total_pnl=0.0,
                positions=[],
            ),
            analyses=[],
            instrument_map={},
        )

        assert req.open_positions_count == 0
        assert req.positions == []
        assert req.sectors == []

    def test_position_without_analysis(self):
        """Position exists in snapshot but no analysis — gets default values."""
        req = build_commentary_request(
            run_type="market_open",
            snapshot=_make_snapshot(),
            analyses=[],  # no analyses
            instrument_map=_make_instrument_map(),
        )

        assert len(req.positions) == 2
        assert req.positions[0].trend == "unknown"
        assert req.positions[0].trend_strength == 0.0
        assert req.positions[0].momentum_signal == "unknown"

    def test_position_with_flat_pnl(self):
        """P&L as a flat key rather than nested unrealized_pnl."""
        snapshot = _make_snapshot(
            positions=[
                {
                    "instrument_id": 1010,
                    "is_buy": True,
                    "open_rate": 228.67,
                    "amount": 20.0,
                    "units": 0.0875,
                    "pnl": 5.0,
                },
            ],
            open_positions=1,
        )
        req = build_commentary_request(
            run_type="market_open",
            snapshot=snapshot,
            analyses=_make_analyses(),
            instrument_map=_make_instrument_map(),
        )

        assert req.positions[0].pnl == 5.0

    def test_unknown_instrument_gets_fallback_symbol(self):
        """Instrument not in map → symbol defaults to 'ID:...'."""
        req = build_commentary_request(
            run_type="market_open",
            snapshot=_make_snapshot(
                positions=[
                    {
                        "instrument_id": 9999,
                        "is_buy": False,
                        "open_rate": 10.0,
                        "amount": 5.0,
                        "units": 1.0,
                    },
                ],
                open_positions=1,
            ),
            analyses=[],
            instrument_map={},
        )

        assert req.positions[0].symbol == "ID:9999"
        assert req.positions[0].name == "Unknown"
        assert req.positions[0].direction == "Short"

    def test_builds_enriched_news_headlines(self):
        """News context with published dates and categories maps correctly."""
        req = build_commentary_request(
            run_type="market_open",
            snapshot=_make_snapshot(),
            analyses=_make_analyses(),
            instrument_map=_make_instrument_map(),
            news_context=[
                {
                    "title": "Rate hike expected",
                    "description": "Central bank meets today.",
                    "source": "Reuters",
                    "published": "2024-01-15T14:00:00+00:00",
                    "categories": ["Economy", "Central Banks"],
                },
            ],
        )

        assert len(req.news_headlines) == 1
        h = req.news_headlines[0]
        assert h.title == "Rate hike expected"
        assert h.published == "2024-01-15T14:00:00+00:00"
        assert h.categories == ("Economy", "Central Banks")

    def test_builds_news_headlines_with_missing_enrichment(self):
        """Legacy news context dicts (no published/categories) still work."""
        req = build_commentary_request(
            run_type="market_open",
            snapshot=_make_snapshot(),
            analyses=_make_analyses(),
            instrument_map=_make_instrument_map(),
            news_context=[
                {
                    "title": "Simple headline",
                    "description": "Some text.",
                    "source": "BBC",
                },
            ],
        )

        assert len(req.news_headlines) == 1
        h = req.news_headlines[0]
        assert h.published == ""
        assert h.categories == ()


# =====================================================================
# format_prompt() tests
# =====================================================================


class TestFormatPrompt:
    def test_contains_portfolio_overview(self):
        req = build_commentary_request(
            run_type="market_open",
            snapshot=_make_snapshot(),
            analyses=_make_analyses(),
            instrument_map=_make_instrument_map(),
        )
        prompt = format_prompt(req)

        assert "Market Open" in prompt
        assert "$500.00" in prompt
        assert "$50.00" in prompt
        assert "$12.50" in prompt

    def test_contains_position_data(self):
        req = build_commentary_request(
            run_type="market_open",
            snapshot=_make_snapshot(),
            analyses=_make_analyses(),
            instrument_map=_make_instrument_map(),
        )
        prompt = format_prompt(req)

        assert "BA" in prompt
        assert "Boeing Co" in prompt
        assert "bullish" in prompt
        assert "220.0000" in prompt  # support
        assert "240.0000" in prompt  # resistance
        assert "BTC" in prompt
        assert "Bitcoin" in prompt

    def test_contains_sector_data(self):
        req = build_commentary_request(
            run_type="market_open",
            snapshot=_make_snapshot(),
            analyses=_make_analyses(),
            instrument_map=_make_instrument_map(),
        )
        prompt = format_prompt(req)

        assert "Sector / Exchange Performance" in prompt
        assert "US" in prompt
        assert "Crypto" in prompt

    def test_empty_portfolio_prompt(self):
        req = CommentaryRequest(
            run_type="market_open",
            total_value=0.0,
            cash_available=0.0,
            open_positions_count=0,
            total_pnl=0.0,
        )
        prompt = format_prompt(req)

        assert "No open positions" in prompt

    def test_market_close_label(self):
        req = CommentaryRequest(
            run_type="market_close",
            total_value=100.0,
            cash_available=10.0,
            open_positions_count=0,
            total_pnl=0.0,
        )
        prompt = format_prompt(req)

        assert "Market Close" in prompt

    def test_news_headlines_include_published_date(self):
        req = CommentaryRequest(
            run_type="market_open",
            total_value=100.0,
            cash_available=10.0,
            open_positions_count=0,
            total_pnl=0.0,
            news_headlines=[
                NewsHeadline(
                    title="Rate hike expected",
                    description="Central bank meets today.",
                    source="Reuters",
                    published="2024-01-15T14:00:00+00:00",
                ),
            ],
        )
        prompt = format_prompt(req)

        assert "World News Headlines" in prompt
        assert "Rate hike expected" in prompt
        assert "[2024-01-15]" in prompt
        assert "(Reuters)" in prompt

    def test_news_headlines_include_categories(self):
        req = CommentaryRequest(
            run_type="market_open",
            total_value=100.0,
            cash_available=10.0,
            open_positions_count=0,
            total_pnl=0.0,
            news_headlines=[
                NewsHeadline(
                    title="Oil supply cuts",
                    description="OPEC reduces output.",
                    source="BBC",
                    categories=("Energy", "Commodities"),
                ),
            ],
        )
        prompt = format_prompt(req)

        assert "Topics: Energy, Commodities" in prompt

    def test_news_headlines_without_optional_fields(self):
        req = CommentaryRequest(
            run_type="market_open",
            total_value=100.0,
            cash_available=10.0,
            open_positions_count=0,
            total_pnl=0.0,
            news_headlines=[
                NewsHeadline(
                    title="Simple headline",
                    description="",
                    source="",
                ),
            ],
        )
        prompt = format_prompt(req)

        assert "Simple headline" in prompt
        assert "Topics:" not in prompt


# =====================================================================
# CommentaryResponse parsing tests
# =====================================================================


class TestCommentaryResponseParsing:
    def test_parse_valid_json(self):
        raw = _make_valid_response_json()
        resp = CommentaryResponse.model_validate_json(raw)

        assert "Mixed signals" in resp.summary
        assert len(resp.position_commentaries) == 2
        assert len(resp.recommendations) == 2

        assert resp.position_commentaries[0].symbol == "BA"
        assert resp.recommendations[0].action == "hold"
        assert resp.recommendations[0].conviction == "medium"
        assert resp.recommendations[1].action == "reduce"

    def test_parse_minimal_response(self):
        """Minimum valid response — empty lists."""
        raw = json.dumps(
            {
                "summary": "All quiet.",
                "market_context": "Nothing to report.",
                "position_commentaries": [],
                "recommendations": [],
            }
        )
        resp = CommentaryResponse.model_validate_json(raw)

        assert resp.summary == "All quiet."
        assert resp.position_commentaries == []
        assert resp.recommendations == []

    def test_parse_missing_required_field_raises(self):
        """Missing summary field → validation error."""
        raw = json.dumps(
            {
                "market_context": "Context.",
                "position_commentaries": [],
                "recommendations": [],
            }
        )
        with pytest.raises(Exception):
            CommentaryResponse.model_validate_json(raw)


# =====================================================================
# generate_commentary() tests (mocked Gemini)
# =====================================================================


class TestGenerateCommentary:
    def _make_settings(self):
        from agent.config import Settings

        return Settings(
            etoro_api_key="test",
            etoro_user_key="test",
            surreal_url="memory",
            surreal_namespace="test",
            surreal_database="test",
            surreal_user="root",
            surreal_pass="root",
            llm_provider="gemini",
            llm_api_key="test-gemini-key",
            llm_model="gemini-2.0-flash",
        )

    def _make_request(self):
        return CommentaryRequest(
            run_type="market_open",
            total_value=500.0,
            cash_available=50.0,
            open_positions_count=2,
            total_pnl=12.50,
            positions=[
                PositionData(
                    instrument_id=1010,
                    symbol="BA",
                    name="Boeing Co",
                    direction="Long",
                    open_rate=228.67,
                    amount=20.0,
                    units=0.0875,
                    pnl=1.36,
                    trend="bullish",
                    trend_strength=0.72,
                    support=220.0,
                    resistance=240.0,
                    momentum_signal="bullish",
                    sector_group="US",
                    sector_avg_return_pct=1.25,
                ),
            ],
        )

    @patch("google.genai.Client")
    def test_generate_commentary_success(self, mock_client_cls):
        """Mock Gemini returning valid JSON → parsed CommentaryResponse."""
        mock_response = MagicMock()
        mock_response.text = _make_valid_response_json()

        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response
        mock_client_cls.return_value = mock_client

        result = generate_commentary(self._make_request(), self._make_settings())

        assert isinstance(result, CommentaryResponse)
        assert "Mixed signals" in result.summary
        assert len(result.recommendations) == 2

        # Verify the client was created with the API key
        mock_client_cls.assert_called_once_with(api_key="test-gemini-key")

    @patch("google.genai.Client")
    def test_generate_commentary_api_error(self, mock_client_cls):
        """Gemini API raises → RuntimeError with clear message."""
        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = Exception(
            "API quota exceeded"
        )
        mock_client_cls.return_value = mock_client

        with pytest.raises(RuntimeError, match="Gemini API call failed"):
            generate_commentary(self._make_request(), self._make_settings())

    @patch("google.genai.Client")
    def test_generate_commentary_invalid_json(self, mock_client_cls):
        """Gemini returns non-JSON → RuntimeError."""
        mock_response = MagicMock()
        mock_response.text = "This is not valid JSON at all"

        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response
        mock_client_cls.return_value = mock_client

        with pytest.raises(RuntimeError, match="Failed to parse"):
            generate_commentary(self._make_request(), self._make_settings())
