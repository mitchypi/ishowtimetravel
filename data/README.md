# Local Market Data

This folder stores the offline market data used by `app.py`.

- `stocks/` – Parquet files for equities and ETFs plus `manifest.json` with metadata.
- `crypto/` – Parquet files for cryptocurrencies and their manifest.
- `sources/` – Helper inputs used by the data pipeline (e.g., candidate universe lists).

## Refresh workflow

1. Populate `data/sources/non_sp500_candidates.csv` with any additional tickers you want to
   consider for the "largest non-S&P 500" bucket. A starter template is generated the first
   time you run the pipeline.
2. Run `python scripts/build_market_data.py` to download OHLCV history and rebuild the
   manifests. See `python scripts/build_market_data.py --help` for more options (date range,
   throttling, skipping crypto, etc.).
3. Restart the Flask app so it reloads the refreshed parquet files.

The app expects daily data covering 2000-01-03 through today. You can experiment with a
shorter range while iterating, but be sure to regenerate the full window before shipping.
