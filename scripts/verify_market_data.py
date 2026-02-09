"""Manual verification script for Step 3: eToro Market Data API.

This script tests the market data functions against the live eToro API
to verify they work with real data for a stock, crypto, and ETF.
"""

from agent.config import Settings
from agent.etoro.client import EToroClient
from agent.etoro.market_data import (
    get_candles,
    get_instrument_by_symbol,
    get_prices,
    search_instruments,
)


def main():
    settings = Settings()
    
    print("=" * 60)
    print("Step 3 Manual Verification: eToro Market Data API")
    print("=" * 60)
    
    with EToroClient(settings) as client:
        # Test 1: Search for instruments
        print("\n[1] Testing search_instruments('Apple')...")
        results = search_instruments(client, "Apple", page_size=5)
        print(f"    Found {len(results)} results")
        for r in results[:3]:
            print(f"    - {r.symbol}: {r.name} ({r.asset_class})")
        
        # Test 2: Get instrument by symbol for stock, crypto, ETF
        test_symbols = [
            ("AAPL", "Stock"),
            ("BTC", "Crypto"),
            ("SPY", "ETF"),
        ]
        
        instruments = []
        for symbol, asset_type in test_symbols:
            print(f"\n[2] Getting instrument by symbol: {symbol} ({asset_type})...")
            try:
                inst = get_instrument_by_symbol(client, symbol)
                instruments.append(inst)
                print(f"    ✓ Found: {inst.name} (ID: {inst.instrument_id})")
                print(f"      Asset class: {inst.asset_class}")
            except Exception as e:
                print(f"    ✗ Error: {e}")
        
        # Test 3: Get candles for each instrument
        print("\n[3] Fetching candle data...")
        for inst in instruments:
            try:
                candles = get_candles(client, inst.instrument_id, interval="OneDay", count=5)
                print(f"    {inst.symbol}: Got {len(candles)} candles")
                if candles:
                    latest = candles[0]
                    print(f"      Latest: O={latest.open:.2f} H={latest.high:.2f} L={latest.low:.2f} C={latest.close:.2f}")
            except Exception as e:
                print(f"    {inst.symbol}: Error - {e}")
        
        # Test 4: Get current prices
        if instruments:
            print("\n[4] Fetching current prices...")
            instrument_ids = [i.instrument_id for i in instruments]
            try:
                prices = get_prices(client, instrument_ids)
                for price in prices:
                    symbol = next((i.symbol for i in instruments if i.instrument_id == price.instrument_id), "?")
                    print(f"    {symbol}: Bid={price.bid:.2f} Ask={price.ask:.2f}")
            except Exception as e:
                print(f"    Error: {e}")
    
    print("\n" + "=" * 60)
    print("Verification complete!")
    print("=" * 60)


if __name__ == "__main__":
    main()
