"""Market data functions for the eToro API."""

from __future__ import annotations

from typing import Literal

import structlog
from pydantic import ValidationError

from agent.etoro.client import EToroClient
from agent.etoro.models import (
    Candle,
    CandleResponse,
    Instrument,
    InstrumentSearchResponse,
    Rate,
    RatesResponse,
)

logger = structlog.get_logger(__name__)


# Valid candle intervals as defined by the eToro API
CandleInterval = Literal[
    "OneMinute",
    "FiveMinutes",
    "TenMinutes",
    "FifteenMinutes",
    "ThirtyMinutes",
    "OneHour",
    "FourHours",
    "OneDay",
    "OneWeek",
]

CandleDirection = Literal["asc", "desc"]


class InstrumentNotFoundError(Exception):
    """Raised when an instrument cannot be found by symbol."""


class InvalidCandleCountError(ValueError):
    """Raised when the candle count parameter is out of valid range."""


def search_instruments(
    client: EToroClient,
    query: str,
    *,
    page_size: int = 20,
    page_number: int = 1,
) -> list[Instrument]:
    """
    Search for instruments by name or symbol.
    
    Note: The eToro API does not support server-side search for this endpoint,
    so we fetch all instruments and filter client-side. Pagination is simulated.

    Args:
        client: The eToro API client.
        query: Search text to match against instrument names.
        page_size: Number of results per page (default 20).
        page_number: Page number for pagination (default 1).

    Returns:
        A list of matching Instrument objects.

    Raises:
        ValueError: If page_size or page_number is less than 1.
    """
    # Validate pagination parameters
    if page_size < 1:
        raise ValueError(f"page_size must be >= 1, got {page_size}")
    if page_number < 1:
        raise ValueError(f"page_number must be >= 1, got {page_number}")

    # Fetch all instruments
    response = client.get("/market-data/instruments")
    parsed = InstrumentSearchResponse.model_validate(response.json())

    query_lower = query.lower()
    matches: list[Instrument] = []

    # Client-side filtering
    for item in parsed.items:
        try:
            # Check symbol and display name
            symbol = item.get("symbolFull", "").lower()
            name = item.get("instrumentDisplayName", "").lower()

            if query_lower in symbol or query_lower in name:
                matches.append(Instrument.model_validate(item))
        except ValidationError as exc:
            logger.warning(
                "instrument_validation_failed",
                instrument_id=item.get("instrumentID"),
                symbol=item.get("symbolFull"),
                name=item.get("instrumentDisplayName"),
                error=str(exc),
            )
            continue

    # Simulate pagination
    start_idx = (page_number - 1) * page_size
    end_idx = start_idx + page_size
    return matches[start_idx:end_idx]


def get_instrument_by_symbol(client: EToroClient, symbol: str) -> Instrument:
    """
    Resolve a ticker symbol to an instrument.

    Args:
        client: The eToro API client.
        symbol: The exact ticker symbol (e.g., 'AAPL', 'BTC').

    Returns:
        The matching Instrument object.

    Raises:
        InstrumentNotFoundError: If no instrument matches the symbol exactly.
    """
    response = client.get("/market-data/instruments")
    parsed = InstrumentSearchResponse.model_validate(response.json())

    # Find exact match (case-insensitive)
    symbol_upper = symbol.upper()
    for item in parsed.items:
        try:
            # We peek at the raw dict first to avoid validating everything
            if item.get("symbolFull", "").upper() == symbol_upper:
                return Instrument.model_validate(item)
        except ValidationError as exc:
            logger.warning(
                "instrument_validation_failed",
                instrument_id=item.get("instrumentID"),
                symbol=item.get("symbolFull"),
                name=item.get("instrumentDisplayName"),
                error=str(exc),
            )
            continue

    raise InstrumentNotFoundError(f"No instrument found with symbol '{symbol}'")


def get_candles(
    client: EToroClient,
    instrument_id: int,
    interval: CandleInterval = "OneDay",
    count: int = 100,
    direction: CandleDirection = "desc",
) -> list[Candle]:
    """
    Fetch historical OHLCV candle data for an instrument.

    Args:
        client: The eToro API client.
        instrument_id: The eToro instrument ID.
        interval: Candle interval (default 'OneDay').
        count: Number of candles to fetch, must be between 1 and 1000 (default 100).
        direction: Sort direction, 'asc' or 'desc' (default 'desc').

    Returns:
        A list of Candle objects with OHLCV data.

    Raises:
        InvalidCandleCountError: If count is not between 1 and 1000.
    """
    # Validate count is within valid range
    if count < 1 or count > 1000:
        raise InvalidCandleCountError(
            f"count must be between 1 and 1000, got {count}"
        )

    response = client.get(
        f"/market-data/instruments/{instrument_id}/history/candles/{direction}/{interval}/{count}"
    )
    parsed = CandleResponse.model_validate(response.json())

    # Flatten the nested candle structure
    candles: list[Candle] = []
    for instrument_candles in parsed.candles:
        candles.extend(instrument_candles.candles)

    return candles


def get_prices(client: EToroClient, instrument_ids: list[int]) -> list[Rate]:
    """
    Get current bid/ask prices for specified instruments.

    Args:
        client: The eToro API client.
        instrument_ids: List of instrument IDs to get prices for.

    Returns:
        A list of Rate objects with current bid/ask prices.
    """
    if not instrument_ids:
        return []

    ids_param = ",".join(str(id_) for id_ in instrument_ids)
    response = client.get(
        "/market-data/instruments/rates",
        params={"instrumentIds": ids_param},
    )
    parsed = RatesResponse.model_validate(response.json())
    return parsed.rates
