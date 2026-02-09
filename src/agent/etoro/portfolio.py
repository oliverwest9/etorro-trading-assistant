"""Portfolio and trading history functions for the eToro API."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import structlog

from agent.etoro.client import EToroClient
from agent.etoro.models import (
    PortfolioResponse,
    TradingHistoryItem,
)

logger = structlog.get_logger(__name__)

# Default lookback period for trading history when no min_date is provided
_DEFAULT_HISTORY_DAYS = 90


def get_portfolio(client: EToroClient) -> PortfolioResponse:
    """
    Fetch the current portfolio with P&L data.

    Uses the real-account PnL endpoint which returns positions enriched with
    per-position P&L values and portfolio-level unrealised P&L.

    Args:
        client: The eToro API client.

    Returns:
        A PortfolioResponse containing positions, credit, mirrors, and orders.
    """
    logger.info("fetching_portfolio")
    response = client.get("/trading/info/real/pnl")
    portfolio = PortfolioResponse.model_validate(response.json())
    logger.info(
        "portfolio_fetched",
        positions=len(portfolio.client_portfolio.positions),
        credit=portfolio.client_portfolio.credit,
        unrealized_pnl=portfolio.client_portfolio.unrealized_pnl,
    )
    return portfolio


def get_trading_history(
    client: EToroClient,
    *,
    min_date: str | None = None,
    page: int | None = None,
    page_size: int | None = None,
) -> list[TradingHistoryItem]:
    """
    Fetch closed-trade history from the eToro API.

    Args:
        client: The eToro API client.
        min_date: Start date in 'YYYY-MM-DD' format. Defaults to 90 days ago.
        page: Page number for pagination (optional).
        page_size: Number of trades per page (optional).

    Returns:
        A list of TradingHistoryItem objects representing closed trades.
    """
    if min_date is None:
        default_date = datetime.now(tz=timezone.utc) - timedelta(
            days=_DEFAULT_HISTORY_DAYS
        )
        min_date = default_date.strftime("%Y-%m-%d")

    params: dict[str, str | int] = {"minDate": min_date}
    if page is not None:
        params["page"] = page
    if page_size is not None:
        params["pageSize"] = page_size

    logger.info("fetching_trading_history", min_date=min_date, page=page)
    response = client.get("/trading/info/trade/history", params=params)

    raw = response.json()
    trades = [TradingHistoryItem.model_validate(item) for item in raw]
    logger.info("trading_history_fetched", trades=len(trades))
    return trades
