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


# =============================================================================
# Portfolio / Position Models
# =============================================================================


class UnrealizedPnL(BaseModel):
    """Per-position P&L data nested inside the PnL endpoint response."""

    pnl: float = Field(alias="pnL")
    pnl_asset_currency: Optional[float] = Field(default=None, alias="pnlAssetCurrency")
    exposure_in_account_currency: Optional[float] = Field(
        default=None, alias="exposureInAccountCurrency"
    )
    exposure_in_asset_currency: Optional[float] = Field(
        default=None, alias="exposureInAssetCurrency"
    )
    margin_in_account_currency: Optional[float] = Field(
        default=None, alias="marginInAccountCurrency"
    )
    margin_in_asset_currency: Optional[float] = Field(
        default=None, alias="marginInAssetCurrency"
    )
    margin_currency_id: Optional[int] = Field(default=None, alias="marginCurrencyId")
    asset_currency_id: Optional[int] = Field(default=None, alias="assetCurrencyId")
    close_rate: Optional[float] = Field(default=None, alias="closeRate")
    close_conversion_rate: Optional[float] = Field(
        default=None, alias="closeConversionRate"
    )
    timestamp: Optional[datetime] = Field(default=None)


class Position(BaseModel):
    """An open trading position from the eToro portfolio."""

    position_id: int = Field(alias="positionID")
    cid: int = Field(alias="CID")
    open_date_time: datetime = Field(alias="openDateTime")
    open_rate: float = Field(alias="openRate")
    instrument_id: int = Field(alias="instrumentID")
    is_buy: bool = Field(alias="isBuy")
    take_profit_rate: float = Field(alias="takeProfitRate")
    stop_loss_rate: float = Field(alias="stopLossRate")
    amount: float
    leverage: int
    order_id: int = Field(alias="orderID")
    order_type: int = Field(alias="orderType")
    units: float
    total_fees: float = Field(alias="totalFees")
    initial_amount_in_dollars: float = Field(alias="initialAmountInDollars")
    is_tsl_enabled: bool = Field(alias="isTslEnabled")
    stop_loss_version: Optional[int] = Field(default=None, alias="stopLossVersion")
    is_settled: Optional[bool] = Field(default=None, alias="isSettled")
    redeem_status_id: Optional[int] = Field(default=None, alias="redeemStatusID")
    initial_units: float = Field(alias="initialUnits")
    is_partially_altered: bool = Field(alias="isPartiallyAltered")
    units_base_value_dollars: float = Field(alias="unitsBaseValueDollars")
    is_discounted: Optional[bool] = Field(default=None, alias="isDiscounted")
    open_position_action_type: Optional[int] = Field(
        default=None, alias="openPositionActionType"
    )
    settlement_type_id: int = Field(alias="settlementTypeID")
    is_detached: Optional[bool] = Field(default=None, alias="isDetached")
    open_conversion_rate: float = Field(alias="openConversionRate")
    pnl_version: Optional[int] = Field(default=None, alias="pnlVersion")
    total_external_fees: float = Field(alias="totalExternalFees")
    total_external_taxes: float = Field(alias="totalExternalTaxes")
    is_no_take_profit: bool = Field(alias="isNoTakeProfit")
    is_no_stop_loss: bool = Field(alias="isNoStopLoss")
    lot_count: float = Field(alias="lotCount")
    mirror_id: Optional[int] = Field(default=None, alias="mirrorID")
    parent_position_id: Optional[int] = Field(default=None, alias="parentPositionID")


class PositionWithPnl(Position):
    """A position enriched with P&L data from the PnL endpoint."""

    unrealized_pnl: Optional[UnrealizedPnL] = Field(
        default=None, alias="unrealizedPnL"
    )

    @property
    def pnl(self) -> Optional[float]:
        """Convenience accessor for the nested P&L value."""
        return self.unrealized_pnl.pnl if self.unrealized_pnl else None

    @property
    def close_rate(self) -> Optional[float]:
        """Convenience accessor for the nested close rate."""
        return self.unrealized_pnl.close_rate if self.unrealized_pnl else None

    @property
    def close_conversion_rate(self) -> Optional[float]:
        """Convenience accessor for the nested close conversion rate."""
        return (
            self.unrealized_pnl.close_conversion_rate
            if self.unrealized_pnl
            else None
        )


class Mirror(BaseModel):
    """A copy-trading (mirror) configuration."""

    mirror_id: int = Field(alias="mirrorId")
    cid: int
    parent_cid: int = Field(alias="parentCid")
    stop_loss_percentage: float = Field(alias="stopLossPercentage")
    is_paused: bool = Field(alias="isPaused")
    copy_existing_positions: bool = Field(alias="copyExistingPositions")
    available_amount: float = Field(alias="availableAmount")
    stop_loss_amount: float = Field(alias="stopLossAmount")
    initial_investment: float = Field(alias="initialInvestment")
    deposit_summary: float = Field(alias="depositSummary")
    withdrawal_summary: float = Field(alias="withdrawalSummary")
    parent_username: Optional[str] = Field(default=None, alias="parentUsername")
    closed_positions_net_profit: float = Field(alias="closedPositionsNetProfit")
    started_copy_date: Optional[datetime] = Field(
        default=None, alias="startedCopyDate"
    )
    pending_for_closure: bool = Field(alias="pendingForClosure")
    mirror_status_id: int = Field(alias="mirrorStatusId")


class PendingOrder(BaseModel):
    """A pending limit/entry order."""

    order_id: int = Field(alias="orderId")
    cid: int
    open_date_time: datetime = Field(alias="openDateTime")
    instrument_id: int = Field(alias="instrumentId")
    is_buy: bool = Field(alias="isBuy")
    take_profit_rate: float = Field(alias="takeProfitRate")
    stop_loss_rate: float = Field(alias="stopLossRate")
    rate: float
    amount: float
    leverage: int
    units: float
    is_tsl_enabled: bool = Field(alias="isTslEnabled")
    execution_type: Optional[int] = Field(default=None, alias="executionType")
    is_discounted: Optional[bool] = Field(default=None, alias="isDiscounted")


class ClientPortfolio(BaseModel):
    """The inner portfolio wrapper containing positions, credit, and orders."""

    positions: list[PositionWithPnl] = Field(default_factory=list)
    credit: float
    unrealized_pnl: Optional[float] = Field(default=None, alias="unrealizedPnL")
    mirrors: list[dict] = Field(default_factory=list)
    orders: list[dict] = Field(default_factory=list)
    bonus_credit: float = Field(default=0.0, alias="bonusCredit")
    account_currency_id: Optional[int] = Field(default=None, alias="accountCurrencyId")
    stock_orders: list[dict] = Field(default_factory=list, alias="stockOrders")
    entry_orders: list[dict] = Field(default_factory=list, alias="entryOrders")
    exit_orders: list[dict] = Field(default_factory=list, alias="exitOrders")
    orders_for_open: list[dict] = Field(default_factory=list, alias="ordersForOpen")
    orders_for_close: list[dict] = Field(default_factory=list, alias="ordersForClose")
    orders_for_close_multiple: list[dict] = Field(
        default_factory=list, alias="ordersForCloseMultiple"
    )


class PortfolioResponse(BaseModel):
    """Top-level response from the portfolio/PnL endpoint."""

    client_portfolio: ClientPortfolio = Field(alias="clientPortfolio")


# =============================================================================
# Trading History Models
# =============================================================================


class TradingHistoryItem(BaseModel):
    """A single closed trade from the trading history endpoint."""

    net_profit: float = Field(alias="netProfit")
    close_rate: float = Field(alias="closeRate")
    close_timestamp: datetime = Field(alias="closeTimestamp")
    position_id: int = Field(alias="positionId")
    instrument_id: int = Field(alias="instrumentId")
    is_buy: bool = Field(alias="isBuy")
    leverage: int
    open_rate: float = Field(alias="openRate")
    open_timestamp: datetime = Field(alias="openTimestamp")
    stop_loss_rate: Optional[float] = Field(default=None, alias="stopLossRate")
    take_profit_rate: Optional[float] = Field(default=None, alias="takeProfitRate")
    trailing_stop_loss: bool = Field(alias="trailingStopLoss")
    order_id: int = Field(alias="orderId")
    social_trade_id: Optional[int] = Field(default=None, alias="socialTradeId")
    parent_position_id: Optional[int] = Field(
        default=None, alias="parentPositionId"
    )
    investment: float
    initial_investment: float = Field(alias="initialInvestment")
    fees: float
    units: float
