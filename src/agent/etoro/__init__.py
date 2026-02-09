"""eToro API client, market data, and portfolio access."""

from agent.etoro.client import EToroClient, EToroAuthError, EToroError, EToroRequestError
from agent.etoro.market_data import (
    InstrumentNotFoundError,
    InvalidCandleCountError,
    get_candles,
    get_instrument_by_symbol,
    get_prices,
    search_instruments,
)
from agent.etoro.models import (
    Candle,
    CandleResponse,
    ClientPortfolio,
    Instrument,
    InstrumentCandles,
    InstrumentSearchResponse,
    Mirror,
    PendingOrder,
    PortfolioResponse,
    Position,
    PositionWithPnl,
    Rate,
    RatesResponse,
    TradingHistoryItem,
    UnrealizedPnL,
)
from agent.etoro.portfolio import get_portfolio, get_trading_history

__all__ = [
    # Client
    "EToroClient",
    "EToroAuthError",
    "EToroError",
    "EToroRequestError",
    # Market data functions
    "InstrumentNotFoundError",
    "InvalidCandleCountError",
    "get_candles",
    "get_instrument_by_symbol",
    "get_prices",
    "search_instruments",
    # Portfolio functions
    "get_portfolio",
    "get_trading_history",
    # Models
    "Candle",
    "CandleResponse",
    "ClientPortfolio",
    "Instrument",
    "InstrumentCandles",
    "InstrumentSearchResponse",
    "Mirror",
    "PendingOrder",
    "PortfolioResponse",
    "Position",
    "PositionWithPnl",
    "Rate",
    "RatesResponse",
    "TradingHistoryItem",
    "UnrealizedPnL",
]
