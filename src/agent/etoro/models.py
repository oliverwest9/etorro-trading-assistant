"""Pydantic models for eToro API responses."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


# =============================================================================
# Instrument Models
# =============================================================================


class Instrument(BaseModel):
    """An instrument (tradable asset) from eToro."""

    instrument_id: int = Field(alias="instrumentID")
    symbol: str = Field(alias="symbolFull")
    name: str = Field(alias="instrumentDisplayName")
    instrument_type_id: int = Field(alias="instrumentTypeID")
    exchange_id: Optional[int] = Field(default=None, alias="exchangeID")

    # These fields are not in the list response, so we make them optional/computed
    is_open: Optional[bool] = Field(default=None)
    is_tradable: Optional[bool] = Field(default=None)

    @property
    def asset_class(self) -> str:
        """Derive asset class from instrument type ID."""
        # Mapping based on observation:
        # 1: Currencies?
        # 2: Indices/Commodities?
        # 4: Futures?
        # 5: Stocks
        # 6: ETFs
        # 10: Cryptocurrencies
        type_id = self.instrument_type_id
        if type_id == 5:
            return "Stocks"
        if type_id == 6:
            return "ETF"
        if type_id == 10:
            return "Crypto"
        if type_id == 1:
            return "Forex"
        if type_id == 4:
            return "Commodities"
        return "Other"

    @property
    def instrument_type(self) -> str:
        """Return the instrument type (aliased to asset_class for now)."""
        return self.asset_class


class InstrumentSearchResponse(BaseModel):
    """Response from the instrument list endpoint."""
    
    items: list[dict] = Field(alias="instrumentDisplayDatas")


# =============================================================================
# Candle (OHLCV) Models
# =============================================================================


class Candle(BaseModel):
    """A single OHLCV candle."""

    instrument_id: int = Field(alias="instrumentID")
    timestamp: datetime = Field(alias="fromDate")
    open: float
    high: float
    low: float
    close: float
    volume: Optional[float] = 0.0


class InstrumentCandles(BaseModel):
    """Candles for a single instrument, as returned in the API response."""

    instrument_id: int = Field(alias="instrumentId")
    candles: list[Candle]
    range_open: float = Field(alias="rangeOpen")
    range_close: float = Field(alias="rangeClose")
    range_high: float = Field(alias="rangeHigh")
    range_low: float = Field(alias="rangeLow")
    volume: float


class CandleResponse(BaseModel):
    """Response from the candles history endpoint."""

    interval: str
    candles: list[InstrumentCandles]


# =============================================================================
# Rate (Bid/Ask) Models
# =============================================================================


class Rate(BaseModel):
    """Current market rate for an instrument."""

    instrument_id: int = Field(alias="instrumentID")
    bid: float
    ask: float
    last_execution: float = Field(alias="lastExecution")
    date: datetime
    conversion_rate_ask: Optional[float] = Field(default=None, alias="conversionRateAsk")
    conversion_rate_bid: Optional[float] = Field(default=None, alias="conversionRateBid")


class RatesResponse(BaseModel):
    """Response from the rates endpoint."""

    rates: list[Rate]
