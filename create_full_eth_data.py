"""
Create full Ethereum historical data from 2015 to present
Combines early Ethereum prices (2015) with Yahoo Finance data (2017+)
"""

import pandas as pd
import yfinance as yf
from datetime import datetime

def create_early_ethereum_data():
    """
    Create Ethereum price data for 2015-2017 period
    Using known historical prices from Ethereum's launch
    """
    # Historical Ethereum prices (key dates and approximate values)
    early_prices = {
        '2015-07-30': 0.0,     # Genesis block / Network launch
        '2015-08-07': 2.77,    # First trading on exchanges
        '2015-08-08': 0.68,    # Early volatility
        '2015-09-01': 1.20,
        '2015-10-01': 0.45,
        '2015-11-01': 0.90,
        '2015-12-01': 0.95,
        '2016-01-01': 0.93,
        '2016-03-01': 10.00,   # The DAO launch hype
        '2016-06-17': 14.50,   # Before DAO hack
        '2016-06-18': 10.00,   # DAO hack
        '2016-07-20': 12.00,   # Hard fork / ETC split
        '2016-10-01': 12.50,
        '2016-12-01': 8.00,
        '2017-01-01': 8.06,
        '2017-03-01': 15.00,
        '2017-05-01': 85.00,
        '2017-06-12': 396.00,  # Mid-2017 rally
        '2017-07-16': 150.00,  # Correction
        '2017-09-01': 385.00,  # Day before Yahoo data starts
    }

    # Convert to DataFrame
    dates = pd.to_datetime(list(early_prices.keys()))
    prices = list(early_prices.values())

    df = pd.DataFrame({
        'Date': dates,
        'Close': prices
    })
    df = df.set_index('Date')

    # Interpolate daily values between known prices
    all_dates = pd.date_range(start='2015-07-30', end='2017-09-01', freq='D')
    df_daily = df.reindex(all_dates)
    df_daily['Close'] = df_daily['Close'].interpolate(method='linear')

    # For early Ethereum, use Close price for OHLC
    df_daily['Open'] = df_daily['Close']
    df_daily['High'] = df_daily['Close']
    df_daily['Low'] = df_daily['Close']
    df_daily['Volume'] = 0  # Volume data not reliably available for early period

    return df_daily

def combine_ethereum_data():
    """Combine early Ethereum data with Yahoo Finance data"""
    print("Creating comprehensive Ethereum dataset (2015-present)...")

    # Create early Ethereum data (2015-2017)
    print("Generating early Ethereum data (2015-2017)...")
    early_df = create_early_ethereum_data()
    print(f"  Created {len(early_df)} days of early Ethereum data")

    # Download Yahoo Finance data (2017-present)
    print("Downloading Yahoo Finance data (2017-present)...")
    eth = yf.Ticker("ETH-USD")
    yahoo_df = eth.history(start='2017-09-02', end='2025-12-31', interval='1d')

    if yahoo_df.empty:
        print("  Warning: Could not download Yahoo Finance data")
        final_df = early_df
    else:
        print(f"  Downloaded {len(yahoo_df)} days from Yahoo Finance")

        # Standardize column names
        yahoo_df = yahoo_df[['Open', 'High', 'Low', 'Close', 'Volume']]

        # Remove timezone info for combining
        yahoo_df.index = yahoo_df.index.tz_localize(None)

        # Combine datasets
        final_df = pd.concat([early_df, yahoo_df])

    # Save to CSV
    csv_path = 'eth_full_historical_data.csv'
    final_df.to_csv(csv_path)

    print(f"\nSaved full Ethereum dataset to {csv_path}")
    print(f"Date range: {final_df.index[0]} to {final_df.index[-1]}")
    print(f"Total days: {len(final_df)}")

    return final_df

if __name__ == '__main__':
    df = combine_ethereum_data()

    if df is not None:
        print("\nFirst Ethereum price (2015):")
        print(df.head(3))
        print("\nEthereum during DAO hack (2016):")
        print(df.loc['2016-06-17':'2016-06-20'])
        print("\nRecent prices:")
        print(df.tail(3))
