"""
Download Bitcoin historical data using yfinance and save as CSV
This creates a local copy for faster loading
"""

import yfinance as yf
import pandas as pd
import time

def download_bitcoin_data():
    """Download Bitcoin historical data from Yahoo Finance"""
    print("Downloading Bitcoin historical data from Yahoo Finance...")
    print("Getting data from 2014 (when BTC-USD became available) to present...")

    try:
        # Download Bitcoin data
        btc = yf.Ticker("BTC-USD")

        # Get all available historical data
        df = btc.history(start='2014-09-17', end='2025-12-31', interval='1d')

        if df.empty:
            print("X Error: No data downloaded")
            return None

        print(f"Downloaded {len(df)} days of Bitcoin price data")

        # Save to CSV
        csv_path = 'btc_historical_data.csv'
        df.to_csv(csv_path)
        print(f"Saved Bitcoin data to {csv_path}")
        print(f"Date range: {df.index[0]} to {df.index[-1]}")
        print(f"Total days: {len(df)}")

        return df

    except Exception as e:
        print(f"X Error downloading Bitcoin data: {e}")
        return None

if __name__ == '__main__':
    start_time = time.time()
    df = download_bitcoin_data()
    elapsed = time.time() - start_time
    print(f"\nCompleted in {elapsed:.2f}s")

    if df is not None:
        print("\nSample data (first 5 rows):")
        print(df.head())
        print("\nSample data (last 5 rows):")
        print(df.tail())
