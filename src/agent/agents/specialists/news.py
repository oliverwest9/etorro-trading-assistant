"""News specialist agent.

Fetches world news headlines from a free RSS feed and provides news
context for the commentary agent.  Runs after analysis and before
commentary so that LLM-generated reports can reference current events.

The default endpoint is the BBC Business RSS feed which requires no
API key.  The feed URL can be changed via the ``NEWS_API_URL``
environment variable.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any

import httpx
import structlog
from langchain_core.tools import tool as langchain_tool

from agent.agents.base import AgentContext, BaseSpecialist

logger = structlog.get_logger(__name__)

_DEFAULT_MAX_ITEMS = 10


def fetch_news_headlines(
    feed_url: str,
    *,
    max_items: int = _DEFAULT_MAX_ITEMS,
) -> list[dict[str, str]]:
    """Fetch news headlines from an RSS feed.

    Makes a GET request to ``feed_url`` and parses the XML response
    as an RSS 2.0 feed.  Each ``<item>`` element is expected to
    contain ``<title>`` and ``<description>`` children.

    Args:
        feed_url: URL of the RSS feed.
        max_items: Maximum number of headlines to return (default 10).

    Returns:
        A list of dicts with keys ``title``, ``description``, and
        ``source``.  Returns an empty list on any failure.
    """
    try:
        resp = httpx.get(feed_url, timeout=15.0)
        resp.raise_for_status()
    except Exception as exc:
        logger.warning("news_fetch_failed", error=str(exc))
        return []

    try:
        root = ET.fromstring(resp.text)
    except ET.ParseError as exc:
        logger.warning("news_rss_parse_failed", error=str(exc))
        return []

    # RSS 2.0: <rss><channel><title>…</title><item>…</item></channel></rss>
    channel = root.find("channel")
    if channel is None:
        logger.warning("news_rss_no_channel")
        return []

    feed_title = (channel.findtext("title") or "").strip()

    headlines: list[dict[str, str]] = []
    for item in channel.findall("item"):
        if len(headlines) >= max_items:
            break
        title = (item.findtext("title") or "").strip()
        description = (item.findtext("description") or "").strip()
        headlines.append({
            "title": title,
            "description": description,
            "source": feed_title,
        })
    return headlines


class NewsSpecialist(BaseSpecialist):
    """Fetches world news headlines and provides market-relevant news context."""

    @property
    def name(self) -> str:
        return "news"

    @property
    def description(self) -> str:
        return (
            "Fetches world and business news headlines from a free RSS feed "
            "to provide current-events context for the commentary agent. "
            "Call after analysis and before commentary."
        )

    def get_system_prompt(self) -> str:
        return (
            "You are the news specialist for a trading portfolio agent. "
            "Your job is to:\n"
            "1. Fetch the latest business news headlines using fetch_news\n"
            "2. The news context will be forwarded to the commentary agent "
            "to enrich its market analysis with current events.\n\n"
            "The news feed is a free RSS endpoint — no API key is needed."
        )

    def create_tools(self, ctx: AgentContext) -> list[Any]:

        @langchain_tool
        def fetch_news() -> str:
            """Fetch the latest business and world news headlines.

            Returns a summary of fetched headlines or a message
            if the feed is unavailable.
            """
            if not ctx.settings.news_api_url:
                return "SKIP: No news feed URL configured. News context will be empty."

            headlines = fetch_news_headlines(ctx.settings.news_api_url)
            self._headlines = headlines

            if not headlines:
                return "No headlines fetched (feed may be unavailable)."

            return f"Fetched {len(headlines)} news headlines."

        return [fetch_news]

    def run_procedural(
        self,
        state: dict[str, Any],
        ctx: AgentContext,
    ) -> None:
        """Fetch news headlines (procedural)."""
        if not ctx.settings.news_api_url:
            self._headlines: list[dict[str, str]] = []
            return

        headlines = fetch_news_headlines(ctx.settings.news_api_url)
        self._headlines = headlines

        if headlines:
            logger.info("news_fetched", headline_count=len(headlines))
        else:
            logger.info("news_none_fetched")

    def process_results(
        self,
        state: dict[str, Any],
        ctx: AgentContext,
    ) -> dict[str, Any]:
        """Store fetched headlines in the pipeline state."""
        headlines = getattr(self, "_headlines", [])
        self._headlines = []
        return {"news_context": headlines if headlines else None}
