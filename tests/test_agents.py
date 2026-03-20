"""Tests for the multi-agent pipeline framework.

Covers:
- PipelineState construction
- BaseSpecialist ABC contract
- Registry (register / get / list / clear)
- AgentContext construction
- Graph builder with model=None (deterministic routing)
- Specialist procedural execution against in-memory SurrealDB
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from surrealdb.connections.sync_template import SyncTemplate

from agent.agents.base import AgentContext, BaseSpecialist
from agent.agents.graph import build_pipeline_graph, _fallback_next
from agent.agents.registry import (
    clear_registry,
    get_all_specialists,
    get_specialist,
    register_specialist,
)
from agent.agents.state import PipelineState
from agent.config import Settings


# ---------------------------------------------------------------------------
# PipelineState
# ---------------------------------------------------------------------------


class TestPipelineState:
    """Verify PipelineState TypedDict construction."""

    def test_empty_state(self) -> None:
        state: PipelineState = {}  # type: ignore[typeddict-item]
        assert state == {}

    def test_full_state(self) -> None:
        state: PipelineState = {
            "run_id": "abc-123",
            "run_type": "market_open",
            "next_specialist": "data",
            "completed_stages": ["data"],
            "snapshot_id": "snap:1",
            "instrument_ids": [1001],
            "instrument_map": {},
            "candle_counts": {1001: 100},
            "analyses_created": 1,
            "commentary": None,
            "report": None,
            "report_path": None,
            "errors": [],
            "start_time": 0.0,
            "duration_ms": 500,
        }
        assert state["run_id"] == "abc-123"
        assert state["completed_stages"] == ["data"]


# ---------------------------------------------------------------------------
# AgentContext
# ---------------------------------------------------------------------------


class TestAgentContext:
    """Verify AgentContext dataclass."""

    def test_construction(self) -> None:
        ctx = AgentContext(
            db=MagicMock(),
            client=MagicMock(),
            settings=MagicMock(),
            run_id="run-1",
            run_type="market_open",
        )
        assert ctx.run_id == "run-1"
        assert ctx.run_type == "market_open"
        assert ctx.generate_fn is None

    def test_generate_fn(self) -> None:
        fn = MagicMock()
        ctx = AgentContext(
            db=MagicMock(),
            client=MagicMock(),
            settings=MagicMock(),
            run_id="r",
            run_type="market_open",
            generate_fn=fn,
        )
        assert ctx.generate_fn is fn


# ---------------------------------------------------------------------------
# BaseSpecialist ABC
# ---------------------------------------------------------------------------


class _DummySpecialist(BaseSpecialist):
    """Minimal concrete implementation for testing."""

    @property
    def name(self) -> str:
        return "dummy"

    @property
    def description(self) -> str:
        return "A dummy specialist for testing."

    def create_tools(self, ctx: AgentContext) -> list[Any]:
        return []

    def get_system_prompt(self) -> str:
        return "You are a dummy."

    def process_results(self, state: dict[str, Any], ctx: AgentContext) -> dict[str, Any]:
        return {"dummy_done": True}


class TestBaseSpecialist:
    """Verify the ABC contract."""

    def test_concrete_subclass(self) -> None:
        s = _DummySpecialist()
        assert s.name == "dummy"
        assert s.description == "A dummy specialist for testing."
        assert s.get_system_prompt() == "You are a dummy."
        assert s.create_tools(MagicMock()) == []
        assert s.process_results({}, MagicMock()) == {"dummy_done": True}

    def test_run_procedural_default_raises(self) -> None:
        s = _DummySpecialist()
        with pytest.raises(NotImplementedError, match="dummy"):
            s.run_procedural({}, MagicMock())


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class TestRegistry:
    """Verify specialist registration and lookup."""

    def setup_method(self) -> None:
        # Save original registry state so other tests aren't affected
        from agent.agents.registry import _REGISTRY

        self._saved_registry = dict(_REGISTRY)
        clear_registry()

    def teardown_method(self) -> None:
        # Restore original registry state (other tests depend on it)
        from agent.agents.registry import _REGISTRY

        clear_registry()
        _REGISTRY.update(self._saved_registry)

    def test_register_and_get(self) -> None:
        s = _DummySpecialist()
        register_specialist(s)
        assert get_specialist("dummy") is s

    def test_get_missing_raises(self) -> None:
        with pytest.raises(KeyError, match="nonexistent"):
            get_specialist("nonexistent")

    def test_get_all(self) -> None:
        s = _DummySpecialist()
        register_specialist(s)
        all_specs = get_all_specialists()
        assert len(all_specs) == 1
        assert all_specs[0].name == "dummy"

    def test_clear(self) -> None:
        register_specialist(_DummySpecialist())
        clear_registry()
        assert get_all_specialists() == []

    def test_duplicate_register_raises(self) -> None:
        s1 = _DummySpecialist()
        s2 = _DummySpecialist()
        register_specialist(s1)
        with pytest.raises(ValueError, match="already registered"):
            register_specialist(s2)


# ---------------------------------------------------------------------------
# Fallback routing
# ---------------------------------------------------------------------------


class TestFallbackRouting:
    """Verify deterministic fallback order."""

    def test_starts_with_data(self) -> None:
        available = {"data", "analysis", "news", "commentary", "report"}
        assert _fallback_next([], available) == "data"

    def test_after_data(self) -> None:
        available = {"data", "analysis", "news", "commentary", "report"}
        assert _fallback_next(["data"], available) == "analysis"

    def test_after_analysis(self) -> None:
        available = {"data", "analysis", "news", "commentary", "report"}
        assert _fallback_next(["data", "analysis"], available) == "news"

    def test_after_news(self) -> None:
        available = {"data", "analysis", "news", "commentary", "report"}
        assert _fallback_next(["data", "analysis", "news"], available) == "commentary"

    def test_after_all(self) -> None:
        available = {"data", "analysis", "news", "commentary", "report"}
        assert _fallback_next(
            ["data", "analysis", "news", "commentary", "report"], available
        ) == "done"

    def test_skips_unavailable(self) -> None:
        available = {"data", "report"}
        assert _fallback_next(["data"], available) == "report"


# ---------------------------------------------------------------------------
# Graph builder (model=None, deterministic)
# ---------------------------------------------------------------------------


class _FinishSpecialist(BaseSpecialist):
    """A specialist that does nothing (for graph tests)."""

    def __init__(self, specialist_name: str) -> None:
        self._name = specialist_name

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"{self._name} specialist"

    def create_tools(self, ctx: AgentContext) -> list[Any]:
        return []

    def get_system_prompt(self) -> str:
        return ""

    def process_results(self, state: dict[str, Any], ctx: AgentContext) -> dict[str, Any]:
        return {}

    def run_procedural(self, state: dict[str, Any], ctx: AgentContext) -> None:
        pass


class TestGraphBuilder:
    """Verify graph construction and deterministic execution."""

    def test_graph_compiles(self) -> None:
        specialists = [_FinishSpecialist(n) for n in ("data", "analysis", "news", "commentary", "report")]
        ctx = AgentContext(
            db=MagicMock(),
            client=MagicMock(),
            settings=MagicMock(),
            run_id="test",
            run_type="market_open",
        )
        graph = build_pipeline_graph(specialists, None, ctx)
        assert graph is not None

    def test_deterministic_routing_completes(self) -> None:
        specialists = [_FinishSpecialist(n) for n in ("data", "analysis", "news", "commentary", "report")]
        ctx = AgentContext(
            db=MagicMock(),
            client=MagicMock(),
            settings=MagicMock(),
            run_id="test",
            run_type="market_open",
        )
        graph = build_pipeline_graph(specialists, None, ctx)
        result = graph.invoke({
            "run_id": "test",
            "run_type": "market_open",
            "next_specialist": "",
            "completed_stages": [],
            "errors": [],
        })
        assert set(result["completed_stages"]) == {"data", "analysis", "news", "commentary", "report"}
        assert result["next_specialist"] == "done"

    def test_specialists_run_in_correct_order(self) -> None:
        order: list[str] = []

        class _TrackingSpecialist(_FinishSpecialist):
            def run_procedural(self, state: dict[str, Any], ctx: AgentContext) -> None:
                order.append(self.name)

        specialists = [_TrackingSpecialist(n) for n in ("data", "analysis", "news", "commentary", "report")]
        ctx = AgentContext(
            db=MagicMock(),
            client=MagicMock(),
            settings=MagicMock(),
            run_id="test",
            run_type="market_open",
        )
        graph = build_pipeline_graph(specialists, None, ctx)
        graph.invoke({
            "run_id": "test",
            "run_type": "market_open",
            "next_specialist": "",
            "completed_stages": [],
            "errors": [],
        })
        assert order == ["data", "analysis", "news", "commentary", "report"]


# ---------------------------------------------------------------------------
# DataSpecialist procedural
# ---------------------------------------------------------------------------


class TestDataSpecialistProcedural:
    """Test DataSpecialist.run_procedural against in-memory DB."""

    def test_fetches_portfolio_and_candles(
        self, db: SyncTemplate, test_settings: Settings, httpx_mock
    ) -> None:
        from agent.agents.specialists.data import DataSpecialist
        from agent.db.candles import count_candles
        from agent.db.snapshots import get_latest_snapshot
        from agent.etoro.client import EToroClient

        httpx_mock.add_response(
            url="https://example.com/trading/info/real/pnl",
            json=_portfolio_response(1001),
        )
        httpx_mock.add_response(
            url="https://example.com/market-data/instruments",
            json=_instruments_response(
                {"instrumentID": 1001, "symbolFull": "AAPL",
                 "instrumentDisplayName": "Apple", "instrumentTypeID": 5,
                 "exchangeID": 10},
            ),
        )
        httpx_mock.add_response(
            url="https://example.com/market-data/instruments/1001/history/candles/desc/OneDay/100",
            json=_candles_response(1001),
        )

        with EToroClient(test_settings) as client:
            ctx = AgentContext(
                db=db, client=client, settings=test_settings,
                run_id="run-1", run_type="market_open",
            )
            specialist = DataSpecialist()
            state: dict[str, Any] = {"errors": []}
            specialist.run_procedural(state, ctx)
            updates = specialist.process_results(state, ctx)

        assert updates["snapshot_id"] != ""
        assert 1001 in updates["instrument_ids"]
        assert updates["candle_counts"][1001] == 3

    def test_portfolio_failure_raises(
        self, db: SyncTemplate, test_settings: Settings, httpx_mock
    ) -> None:
        from agent.agents.specialists.data import DataSpecialist
        from agent.etoro.client import EToroClient

        for _ in range(3):
            httpx_mock.add_response(
                url="https://example.com/trading/info/real/pnl",
                status_code=500,
            )

        with EToroClient(test_settings) as client:
            ctx = AgentContext(
                db=db, client=client, settings=test_settings,
                run_id="run-1", run_type="market_open",
            )
            specialist = DataSpecialist()
            with pytest.raises(RuntimeError, match="Portfolio fetch failed"):
                specialist.run_procedural({"errors": []}, ctx)

    def test_adaptive_candle_count_for_crypto(
        self, db: SyncTemplate, test_settings: Settings, httpx_mock
    ) -> None:
        """Crypto instruments should get 200 candles, stocks get 100."""
        from agent.agents.specialists.data import DataSpecialist
        from agent.db.candles import count_candles
        from agent.etoro.client import EToroClient

        # Portfolio with one stock (1001) and one crypto (2001)
        httpx_mock.add_response(
            url="https://example.com/trading/info/real/pnl",
            json=_portfolio_response(1001, 2001),
        )
        httpx_mock.add_response(
            url="https://example.com/market-data/instruments",
            json=_instruments_response(
                {"instrumentID": 1001, "symbolFull": "AAPL",
                 "instrumentDisplayName": "Apple", "instrumentTypeID": 5,
                 "exchangeID": 10},
                {"instrumentID": 2001, "symbolFull": "BTC",
                 "instrumentDisplayName": "Bitcoin", "instrumentTypeID": 10,
                 "exchangeID": 8},
            ),
        )
        # Stock: fetched with count=100
        httpx_mock.add_response(
            url="https://example.com/market-data/instruments/1001/history/candles/desc/OneDay/100",
            json=_candles_response(1001, count=5),
        )
        # Crypto: fetched with count=200 (adaptive)
        httpx_mock.add_response(
            url="https://example.com/market-data/instruments/2001/history/candles/desc/OneDay/200",
            json=_candles_response(2001, count=10),
        )

        with EToroClient(test_settings) as client:
            ctx = AgentContext(
                db=db, client=client, settings=test_settings,
                run_id="run-1", run_type="market_open",
            )
            specialist = DataSpecialist()
            state: dict[str, Any] = {"errors": []}
            specialist.run_procedural(state, ctx)
            updates = specialist.process_results(state, ctx)

        # Stock got 5 candles, crypto got 10
        assert updates["candle_counts"][1001] == 5
        assert updates["candle_counts"][2001] == 10
        assert state["errors"] == []


# ---------------------------------------------------------------------------
# AnalysisSpecialist procedural
# ---------------------------------------------------------------------------


class TestAnalysisSpecialistProcedural:
    """Test AnalysisSpecialist.run_procedural against in-memory DB."""

    def test_creates_analysis_records(
        self, db: SyncTemplate, test_settings: Settings, httpx_mock
    ) -> None:
        from agent.agents.specialists.analysis import AnalysisSpecialist
        from agent.agents.specialists.data import DataSpecialist
        from agent.db.analysis import get_analyses_by_run_id
        from agent.etoro.client import EToroClient

        # Populate data first
        httpx_mock.add_response(
            url="https://example.com/trading/info/real/pnl",
            json=_portfolio_response(1001),
        )
        httpx_mock.add_response(
            url="https://example.com/market-data/instruments",
            json=_instruments_response(
                {"instrumentID": 1001, "symbolFull": "AAPL",
                 "instrumentDisplayName": "Apple", "instrumentTypeID": 5,
                 "exchangeID": 10},
            ),
        )
        httpx_mock.add_response(
            url="https://example.com/market-data/instruments/1001/history/candles/desc/OneDay/100",
            json=_candles_response(1001, count=15),
        )

        with EToroClient(test_settings) as client:
            ctx = AgentContext(
                db=db, client=client, settings=test_settings,
                run_id="run-1", run_type="market_open",
            )
            data_spec = DataSpecialist()
            state: dict[str, Any] = {"errors": []}
            data_spec.run_procedural(state, ctx)
            data_updates = data_spec.process_results(state, ctx)
            state.update(data_updates)

            analysis_spec = AnalysisSpecialist()
            analysis_spec.run_procedural(state, ctx)
            analysis_updates = analysis_spec.process_results(state, ctx)

        assert analysis_updates["analyses_created"] == 1
        analyses = get_analyses_by_run_id(db, "run-1")
        assert len(analyses) == 1
        assert analyses[0]["trend"] in ("bullish", "bearish", "neutral")


# ---------------------------------------------------------------------------
# NewsSpecialist procedural
# ---------------------------------------------------------------------------

_RSS_FEED_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>BBC Business News</title>
    <item>
      <title>Markets rally on trade deal</title>
      <description>Global markets surged today.</description>
    </item>
    <item>
      <title>Oil prices drop</title>
      <description>Crude fell 3% overnight.</description>
    </item>
  </channel>
</rss>"""


class TestNewsSpecialistProcedural:
    """Test NewsSpecialist.run_procedural."""

    def test_skips_without_feed_url(self, test_settings: Settings) -> None:
        from agent.agents.specialists.news import NewsSpecialist

        test_settings.news_api_url = ""
        ctx = AgentContext(
            db=MagicMock(), client=MagicMock(), settings=test_settings,
            run_id="run-1", run_type="market_open",
        )
        spec = NewsSpecialist()
        state: dict[str, Any] = {}
        spec.run_procedural(state, ctx)
        result = spec.process_results(state, ctx)
        assert result["news_context"] is None

    def test_fetches_headlines(self, test_settings: Settings, httpx_mock) -> None:
        from agent.agents.specialists.news import NewsSpecialist

        test_settings.news_api_url = "https://rss.example.com/business.xml"

        httpx_mock.add_response(
            url="https://rss.example.com/business.xml",
            text=_RSS_FEED_XML,
        )

        ctx = AgentContext(
            db=MagicMock(), client=MagicMock(), settings=test_settings,
            run_id="run-1", run_type="market_open",
        )
        spec = NewsSpecialist()
        state: dict[str, Any] = {}
        spec.run_procedural(state, ctx)
        result = spec.process_results(state, ctx)

        assert result["news_context"] is not None
        assert len(result["news_context"]) == 2
        assert result["news_context"][0]["title"] == "Markets rally on trade deal"
        assert result["news_context"][0]["source"] == "BBC Business News"
        assert result["news_context"][1]["title"] == "Oil prices drop"

    def test_handles_feed_failure_gracefully(
        self, test_settings: Settings, httpx_mock
    ) -> None:
        from agent.agents.specialists.news import NewsSpecialist

        test_settings.news_api_url = "https://rss.example.com/business.xml"

        httpx_mock.add_response(
            url="https://rss.example.com/business.xml",
            status_code=500,
        )

        ctx = AgentContext(
            db=MagicMock(), client=MagicMock(), settings=test_settings,
            run_id="run-1", run_type="market_open",
        )
        spec = NewsSpecialist()
        state: dict[str, Any] = {}
        spec.run_procedural(state, ctx)
        result = spec.process_results(state, ctx)
        assert result["news_context"] is None

    def test_specialist_properties(self) -> None:
        from agent.agents.specialists.news import NewsSpecialist

        spec = NewsSpecialist()
        assert spec.name == "news"
        assert "news" in spec.description.lower()
        assert spec.get_system_prompt() != ""

    def test_create_tools_returns_fetch_tool(self) -> None:
        from agent.agents.specialists.news import NewsSpecialist

        ctx = AgentContext(
            db=MagicMock(), client=MagicMock(), settings=MagicMock(),
            run_id="run-1", run_type="market_open",
        )
        spec = NewsSpecialist()
        tools = spec.create_tools(ctx)
        assert len(tools) == 1
        assert tools[0].name == "fetch_news"

    def test_multi_feed_procedural(
        self, test_settings: Settings, httpx_mock
    ) -> None:
        from agent.agents.specialists.news import NewsSpecialist

        rss_b = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>World News</title>
    <item>
      <title>Geopolitical tensions rise</title>
      <description>Tensions escalated overnight.</description>
    </item>
  </channel>
</rss>"""

        test_settings.news_api_url = (
            "https://rss.example.com/business.xml,"
            "https://rss.example.com/world.xml"
        )
        httpx_mock.add_response(
            url="https://rss.example.com/business.xml",
            text=_RSS_FEED_XML,
        )
        httpx_mock.add_response(
            url="https://rss.example.com/world.xml",
            text=rss_b,
        )

        ctx = AgentContext(
            db=MagicMock(), client=MagicMock(), settings=test_settings,
            run_id="run-1", run_type="market_open",
        )
        spec = NewsSpecialist()
        state: dict[str, Any] = {}
        spec.run_procedural(state, ctx)
        result = spec.process_results(state, ctx)

        assert result["news_context"] is not None
        assert len(result["news_context"]) == 3
        sources = {h["source"] for h in result["news_context"]}
        assert "BBC Business News" in sources
        assert "World News" in sources

    def test_headlines_include_published_and_categories(
        self, test_settings: Settings, httpx_mock
    ) -> None:
        from agent.agents.specialists.news import NewsSpecialist

        rss = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Test Feed</title>
    <item>
      <title>Rich headline</title>
      <description>&lt;p&gt;HTML desc&lt;/p&gt;</description>
      <pubDate>Mon, 01 Jan 2024 12:00:00 GMT</pubDate>
      <category>Economy</category>
      <category>Markets</category>
    </item>
  </channel>
</rss>"""

        test_settings.news_api_url = "https://rss.example.com/rich.xml"
        httpx_mock.add_response(
            url="https://rss.example.com/rich.xml",
            text=rss,
        )

        ctx = AgentContext(
            db=MagicMock(), client=MagicMock(), settings=test_settings,
            run_id="run-1", run_type="market_open",
        )
        spec = NewsSpecialist()
        state: dict[str, Any] = {}
        spec.run_procedural(state, ctx)
        result = spec.process_results(state, ctx)

        assert result["news_context"] is not None
        headline = result["news_context"][0]
        assert headline["published"].startswith("2024-01-01")
        assert headline["categories"] == ["Economy", "Markets"]
        assert headline["description"] == "HTML desc"


# ---------------------------------------------------------------------------
# fetch_news_headlines unit tests
# ---------------------------------------------------------------------------


class TestFetchNewsHeadlines:
    """Test the fetch_news_headlines helper function."""

    def test_parses_rss_items(self, httpx_mock) -> None:
        from agent.agents.specialists.news import fetch_news_headlines

        httpx_mock.add_response(
            url="https://rss.example.com/feed.xml",
            text=_RSS_FEED_XML,
        )
        result = fetch_news_headlines("https://rss.example.com/feed.xml")
        assert len(result) == 2
        assert result[0]["title"] == "Markets rally on trade deal"
        assert result[0]["description"] == "Global markets surged today."
        assert result[0]["source"] == "BBC Business News"

    def test_returns_empty_on_http_error(self, httpx_mock) -> None:
        from agent.agents.specialists.news import fetch_news_headlines

        httpx_mock.add_response(
            url="https://rss.example.com/feed.xml",
            status_code=500,
        )
        result = fetch_news_headlines("https://rss.example.com/feed.xml")
        assert result == []

    def test_returns_empty_on_invalid_xml(self, httpx_mock) -> None:
        from agent.agents.specialists.news import fetch_news_headlines

        httpx_mock.add_response(
            url="https://rss.example.com/feed.xml",
            text="this is not xml",
        )
        result = fetch_news_headlines("https://rss.example.com/feed.xml")
        assert result == []

    def test_handles_missing_description(self, httpx_mock) -> None:
        from agent.agents.specialists.news import fetch_news_headlines

        rss = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>News Feed</title>
    <item><title>Headline only</title></item>
  </channel>
</rss>"""
        httpx_mock.add_response(
            url="https://rss.example.com/feed.xml",
            text=rss,
        )
        result = fetch_news_headlines("https://rss.example.com/feed.xml")
        assert len(result) == 1
        assert result[0]["title"] == "Headline only"
        assert result[0]["description"] == ""
        assert result[0]["source"] == "News Feed"

    def test_respects_max_items(self, httpx_mock) -> None:
        from agent.agents.specialists.news import fetch_news_headlines

        httpx_mock.add_response(
            url="https://rss.example.com/feed.xml",
            text=_RSS_FEED_XML,
        )
        result = fetch_news_headlines(
            "https://rss.example.com/feed.xml", max_items=1,
        )
        assert len(result) == 1

    def test_parses_pub_date(self, httpx_mock) -> None:
        from agent.agents.specialists.news import fetch_news_headlines

        rss = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>News Feed</title>
    <item>
      <title>Dated headline</title>
      <description>With a date.</description>
      <pubDate>Mon, 01 Jan 2024 12:00:00 GMT</pubDate>
    </item>
  </channel>
</rss>"""
        httpx_mock.add_response(
            url="https://rss.example.com/feed.xml",
            text=rss,
        )
        result = fetch_news_headlines("https://rss.example.com/feed.xml")
        assert len(result) == 1
        assert result[0]["published"].startswith("2024-01-01")

    def test_parses_categories(self, httpx_mock) -> None:
        from agent.agents.specialists.news import fetch_news_headlines

        rss = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>News Feed</title>
    <item>
      <title>Categorised headline</title>
      <description>Tagged story.</description>
      <category>Economy</category>
      <category>Markets</category>
    </item>
  </channel>
</rss>"""
        httpx_mock.add_response(
            url="https://rss.example.com/feed.xml",
            text=rss,
        )
        result = fetch_news_headlines("https://rss.example.com/feed.xml")
        assert len(result) == 1
        assert result[0]["categories"] == ["Economy", "Markets"]

    def test_strips_html_from_description(self, httpx_mock) -> None:
        from agent.agents.specialists.news import fetch_news_headlines

        rss = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>News Feed</title>
    <item>
      <title>HTML description</title>
      <description>&lt;p&gt;Some &lt;b&gt;bold&lt;/b&gt; text &amp;amp; more.&lt;/p&gt;</description>
    </item>
  </channel>
</rss>"""
        httpx_mock.add_response(
            url="https://rss.example.com/feed.xml",
            text=rss,
        )
        result = fetch_news_headlines("https://rss.example.com/feed.xml")
        assert len(result) == 1
        assert result[0]["description"] == "Some bold text & more."

    def test_missing_pub_date_returns_empty_string(self, httpx_mock) -> None:
        from agent.agents.specialists.news import fetch_news_headlines

        httpx_mock.add_response(
            url="https://rss.example.com/feed.xml",
            text=_RSS_FEED_XML,
        )
        result = fetch_news_headlines("https://rss.example.com/feed.xml")
        assert result[0]["published"] == ""

    def test_missing_categories_returns_empty_list(self, httpx_mock) -> None:
        from agent.agents.specialists.news import fetch_news_headlines

        httpx_mock.add_response(
            url="https://rss.example.com/feed.xml",
            text=_RSS_FEED_XML,
        )
        result = fetch_news_headlines("https://rss.example.com/feed.xml")
        assert result[0]["categories"] == []


# ---------------------------------------------------------------------------
# _strip_html unit tests
# ---------------------------------------------------------------------------


class TestStripHtml:
    """Test the _strip_html helper function."""

    def test_removes_html_tags(self) -> None:
        from agent.agents.specialists.news import _strip_html

        assert _strip_html("<p>Hello <b>world</b></p>") == "Hello world"

    def test_decodes_html_entities(self) -> None:
        from agent.agents.specialists.news import _strip_html

        assert _strip_html("&amp; &lt; &gt;") == "& < >"

    def test_handles_plain_text(self) -> None:
        from agent.agents.specialists.news import _strip_html

        assert _strip_html("No tags here") == "No tags here"

    def test_handles_empty_string(self) -> None:
        from agent.agents.specialists.news import _strip_html

        assert _strip_html("") == ""


# ---------------------------------------------------------------------------
# _parse_feed_urls unit tests
# ---------------------------------------------------------------------------


class TestParseFeedUrls:
    """Test the _parse_feed_urls helper function."""

    def test_single_url(self) -> None:
        from agent.agents.specialists.news import _parse_feed_urls

        result = _parse_feed_urls("https://example.com/feed.xml")
        assert result == ["https://example.com/feed.xml"]

    def test_multiple_urls(self) -> None:
        from agent.agents.specialists.news import _parse_feed_urls

        result = _parse_feed_urls(
            "https://example.com/a.xml,https://example.com/b.xml"
        )
        assert result == [
            "https://example.com/a.xml",
            "https://example.com/b.xml",
        ]

    def test_strips_whitespace(self) -> None:
        from agent.agents.specialists.news import _parse_feed_urls

        result = _parse_feed_urls(
            " https://example.com/a.xml , https://example.com/b.xml "
        )
        assert result == [
            "https://example.com/a.xml",
            "https://example.com/b.xml",
        ]

    def test_empty_string(self) -> None:
        from agent.agents.specialists.news import _parse_feed_urls

        assert _parse_feed_urls("") == []

    def test_ignores_empty_entries(self) -> None:
        from agent.agents.specialists.news import _parse_feed_urls

        result = _parse_feed_urls("https://example.com/a.xml,,")
        assert result == ["https://example.com/a.xml"]


# ---------------------------------------------------------------------------
# fetch_all_news_headlines unit tests
# ---------------------------------------------------------------------------


class TestFetchAllNewsHeadlines:
    """Test the fetch_all_news_headlines multi-feed function."""

    def test_merges_multiple_feeds(self, httpx_mock) -> None:
        from agent.agents.specialists.news import fetch_all_news_headlines

        rss_a = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Feed A</title>
    <item><title>Story A</title><description>Desc A</description></item>
  </channel>
</rss>"""
        rss_b = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Feed B</title>
    <item><title>Story B</title><description>Desc B</description></item>
  </channel>
</rss>"""
        httpx_mock.add_response(url="https://a.example.com/feed.xml", text=rss_a)
        httpx_mock.add_response(url="https://b.example.com/feed.xml", text=rss_b)

        result = fetch_all_news_headlines([
            "https://a.example.com/feed.xml",
            "https://b.example.com/feed.xml",
        ])
        assert len(result) == 2
        titles = {h["title"] for h in result}
        assert titles == {"Story A", "Story B"}

    def test_deduplicates_by_title(self, httpx_mock) -> None:
        from agent.agents.specialists.news import fetch_all_news_headlines

        rss_a = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Feed A</title>
    <item><title>Same headline</title><description>From A</description></item>
  </channel>
</rss>"""
        rss_b = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Feed B</title>
    <item><title>Same headline</title><description>From B</description></item>
  </channel>
</rss>"""
        httpx_mock.add_response(url="https://a.example.com/feed.xml", text=rss_a)
        httpx_mock.add_response(url="https://b.example.com/feed.xml", text=rss_b)

        result = fetch_all_news_headlines([
            "https://a.example.com/feed.xml",
            "https://b.example.com/feed.xml",
        ])
        assert len(result) == 1

    def test_sorts_by_published_date_descending(self, httpx_mock) -> None:
        from agent.agents.specialists.news import fetch_all_news_headlines

        rss = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Feed</title>
    <item>
      <title>Older</title>
      <description>Old</description>
      <pubDate>Mon, 01 Jan 2024 10:00:00 GMT</pubDate>
    </item>
    <item>
      <title>Newer</title>
      <description>New</description>
      <pubDate>Tue, 02 Jan 2024 10:00:00 GMT</pubDate>
    </item>
  </channel>
</rss>"""
        httpx_mock.add_response(url="https://x.example.com/feed.xml", text=rss)
        result = fetch_all_news_headlines(["https://x.example.com/feed.xml"])
        assert result[0]["title"] == "Newer"
        assert result[1]["title"] == "Older"

    def test_respects_max_items(self, httpx_mock) -> None:
        from agent.agents.specialists.news import fetch_all_news_headlines

        httpx_mock.add_response(
            url="https://rss.example.com/feed.xml",
            text=_RSS_FEED_XML,
        )
        result = fetch_all_news_headlines(
            ["https://rss.example.com/feed.xml"], max_items=1,
        )
        assert len(result) == 1

    def test_handles_partial_feed_failure(self, httpx_mock) -> None:
        from agent.agents.specialists.news import fetch_all_news_headlines

        httpx_mock.add_response(
            url="https://a.example.com/feed.xml",
            status_code=500,
        )
        httpx_mock.add_response(
            url="https://b.example.com/feed.xml",
            text=_RSS_FEED_XML,
        )

        result = fetch_all_news_headlines([
            "https://a.example.com/feed.xml",
            "https://b.example.com/feed.xml",
        ])
        assert len(result) == 2

    def test_empty_url_list(self) -> None:
        from agent.agents.specialists.news import fetch_all_news_headlines

        assert fetch_all_news_headlines([]) == []


# ---------------------------------------------------------------------------
# CommentarySpecialist procedural
# ---------------------------------------------------------------------------


class TestCommentarySpecialistProcedural:
    """Test CommentarySpecialist.run_procedural."""

    def test_skips_without_api_key(
        self, db: SyncTemplate, test_settings: Settings
    ) -> None:
        from agent.agents.specialists.commentary import CommentarySpecialist

        test_settings.llm_api_key = ""
        ctx = AgentContext(
            db=db, client=MagicMock(), settings=test_settings,
            run_id="run-1", run_type="market_open",
        )
        spec = CommentarySpecialist()
        state: dict[str, Any] = {}
        spec.run_procedural(state, ctx)
        result = spec.process_results(state, ctx)
        assert result["commentary"] is None

    def test_generates_commentary(
        self, db: SyncTemplate, test_settings: Settings, httpx_mock
    ) -> None:
        from agent.agents.specialists.analysis import AnalysisSpecialist
        from agent.agents.specialists.commentary import CommentarySpecialist
        from agent.agents.specialists.data import DataSpecialist
        from agent.etoro.client import EToroClient
        from agent.reporting.llm import (
            CommentaryResponse,
            PositionCommentary,
            Recommendation,
        )

        httpx_mock.add_response(
            url="https://example.com/trading/info/real/pnl",
            json=_portfolio_response(1001),
        )
        httpx_mock.add_response(
            url="https://example.com/market-data/instruments",
            json=_instruments_response(
                {"instrumentID": 1001, "symbolFull": "AAPL",
                 "instrumentDisplayName": "Apple", "instrumentTypeID": 5,
                 "exchangeID": 10},
            ),
        )
        httpx_mock.add_response(
            url="https://example.com/market-data/instruments/1001/history/candles/desc/OneDay/100",
            json=_candles_response(1001, count=15),
        )

        test_settings.llm_api_key = "test-key"
        mock_resp = CommentaryResponse(
            summary="Test summary",
            market_context="Test context",
            position_commentaries=[
                PositionCommentary(
                    instrument_id=1001, symbol="AAPL",
                    commentary="AAPL looks good.",
                ),
            ],
            recommendations=[
                Recommendation(
                    instrument_id=1001, symbol="AAPL",
                    action="hold", conviction="medium",
                    reasoning="Hold AAPL.",
                ),
            ],
        )

        with EToroClient(test_settings) as client:
            ctx = AgentContext(
                db=db, client=client, settings=test_settings,
                run_id="run-1", run_type="market_open",
                generate_fn=MagicMock(return_value=mock_resp),
            )
            # Run data + analysis first
            data_spec = DataSpecialist()
            state: dict[str, Any] = {"errors": []}
            data_spec.run_procedural(state, ctx)
            state.update(data_spec.process_results(state, ctx))

            analysis_spec = AnalysisSpecialist()
            analysis_spec.run_procedural(state, ctx)
            state.update(analysis_spec.process_results(state, ctx))

            # Run commentary
            comm_spec = CommentarySpecialist()
            comm_spec.run_procedural(state, ctx)
            result = comm_spec.process_results(state, ctx)

        assert result["commentary"] is not None
        assert result["commentary"]["summary"] == "Test summary"
        assert "report_id" in result["commentary"]


# ---------------------------------------------------------------------------
# ReportSpecialist procedural
# ---------------------------------------------------------------------------


class TestReportSpecialistProcedural:
    """Test ReportSpecialist.run_procedural."""

    def test_assembles_report(
        self, db: SyncTemplate, test_settings: Settings, httpx_mock
    ) -> None:
        from agent.agents.specialists.data import DataSpecialist
        from agent.agents.specialists.report import ReportSpecialist
        from agent.etoro.client import EToroClient
        from agent.reporting.generator import Report

        httpx_mock.add_response(
            url="https://example.com/trading/info/real/pnl",
            json=_portfolio_response(1001),
        )
        httpx_mock.add_response(
            url="https://example.com/market-data/instruments",
            json=_instruments_response(
                {"instrumentID": 1001, "symbolFull": "AAPL",
                 "instrumentDisplayName": "Apple", "instrumentTypeID": 5,
                 "exchangeID": 10},
            ),
        )
        httpx_mock.add_response(
            url="https://example.com/market-data/instruments/1001/history/candles/desc/OneDay/100",
            json=_candles_response(1001, count=15),
        )

        with EToroClient(test_settings) as client:
            ctx = AgentContext(
                db=db, client=client, settings=test_settings,
                run_id="run-1", run_type="market_open",
            )
            data_spec = DataSpecialist()
            state: dict[str, Any] = {"errors": []}
            data_spec.run_procedural(state, ctx)
            state.update(data_spec.process_results(state, ctx))

            report_spec = ReportSpecialist()
            report_spec.run_procedural(state, ctx)
            result = report_spec.process_results(state, ctx)

        assert isinstance(result["report"], Report)
        assert result["report_path"] is not None


# ---------------------------------------------------------------------------
# Shared test data helpers (duplicated from test_orchestrator.py)
# ---------------------------------------------------------------------------

from datetime import datetime, timezone


def _portfolio_response(*instrument_ids: int) -> dict:
    positions = []
    for idx, iid in enumerate(instrument_ids, start=1):
        positions.append(
            {
                "positionID": 10000 + idx,
                "CID": 1,
                "openDateTime": "2024-01-01T10:00:00Z",
                "openRate": 150.0,
                "instrumentID": iid,
                "isBuy": True,
                "takeProfitRate": 200.0,
                "stopLossRate": 100.0,
                "amount": 1000.0,
                "leverage": 1,
                "orderID": 20000 + idx,
                "orderType": 1,
                "units": 10.0,
                "totalFees": 0.0,
                "initialAmountInDollars": 1000.0,
                "isTslEnabled": False,
                "initialUnits": 10.0,
                "isPartiallyAltered": False,
                "unitsBaseValueDollars": 1000.0,
                "settlementTypeID": 1,
                "openConversionRate": 1.0,
                "totalExternalFees": 0.0,
                "totalExternalTaxes": 0.0,
                "isNoTakeProfit": False,
                "isNoStopLoss": False,
                "lotCount": 1.0,
            }
        )
    return {
        "clientPortfolio": {
            "positions": positions,
            "credit": 5000.0,
            "unrealizedPnL": 250.0,
        }
    }


def _instruments_response(*instruments: dict) -> dict:
    return {"instrumentDisplayDatas": list(instruments)}


def _candles_response(instrument_id: int, count: int = 3) -> dict:
    candles = []
    for i in range(count):
        candles.append(
            {
                "instrumentID": instrument_id,
                "fromDate": datetime(
                    2024, 1, 10 + i, tzinfo=timezone.utc
                ).isoformat(),
                "open": 150.0 + i,
                "high": 155.0 + i,
                "low": 149.0 + i,
                "close": 153.0 + i,
                "volume": 1_000_000.0,
            }
        )
    return {
        "interval": "OneDay",
        "candles": [
            {
                "instrumentId": instrument_id,
                "candles": candles,
                "rangeOpen": candles[0]["open"],
                "rangeClose": candles[-1]["close"],
                "rangeHigh": max(c["high"] for c in candles),
                "rangeLow": min(c["low"] for c in candles),
                "volume": sum(c["volume"] for c in candles),
            }
        ],
    }
