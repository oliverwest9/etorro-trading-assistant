"""News specialist agent.

Fetches world news headlines from a configurable news API and provides
news context for the commentary agent.  Runs after analysis and before
commentary so that LLM-generated reports can reference current events.
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog
from langchain_core.tools import tool as langchain_tool

from agent.agents.base import AgentContext, BaseSpecialist

logger = structlog.get_logger(__name__)

# Categories relevant to portfolio analysis
_DEFAULT_CATEGORY = "business"
_DEFAULT_PAGE_SIZE = 10


def fetch_news_headlines(
    api_url: str,
    api_key: str,
    *,
    category: str = _DEFAULT_CATEGORY,
    page_size: int = _DEFAULT_PAGE_SIZE,
) -> list[dict[str, str]]:
    """Fetch news headlines from the configured news API.

    Makes a GET request to ``api_url`` with ``apiKey``, ``category``,
    and ``pageSize`` query parameters.  Expects a JSON response with
    an ``articles`` list, each containing ``title``, ``description``,
    and ``source.name``.

    Args:
        api_url: Base URL of the news API endpoint.
        api_key: API key for authentication.
        category: News category (default ``"business"``).
        page_size: Maximum number of headlines to fetch (default 10).

    Returns:
        A list of dicts with keys ``title``, ``description``, and
        ``source``.  Returns an empty list on any failure.
    """
    try:
        resp = httpx.get(
            api_url,
            params={
                "apiKey": api_key,
                "category": category,
                "pageSize": page_size,
            },
            timeout=15.0,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning("news_fetch_failed", error=str(exc))
        return []

    articles = data.get("articles", [])
    headlines: list[dict[str, str]] = []
    for article in articles:
        source = article.get("source") or {}
        headlines.append({
            "title": article.get("title", ""),
            "description": article.get("description") or "",
            "source": source.get("name", "") if isinstance(source, dict) else "",
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
            "Fetches world and business news headlines from a news API "
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
            "If the news API key is not configured, you can skip this step."
        )

    def create_tools(self, ctx: AgentContext) -> list[Any]:

        @langchain_tool
        def fetch_news() -> str:
            """Fetch the latest business and world news headlines.

            Returns a summary of fetched headlines or a skip message
            if no API key is configured.
            """
            if not ctx.settings.news_api_key:
                return "SKIP: No news API key configured. News context will be empty."

            headlines = fetch_news_headlines(
                ctx.settings.news_api_url,
                ctx.settings.news_api_key,
            )
            self._headlines = headlines

            if not headlines:
                return "No headlines fetched (API may be unavailable)."

            return f"Fetched {len(headlines)} news headlines."

        return [fetch_news]

    def run_procedural(
        self,
        state: dict[str, Any],
        ctx: AgentContext,
    ) -> None:
        """Fetch news headlines (procedural)."""
        if not ctx.settings.news_api_key:
            self._headlines: list[dict[str, str]] = []
            return

        headlines = fetch_news_headlines(
            ctx.settings.news_api_url,
            ctx.settings.news_api_key,
        )
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
