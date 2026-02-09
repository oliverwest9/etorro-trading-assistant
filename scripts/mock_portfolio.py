"""Script that creates a mock portfolio for local testing without API access."""

from datetime import datetime, timezone

from agent.etoro.models import (
    ClientPortfolio,
    PortfolioResponse,
    PositionWithPnl,
    TradingHistoryItem,
    UnrealizedPnL,
)


def create_mock_portfolio() -> PortfolioResponse:
    """Create a mock portfolio matching the real API structure."""

    positions = [
        PositionWithPnl(
            **{
                "unrealizedPnL": UnrealizedPnL(
                    **{
                        "pnL": 125.50,
                        "pnlAssetCurrency": 125.50,
                        "exposureInAccountCurrency": 1125.50,
                        "exposureInAssetCurrency": 1125.50,
                        "marginInAccountCurrency": 1000.0,
                        "marginInAssetCurrency": 1000.0,
                        "marginCurrencyId": 1,
                        "assetCurrencyId": 1,
                        "closeRate": 2550.00,
                        "closeConversionRate": 1.0,
                        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
                    }
                ),
                "positionID": 2150896073,
                "CID": 7765437,
                "openDateTime": "2024-08-01T07:44:26.103Z",
                "openRate": 2020.78,
                "instrumentID": 1002,
                "isBuy": True,
                "takeProfitRate": 0,
                "stopLossRate": 0.0001,
                "amount": 1000.0,
                "leverage": 1,
                "orderID": 12402059,
                "orderType": 17,
                "units": 0.049485,
                "totalFees": 0,
                "initialAmountInDollars": 1000,
                "isTslEnabled": False,
                "stopLossVersion": 3,
                "isSettled": True,
                "redeemStatusID": 0,
                "initialUnits": 0.049485,
                "isPartiallyAltered": False,
                "unitsBaseValueDollars": 1000,
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
        ),
        PositionWithPnl(
            **{
                "unrealizedPnL": UnrealizedPnL(
                    **{
                        "pnL": 33.25,
                        "closeRate": 160.00,
                        "closeConversionRate": 1.0,
                    }
                ),
                "positionID": 2150896074,
                "CID": 7765437,
                "openDateTime": "2024-09-15T10:00:00Z",
                "openRate": 150.00,
                "instrumentID": 1001,
                "isBuy": True,
                "takeProfitRate": 0,
                "stopLossRate": 140.00,
                "amount": 500.0,
                "leverage": 1,
                "orderID": 12402060,
                "orderType": 17,
                "units": 3.33,
                "totalFees": 0,
                "initialAmountInDollars": 500,
                "isTslEnabled": False,
                "initialUnits": 3.33,
                "isPartiallyAltered": False,
                "unitsBaseValueDollars": 500,
                "settlementTypeID": 1,
                "openConversionRate": 1,
                "totalExternalFees": 0,
                "totalExternalTaxes": 0,
                "isNoTakeProfit": True,
                "isNoStopLoss": False,
                "lotCount": 3.33,
                "mirrorID": 0,
                "parentPositionID": 0,
            }
        ),
    ]

    portfolio = PortfolioResponse(
        clientPortfolio=ClientPortfolio(
            positions=positions,
            credit=6500.0,
            unrealizedPnL=158.75,
            mirrors=[],
            orders=[],
            bonusCredit=0,
        )
    )

    return portfolio


def create_mock_trading_history() -> list[TradingHistoryItem]:
    """Create mock closed trades."""
    return [
        TradingHistoryItem(
            netProfit=42.50,
            closeRate=155.30,
            closeTimestamp="2024-07-15T14:30:00Z",
            positionId=2150000001,
            instrumentId=1001,
            isBuy=True,
            leverage=1,
            openRate=150.00,
            openTimestamp="2024-06-01T09:00:00Z",
            stopLossRate=145.00,
            takeProfitRate=160.00,
            trailingStopLoss=False,
            orderId=12000001,
            socialTradeId=0,
            parentPositionId=0,
            investment=1000.00,
            initialInvestment=1000.00,
            fees=2.50,
            units=6.67,
        ),
    ]


if __name__ == "__main__":
    portfolio = create_mock_portfolio()
    print("=== Mock Portfolio ===")
    print(portfolio.model_dump_json(indent=2))

    print("\n=== Mock Trading History ===")
    import json

    trades = create_mock_trading_history()
    trades_json = [json.loads(t.model_dump_json()) for t in trades]
    print(json.dumps(trades_json, indent=2))
