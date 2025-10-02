"""
Create full Bitcoin historical data from 2009 to present
Combines early Bitcoin prices (2009-2014) with Yahoo Finance data (2014+)
"""

import pandas as pd
import yfinance as yf
from datetime import datetime

def create_early_bitcoin_data():
    """
    Create Bitcoin price data for 2009-2014 period
    Using known historical prices from various sources
    """
    # Historical Bitcoin prices (approximate monthly/key dates)
    early_prices = {
        '2009-01-03': 0.0,  # Genesis block
        '2009-10-05': 0.0009,  # First USD price established
        '2010-07-17': 0.05,
        '2010-11-06': 0.50,
        '2011-02-09': 1.00,  # Reached $1 parity
        '2011-06-08': 31.91,  # First major bubble peak
        '2011-11-18': 2.00,   # Post-bubble crash
        '2012-01-01': 5.27,
        '2012-09-15': 11.00,
        '2013-04-10': 266.00,  # Second bubble
        '2013-11-30': 1242.00,  # All-time high before crash
        '2014-01-01': 770.00,
        '2014-03-01': 600.00,
        '2014-09-16': 457.00,  # Day before Yahoo Finance data starts
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
    all_dates = pd.date_range(start='2009-01-03', end='2014-09-16', freq='D')
    df_daily = df.reindex(all_dates)
    df_daily['Close'] = df_daily['Close'].interpolate(method='linear')

    # For early Bitcoin, use Close price for OHLC
    df_daily['Open'] = df_daily['Close']
    df_daily['High'] = df_daily['Close']
    df_daily['Low'] = df_daily['Close']
    df_daily['Volume'] = 0  # Volume data not available for early period

    return df_daily

def combine_bitcoin_data():
    """Combine early Bitcoin data with Yahoo Finance data"""
    print("Creating comprehensive Bitcoin dataset (2009-present)...")

    # Create early Bitcoin data (2009-2014)
    print("Generating early Bitcoin data (2009-2014)...")
    early_df = create_early_bitcoin_data()
    print(f"  Created {len(early_df)} days of early Bitcoin data")

    # Download Yahoo Finance data (2014-present)
    print("Downloading Yahoo Finance data (2014-present)...")
    btc = yf.Ticker("BTC-USD")
    yahoo_df = btc.history(start='2014-09-17', end='2025-12-31', interval='1d')

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
    csv_path = 'btc_full_historical_data.csv'
    final_df.to_csv(csv_path)

    print(f"\nSaved full Bitcoin dataset to {csv_path}")
    print(f"Date range: {final_df.index[0]} to {final_df.index[-1]}")
    print(f"Total days: {len(final_df)}")

    return final_df

if __name__ == '__main__':
    df = combine_bitcoin_data()

    if df is not None:
        print("\nFirst Bitcoin price (2009):")
        print(df.head(3))
        print("\nBitcoin in 2013 bubble:")
        print(df.loc['2013-11-28':'2013-12-02'])
        print("\nRecent prices:")
        print(df.tail(3))
