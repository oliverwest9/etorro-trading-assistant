"""Tests for the eToro portfolio module."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from agent.config import Settings
from agent.etoro.client import EToroClient, EToroRequestError
from agent.etoro.portfolio import get_portfolio, get_trading_history


def _settings() -> Settings:
    return Settings(
        etoro_api_key="test-api-key",
        etoro_user_key="test-user-key",
        etoro_base_url="https://example.com",
        surreal_url="ws://localhost:8000/rpc",
        surreal_namespace="trading",
        surreal_database="agent",
        surreal_user="root",
        surreal_pass="root",
        llm_provider="openai",
        llm_api_key="test-llm",
        llm_model="gpt-4o",
    )


# Sample API payloads -----------------------------------------------------------

SAMPLE_PNL_RESPONSE = {
    "clientPortfolio": {
        "credit": 280.35,
        "unrealizedPnL": 150.75,
        "positions": [
            {
                "unrealizedPnL": {
                    "pnL": 25.50,
                    "pnlAssetCurrency": 25.50,
                    "exposureInAccountCurrency": 125.50,
                    "exposureInAssetCurrency": 125.50,
                    "marginInAccountCurrency": 100.0,
                    "marginInAssetCurrency": 100.0,
                    "marginCurrencyId": 1,
                    "assetCurrencyId": 1,
                    "closeRate": 2550.00,
                    "closeConversionRate": 1.1,
                    "timestamp": "2024-08-15T12:00:00Z",
                },
                "positionID": 2150896073,
                "CID": 7765437,
                "openDateTime": "2024-08-01T07:44:26.103Z",
                "openRate": 2020.7784,
                "instrumentID": 1002,
                "isBuy": True,
                "takeProfitRate": 0,
                "stopLossRate": 0.0001,
                "amount": 100,
                "leverage": 1,
                "orderID": 12402059,
                "orderType": 17,
                "units": 0.049485,
                "totalFees": 0,
                "initialAmountInDollars": 100,
                "isTslEnabled": False,
                "stopLossVersion": 3,
                "isSettled": True,
                "redeemStatusID": 0,
                "initialUnits": 0.049485,
                "isPartiallyAltered": False,
                "unitsBaseValueDollars": 100,
                "isDiscounted": True,
                "openPositionActionType": 0,
                "settlementTypeID": 1,
                "isDetached": False,
                "openConversionRate": 1,
                "pnlVersion": 1,
                "totalExternalFees": 0,
                "totalExternalTaxes": 0,
                "isNoTakeProfit": True,
                "isNoStopLoss": True,
                "lotCount": 0.049485,
                "mirrorID": 0,
                "parentPositionID": 0,
            }
        ],
        "mirrors": [
            {
                "mirrorId": 1841334,
                "cid": 7765437,
                "parentCid": 14370798,
                "stopLossPercentage": 5,
                "isPaused": False,
                "copyExistingPositions": True,
                "availableAmount": 560,
                "stopLossAmount": 28,
                "initialInvestment": 560,
                "depositSummary": 0,
                "withdrawalSummary": 0,
                "parentUsername": "Deposit158990700",
                "closedPositionsNetProfit": 0,
                "startedCopyDate": "2024-05-23T13:31:57.007Z",
                "pendingForClosure": False,
                "mirrorStatusId": 0,
            }
        ],
        "orders": [
            {
                "orderId": 5669649,
                "cid": 7765437,
                "openDateTime": "2024-06-06T08:07:25.083Z",
                "instrumentId": 100043,
                "isBuy": True,
                "takeProfitRate": 0,
                "stopLossRate": 0.00001,
                "rate": 0.1453,
                "amount": 100,
                "leverage": 1,
                "units": 688.231246,
                "isTslEnabled": False,
                "executionType": 0,
                "isDiscounted": False,
            }
        ],
        "stockOrders": [],
        "entryOrders": [],
        "exitOrders": [],
        "ordersForOpen": [],
        "ordersForClose": [],
        "ordersForCloseMultiple": [],
        "bonusCredit": 0,
        "accountCurrencyId": 1,
    }
}


SAMPLE_TRADING_HISTORY = [
    {
        "netProfit": 42.50,
        "closeRate": 155.30,
        "closeTimestamp": "2024-07-15T14:30:00Z",
        "positionId": 2150000001,
        "instrumentId": 1001,
        "isBuy": True,
        "leverage": 1,
        "openRate": 150.00,
        "openTimestamp": "2024-06-01T09:00:00Z",
        "stopLossRate": 145.00,
        "takeProfitRate": 160.00,
        "trailingStopLoss": False,
        "orderId": 12000001,
        "socialTradeId": 0,
        "parentPositionId": 0,
        "investment": 1000.00,
        "initialInvestment": 1000.00,
        "fees": 2.50,
        "units": 6.67,
    },
    {
        "netProfit": -15.00,
        "closeRate": 28500.00,
        "closeTimestamp": "2024-07-20T16:00:00Z",
        "positionId": 2150000002,
        "instrumentId": 100001,
        "isBuy": True,
        "leverage": 1,
        "openRate": 29000.00,
        "openTimestamp": "2024-07-10T10:00:00Z",
        "stopLossRate": 28000.00,
        "takeProfitRate": 32000.00,
        "trailingStopLoss": False,
        "orderId": 12000002,
        "socialTradeId": 0,
        "parentPositionId": 0,
        "investment": 500.00,
        "initialInvestment": 500.00,
        "fees": 0.00,
        "units": 0.01724,
    },
]


# =============================================================================
# get_portfolio tests
# =============================================================================


def test_get_portfolio_returns_positions(httpx_mock):
    """Verify get_portfolio parses positions with P&L data correctly."""
    httpx_mock.add_response(
        url="https://example.com/trading/info/real/pnl",
        json=SAMPLE_PNL_RESPONSE,
    )

    with EToroClient(_settings()) as client:
        result = get_portfolio(client)

    portfolio = result.client_portfolio
    assert len(portfolio.positions) == 1
    assert portfolio.credit == 280.35
    assert portfolio.unrealized_pnl == 150.75
    assert portfolio.bonus_credit == 0

    pos = portfolio.positions[0]
    assert pos.position_id == 2150896073
    assert pos.instrument_id == 1002
    assert pos.open_rate == 2020.7784
    assert pos.is_buy is True
    assert pos.amount == 100
    assert pos.leverage == 1
    assert pos.units == 0.049485
    assert pos.initial_amount_in_dollars == 100
    assert pos.settlement_type_id == 1
    assert pos.is_no_take_profit is True
    assert pos.is_no_stop_loss is True

    # PnL-specific fields
    assert pos.pnl == 25.50
    assert pos.close_rate == 2550.00
    assert pos.close_conversion_rate == 1.1


def test_get_portfolio_empty_positions(httpx_mock):
    """Empty portfolio returns valid structure with no positions."""
    httpx_mock.add_response(
        url="https://example.com/trading/info/real/pnl",
        json={
            "clientPortfolio": {
                "credit": 5000.00,
                "unrealizedPnL": 0,
                "positions": [],
                "mirrors": [],
                "orders": [],
                "bonusCredit": 0,
            }
        },
    )

    with EToroClient(_settings()) as client:
        result = get_portfolio(client)

    assert result.client_portfolio.positions == []
    assert result.client_portfolio.credit == 5000.00
    assert result.client_portfolio.unrealized_pnl == 0


def test_get_portfolio_parses_mirrors(httpx_mock):
    """Verify mirrors (copy trading) are parsed correctly."""
    httpx_mock.add_response(
        url="https://example.com/trading/info/real/pnl",
        json=SAMPLE_PNL_RESPONSE,
    )

    with EToroClient(_settings()) as client:
        result = get_portfolio(client)

    mirrors = result.client_portfolio.mirrors
    assert len(mirrors) == 1
    assert mirrors[0]["mirrorId"] == 1841334
    assert mirrors[0]["parentUsername"] == "Deposit158990700"
    assert mirrors[0]["initialInvestment"] == 560
    assert mirrors[0]["isPaused"] is False


def test_get_portfolio_parses_pending_orders(httpx_mock):
    """Verify pending orders are parsed correctly."""
    httpx_mock.add_response(
        url="https://example.com/trading/info/real/pnl",
        json=SAMPLE_PNL_RESPONSE,
    )

    with EToroClient(_settings()) as client:
        result = get_portfolio(client)

    orders = result.client_portfolio.orders
    assert len(orders) == 1
    assert orders[0]["orderId"] == 5669649
    assert orders[0]["instrumentId"] == 100043
    assert orders[0]["rate"] == 0.1453


def test_get_portfolio_sets_correct_headers(httpx_mock):
    """Verify the 3 required auth headers are sent."""
    httpx_mock.add_response(
        url="https://example.com/trading/info/real/pnl",
        json={
            "clientPortfolio": {
                "credit": 0,
                "unrealizedPnL": 0,
                "positions": [],
                "mirrors": [],
                "orders": [],
                "bonusCredit": 0,
            }
        },
    )

    with EToroClient(_settings()) as client:
        get_portfolio(client)

    request = httpx_mock.get_requests()[0]
    assert request.headers["x-api-key"] == "test-api-key"
    assert request.headers["x-user-key"] == "test-user-key"
    assert "x-request-id" in request.headers


def test_get_portfolio_handles_api_error(httpx_mock):
    """500 response raises EToroRequestError after retries."""
    for _ in range(3):
        httpx_mock.add_response(
            url="https://example.com/trading/info/real/pnl",
            status_code=500,
        )

    with EToroClient(_settings(), backoff_base=0.001) as client:
        with pytest.raises(EToroRequestError):
            get_portfolio(client)


# =============================================================================
# get_trading_history tests
# =============================================================================


def test_get_trading_history_returns_trades(httpx_mock):
    """Verify get_trading_history parses closed trades correctly."""
    httpx_mock.add_response(
        url="https://example.com/trading/info/trade/history?minDate=2024-06-01",
        json=SAMPLE_TRADING_HISTORY,
    )

    with EToroClient(_settings()) as client:
        trades = get_trading_history(client, min_date="2024-06-01")

    assert len(trades) == 2

    t1 = trades[0]
    assert t1.net_profit == 42.50
    assert t1.close_rate == 155.30
    assert t1.position_id == 2150000001
    assert t1.instrument_id == 1001
    assert t1.is_buy is True
    assert t1.leverage == 1
    assert t1.open_rate == 150.00
    assert t1.investment == 1000.00
    assert t1.fees == 2.50
    assert t1.units == 6.67
    assert t1.trailing_stop_loss is False

    t2 = trades[1]
    assert t2.net_profit == -15.00
    assert t2.instrument_id == 100001


def test_get_trading_history_default_min_date(httpx_mock):
    """When min_date is omitted, it defaults to ~90 days ago."""
    httpx_mock.add_response(
        json=[],
    )

    with EToroClient(_settings()) as client:
        get_trading_history(client)

    request = httpx_mock.get_requests()[0]
    min_date_param = str(request.url.params["minDate"])

    # Parse the date and verify it's approximately 90 days ago
    param_date = datetime.strptime(min_date_param, "%Y-%m-%d")
    expected_date = datetime.now(tz=timezone.utc) - timedelta(days=90)
    # Allow 1 day tolerance for edge cases around midnight
    diff = abs((param_date.date() - expected_date.date()).days)
    assert diff <= 1, f"Expected ~90 days ago, got {min_date_param}"


def test_get_trading_history_passes_pagination(httpx_mock):
    """Verify page and pageSize are forwarded as query params."""
    httpx_mock.add_response(
        url="https://example.com/trading/info/trade/history?minDate=2024-01-01&page=2&pageSize=50",
        json=[],
    )

    with EToroClient(_settings()) as client:
        get_trading_history(client, min_date="2024-01-01", page=2, page_size=50)

    request = httpx_mock.get_requests()[0]
    assert request.url.params["minDate"] == "2024-01-01"
    assert request.url.params["page"] == "2"
    assert request.url.params["pageSize"] == "50"


def test_get_trading_history_empty(httpx_mock):
    """Empty history returns an empty list."""
    httpx_mock.add_response(
        url="https://example.com/trading/info/trade/history?minDate=2024-01-01",
        json=[],
    )

    with EToroClient(_settings()) as client:
        trades = get_trading_history(client, min_date="2024-01-01")

    assert trades == []


def test_get_trading_history_handles_api_error(httpx_mock):
    """500 response raises EToroRequestError after retries."""
    for _ in range(3):
        httpx_mock.add_response(
            url="https://example.com/trading/info/trade/history?minDate=2024-01-01",
            status_code=500,
        )

    with EToroClient(_settings(), backoff_base=0.001) as client:
        with pytest.raises(EToroRequestError):
            get_trading_history(client, min_date="2024-01-01")
