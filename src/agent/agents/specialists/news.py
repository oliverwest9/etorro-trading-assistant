"""News specialist agent.

Fetches world news headlines from one or more free RSS feeds and
provides news context for the commentary agent.  Runs after analysis
and before commentary so that LLM-generated reports can reference
current events.

Multiple feed URLs can be specified via the ``NEWS_API_URL``
environment variable as a comma-separated list.  Each headline is
enriched with its publish date and RSS category tags so the LLM
receives richer temporal and topical context.
"""

from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from typing import Any

import httpx
import structlog
from langchain_core.tools import tool as langchain_tool

from agent.agents.base import AgentContext, BaseSpecialist

logger = structlog.get_logger(__name__)

_DEFAULT_MAX_ITEMS = 10

# Regex to strip HTML tags from RSS descriptions
_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    """Remove HTML tags and decode entities from *text*."""
    cleaned = _HTML_TAG_RE.sub("", text)
    return html.unescape(cleaned).strip()


def _parse_feed_urls(raw: str) -> list[str]:
    """Split a comma-separated string of feed URLs into a list.

    Whitespace around each URL is stripped and empty entries are
    discarded.
    """
    return [u.strip() for u in raw.split(",") if u.strip()]


def fetch_news_headlines(
    feed_url: str,
    *,
    max_items: int = _DEFAULT_MAX_ITEMS,
) -> list[dict[str, Any]]:
    """Fetch news headlines from an RSS feed.

    Makes a GET request to ``feed_url`` and parses the XML response
    as an RSS 2.0 feed.  Each ``<item>`` element is expected to
    contain ``<title>`` and ``<description>`` children.

    Returns:
        A list of dicts with keys ``title``, ``description``,
        ``source``, ``published`` (ISO-8601 string or ``""``), and
        ``categories`` (list of strings).  Returns an empty list on
        any failure.
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

    feed_title = (channel.findtext("title") or "").strip() or "Unknown"

    headlines: list[dict[str, Any]] = []
    for item in channel.findall("item"):
        if len(headlines) >= max_items:
            break
        title = (item.findtext("title") or "").strip()
        if not title:
            continue

        raw_desc = (item.findtext("description") or "").strip()
        description = _strip_html(raw_desc)

        # Parse pubDate (RFC-2822) into ISO-8601
        published = ""
        pub_date_text = (item.findtext("pubDate") or "").strip()
        if pub_date_text:
            try:
                published = parsedate_to_datetime(pub_date_text).isoformat()
            except Exception:
                pass

        # Collect <category> tags
        categories = [
            (cat.text or "").strip()
            for cat in item.findall("category")
            if (cat.text or "").strip()
        ]

        headlines.append({
            "title": title,
            "description": description,
            "source": feed_title,
            "published": published,
            "categories": categories,
        })
    return headlines


def fetch_all_news_headlines(
    feed_urls: list[str],
    *,
    max_items: int = _DEFAULT_MAX_ITEMS,
) -> list[dict[str, Any]]:
    """Fetch and merge headlines from multiple RSS feeds.

    Headlines are deduplicated by normalised title across all feeds.
    The combined result is capped at *max_items* and sorted newest-first
    when publish dates are available.
    """
    all_headlines: list[dict[str, Any]] = []
    seen_titles: set[str] = set()

    for url in feed_urls:
        for headline in fetch_news_headlines(url, max_items=max_items):
            normalised = headline["title"].lower().strip()
            if normalised not in seen_titles:
                seen_titles.add(normalised)
                all_headlines.append(headline)

    # Sort by published date descending (entries without dates last)
    all_headlines.sort(key=lambda h: h.get("published") or "", reverse=True)

    return all_headlines[:max_items]


class NewsSpecialist(BaseSpecialist):
    """Fetches world news headlines and provides market-relevant news context."""

    @property
    def name(self) -> str:
        return "news"

    @property
    def description(self) -> str:
        return (
            "Fetches world and business news headlines from one or more "
            "free RSS feeds to provide current-events context for the "
            "commentary agent. Call after analysis and before commentary."
        )

    def get_system_prompt(self) -> str:
        return (
            "You are the news specialist for a trading portfolio agent. "
            "Your job is to:\n"
            "1. Fetch the latest business news headlines using fetch_news\n"
            "2. The news context will be forwarded to the commentary agent "
            "to enrich its market analysis with current events.\n\n"
            "The news feeds are free RSS endpoints — no API key is needed.\n"
            "Headlines include publish dates and category tags to help the "
            "commentary agent assess recency and relevance."
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

            feed_urls = _parse_feed_urls(ctx.settings.news_api_url)
            if not feed_urls:
                return "SKIP: No valid feed URLs found. News context will be empty."

            headlines = fetch_all_news_headlines(feed_urls)
            self._headlines = headlines

            if not headlines:
                return "No headlines fetched (feeds may be unavailable)."

            sources = {h["source"] for h in headlines}
            return (
                f"Fetched {len(headlines)} news headlines "
                f"from {len(sources)} source(s)."
            )

        return [fetch_news]

    def run_procedural(
        self,
        state: dict[str, Any],
        ctx: AgentContext,
    ) -> None:
        """Fetch news headlines (procedural)."""
        if not ctx.settings.news_api_url:
            self._headlines: list[dict[str, Any]] = []
            return

        feed_urls = _parse_feed_urls(ctx.settings.news_api_url)
        if not feed_urls:
            self._headlines = []
            return

        headlines = fetch_all_news_headlines(feed_urls)
        self._headlines = headlines

        if headlines:
            sources = {h["source"] for h in headlines}
            logger.info(
                "news_fetched",
                headline_count=len(headlines),
                source_count=len(sources),
            )
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
