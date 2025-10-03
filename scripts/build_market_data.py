#!/usr/bin/env python3
"""
Build local market data files for the Stock Market Time Machine app.

The pipeline performs four high-level tasks:

1. Collect a curated symbol universe (S&P 500 constituents, the largest
   non-S&P equities, a short list of flagship ETFs, and any custom additions).
2. Download daily OHLCV bars from Yahoo Finance for the selected window
   (default 2000-01-03 through today).
3. Enrich each dataset with a MarketCap column derived from the close price
   and latest shares-outstanding metadata.
4. Persist per-symbol Parquet files plus manifest.json indexes that the app
   can load instantly without hitting external APIs at runtime.

Run this script periodically to refresh cached data:
    python scripts/build_market_data.py

Notes:
- The first run may take a while; consider executing overnight.
- Edit data/sources/non_sp500_candidates.csv to tune the candidate list used
  for "top 100 non S&P" selection. A template is generated automatically if
  the file is missing.
- Set the --start/--end flags to shorten the historical window while testing.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import pandas as pd
import requests
import yfinance as yf
from tqdm import tqdm
from io import StringIO

DATA_ROOT = Path(__file__).resolve().parents[1] / "data"
STOCK_DIR = DATA_ROOT / "stocks"
CRYPTO_DIR = DATA_ROOT / "crypto"
SOURCES_DIR = DATA_ROOT / "sources"

DEFAULT_START_DATE = "2000-01-03"
DEFAULT_END_DATE = datetime.utcnow().strftime("%Y-%m-%d")

EXTRA_CANDIDATE_FILE = SOURCES_DIR / "non_sp500_candidates.csv"
SP500_CACHE_FILE = SOURCES_DIR / "sp500_constituents.csv"

ETF_TICKERS = [
    {"symbol": "SPY", "name": "SPDR S&P 500 ETF Trust"},
    {"symbol": "IVV", "name": "iShares Core S&P 500 ETF"},
    {"symbol": "VOO", "name": "Vanguard S&P 500 ETF"},
    {"symbol": "QQQ", "name": "Invesco QQQ Trust"},
    {"symbol": "VTI", "name": "Vanguard Total Stock Market ETF"},
    {"symbol": "IWM", "name": "iShares Russell 2000 ETF"},
    {"symbol": "EFA", "name": "iShares MSCI EAFE ETF"},
    {"symbol": "EEM", "name": "iShares MSCI Emerging Markets ETF"},
    {"symbol": "XLK", "name": "Technology Select Sector SPDR Fund"},
    {"symbol": "XLF", "name": "Financial Select Sector SPDR Fund"},
]

CRYPTO_TICKERS = [
    {"symbol": "BTC-USD", "name": "Bitcoin"},
    {"symbol": "ETH-USD", "name": "Ethereum"},
]

SPECIAL_ADDITIONS = [
    {"symbol": "RNMBY", "name": "Rheinmetall AG"},
]

# Broad fallback list used to seed the non-S&P 500 candidate pool when the
# CSV file is missing. Adjust as needed; more rows are always welcome.
DEFAULT_EXTRA_CANDIDATES: List[Tuple[str, str]] = [
    ("TSM", "Taiwan Semiconductor Manufacturing"),
    ("BABA", "Alibaba Group"),
    ("ASML", "ASML Holding"),
    ("TM", "Toyota Motor"),
    ("NVO", "Novo Nordisk"),
    ("NSRGY", "Nestle"),
    ("RHHBY", "Roche Holding"),
    ("LVMUY", "LVMH Moet Hennessy Louis Vuitton"),
    ("SHEL", "Shell"),
    ("BP", "BP"),
    ("RIO", "Rio Tinto"),
    ("BHP", "BHP Group"),
    ("HSBC", "HSBC Holdings"),
    ("SONY", "Sony Group"),
    ("SAP", "SAP"),
    ("UL", "Unilever"),
    ("NVS", "Novartis"),
    ("AZN", "AstraZeneca"),
    ("GSK", "GSK"),
    ("SNY", "Sanofi"),
    ("VWAGY", "Volkswagen"),
    ("OR", "L'Oreal"),
    ("BUD", "Anheuser-Busch InBev"),
    ("TTE", "TotalEnergies"),
    ("ENB", "Enbridge"),
    ("EQNR", "Equinor"),
    ("SU", "Suncor Energy"),
    ("CNQ", "Canadian Natural Resources"),
    ("SHOP", "Shopify"),
    ("TCEHY", "Tencent"),
    ("JD", "JD.com"),
    ("PDD", "PDD Holdings"),
    ("NIO", "NIO"),
    ("LI", "Li Auto"),
    ("XPEV", "XPeng"),
    ("HMC", "Honda Motor"),
    ("SMFG", "Sumitomo Mitsui Financial"),
    ("MFG", "Mizuho Financial"),
    ("MUFG", "Mitsubishi UFJ Financial"),
    ("SFTBY", "SoftBank Group"),
    ("KB", "KB Financial Group"),
    ("KT", "KT Corporation"),
    ("SKM", "SK Telecom"),
    ("CHT", "Chunghwa Telecom"),
    ("SNP", "China Petroleum & Chemical"),
    ("PTR", "PetroChina"),
    ("LFC", "China Life Insurance"),
    ("PNGAY", "Ping An Insurance"),
    ("BIDU", "Baidu"),
    ("NTES", "NetEase"),
    ("CM", "Canadian Imperial Bank of Commerce"),
    ("BMO", "Bank of Montreal"),
    ("RY", "Royal Bank of Canada"),
    ("TD", "Toronto-Dominion Bank"),
    ("BNS", "Bank of Nova Scotia"),
    ("ING", "ING Groep"),
    ("BBVA", "Banco Bilbao Vizcaya Argentaria"),
    ("SAN", "Banco Santander"),
    ("UBS", "UBS Group"),
    ("DB", "Deutsche Bank"),
    ("PHG", "Koninklijke Philips"),
    ("AEG", "Aegon"),
    ("ABB", "ABB"),
    ("NOK", "Nokia"),
    ("ERIC", "Ericsson"),
    ("STLA", "Stellantis"),
    ("BYDDY", "BYD"),
    ("LPL", "LG Display"),
    ("CRH", "CRH plc"),
    ("AMX", "America Movil"),
    ("IBN", "ICICI Bank"),
    ("HDB", "HDFC Bank"),
    ("VOD", "Vodafone"),
    ("TEF", "Telefonica"),
    ("CHA", "China Telecom"),
    ("LYG", "Lloyds Banking Group"),
    ("RELX", "RELX"),
    ("DTEGY", "Deutsche Telekom"),
    ("SIEGY", "Siemens"),
    ("DEO", "Diageo"),
    ("BTI", "British American Tobacco"),
    ("IMBBY", "Imperial Brands"),
    ("HEINY", "Heineken"),
    ("ADRNY", "Ahold Delhaize"),
    ("ITOCY", "Itochu"),
    ("MITSY", "Mitsui"),
    ("IX", "ORIX"),
    ("CAJ", "Canon"),
    ("FUJHY", "Subaru"),
    ("PKX", "POSCO"),
    ("YUMC", "Yum China"),
    ("EDU", "New Oriental Education"),
    ("TAL", "TAL Education"),
    ("VIPS", "Vipshop"),
    ("GRFS", "Grifols"),
    ("SID", "Companhia Siderurgica Nacional"),
    ("VALE", "Vale"),
    ("SBSW", "Sibanye Stillwater"),
    ("GLNCY", "Glencore"),
    ("NGLOY", "Anglo American"),
    ("GOLD", "Barrick Gold"),
    ("AEM", "Agnico Eagle Mines"),
    ("WPM", "Wheaton Precious Metals"),
    ("FNV", "Franco-Nevada"),
    ("FMX", "Fomento Economico Mexicano"),
    ("CPNG", "Coupang"),
    ("SE", "Sea Limited"),
    ("ARM", "Arm Holdings"),
    ("NGG", "National Grid"),
    ("PUK", "Prudential plc"),
    ("ENLAY", "Enel"),
    ("EDPFY", "EDP - Energias de Portugal"),
    ("IBDRY", "Iberdrola"),
    ("ORAN", "Orange"),
    ("TEVA", "Teva Pharmaceutical"),
    ("GELYF", "Geely Automobile"),
    ("CKHUY", "CK Hutchison"),
]


def ensure_directories() -> None:
    STOCK_DIR.mkdir(parents=True, exist_ok=True)
    CRYPTO_DIR.mkdir(parents=True, exist_ok=True)
    SOURCES_DIR.mkdir(parents=True, exist_ok=True)


def fetch_sp500_table() -> pd.DataFrame:
    ensure_directories()
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; StockTimeMachine/1.0; +https://localhost)",
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        html = StringIO(response.text)
        tables = pd.read_html(html, match="Symbol")
        if not tables:
            raise RuntimeError("Unable to locate S&P 500 table in response")
        table = tables[0].copy()
    except Exception as exc:
        if SP500_CACHE_FILE.exists():
            print(f"  Warning: could not refresh S&P 500 list ({exc}). Using cached copy.")
            cached = pd.read_csv(SP500_CACHE_FILE)
            return cached
        raise

    table.columns = [str(col).strip().lower() for col in table.columns]
    rename_map = {
        "symbol": "symbol",
        "security": "name",
        "gics sector": "sector",
    }
    for old, new in rename_map.items():
        if old not in table.columns:
            raise RuntimeError(f"Expected column '{old}' missing from S&P table")
        table[new] = table[old]

    result = table[["symbol", "name", "sector"]].copy()
    result["symbol"] = result["symbol"].astype(str).str.upper().str.strip()
    result["name"] = result["name"].astype(str).str.strip()
    result["sector"] = result["sector"].astype(str).str.strip()

    SP500_CACHE_FILE.write_text(result.to_csv(index=False), encoding="utf-8")
    return result


def load_extra_candidates() -> pd.DataFrame:
    ensure_directories()
    if not EXTRA_CANDIDATE_FILE.exists():
        template = pd.DataFrame(DEFAULT_EXTRA_CANDIDATES, columns=["symbol", "name"])
        template.to_csv(EXTRA_CANDIDATE_FILE, index=False)
        print(
            f"Wrote template candidate list to {EXTRA_CANDIDATE_FILE}. "
            "Review, edit, and rerun for precise control.",
            file=sys.stderr,
        )
    df = pd.read_csv(EXTRA_CANDIDATE_FILE)
    required_cols = {"symbol", "name"}
    if not required_cols.issubset({col.lower() for col in df.columns}):
        raise RuntimeError(
            f"Candidate file must contain columns: {sorted(required_cols)}"
        )
    df = df.rename(columns={col: col.lower() for col in df.columns})
    df["symbol"] = df["symbol"].astype(str).str.upper().str.strip()
    df["name"] = df["name"].astype(str).str.strip()
    df = df.drop_duplicates(subset="symbol")
    df = df[df["symbol"].str.len() > 0]
    return df


def fetch_market_metadata(symbols: Sequence[str], throttle: float, progress_label: str = "Metadata") -> Dict[str, Dict[str, Optional[float]]]:
    sequence = list(symbols)
    meta: Dict[str, Dict[str, Optional[float]]] = {}
    if not sequence:
        return meta

    for symbol in tqdm(sequence, desc=progress_label, unit="symbol"):
        ticker = yf.Ticker(symbol)
        record: Dict[str, Optional[float]] = {"market_cap": None, "shares_outstanding": None, "name": None, "sector": None}
        try:
            fast = ticker.fast_info
        except Exception:
            fast = None
        if fast:
            record["market_cap"] = getattr(fast, "market_cap", None)
            record["shares_outstanding"] = getattr(fast, "shares_outstanding", None)
        if not record["market_cap"] or not record["shares_outstanding"] or not record["name"]:
            try:
                info = ticker.info
            except Exception:
                info = {}
            record["market_cap"] = record["market_cap"] or info.get("marketCap") or info.get("market_cap")
            record["shares_outstanding"] = record["shares_outstanding"] or info.get("sharesOutstanding") or info.get("shares_outstanding")
            record["name"] = info.get("longName") or info.get("shortName") or record.get("name")
            record["sector"] = info.get("sector") or info.get("category") or record.get("sector")
        meta[symbol] = record
        if throttle > 0:
            time.sleep(throttle)
    return meta


def shortlist_extras(
    sp500_symbols: Sequence[str],
    candidate_df: pd.DataFrame,
    metadata: Dict[str, Dict[str, Optional[float]]],
    limit: int,
) -> List[str]:
    sp500_set = {symbol.upper() for symbol in sp500_symbols}
    candidates = []
    for symbol in candidate_df["symbol"]:
        if symbol in sp500_set:
            continue
        candidates.append(symbol)
    unique_candidates = list(dict.fromkeys(candidates))

    def market_cap_value(symbol: str) -> float:
        value = metadata.get(symbol, {}).get("market_cap")
        return float(value) if value else 0.0

    sorted_candidates = sorted(unique_candidates, key=market_cap_value, reverse=True)
    return sorted_candidates[:limit]


def dedupe_preserve_order(symbols: Iterable[str]) -> List[str]:
    seen: set[str] = set()
    ordered: List[str] = []
    for symbol in symbols:
        upper = symbol.upper()
        if upper in seen:
            continue
        seen.add(upper)
        ordered.append(upper)
    return ordered


def download_history(symbol: str, start: str, end: str) -> pd.DataFrame:
    ticker = yf.Ticker(symbol)
    frame = ticker.history(start=start, end=end, auto_adjust=False, actions=False)
    if frame.empty:
        return frame
    frame = frame.copy()
    frame.index = frame.index.tz_localize(None)
    keep = [col for col in ["Open", "High", "Low", "Close", "Adj Close", "Volume"] if col in frame.columns]
    return frame[keep]


def persist_history(
    symbol: str,
    frame: pd.DataFrame,
    dest_dir: Path,
    shares_outstanding: Optional[float],
) -> Tuple[str, str]:
    dataset = frame.copy()
    close_series = None
    for candidate in ("Close", "Adj Close"):
        if candidate in dataset.columns:
            close_series = dataset[candidate]
            break
    if close_series is not None and shares_outstanding:
        dataset["MarketCap"] = close_series.astype(float) * float(shares_outstanding)
    else:
        dataset["MarketCap"] = float('nan')
    dataset.index.name = "date"
    dest_path = dest_dir / f"{symbol.upper()}.parquet"
    dataset.to_parquet(dest_path)
    first_date = dataset.index.min().strftime("%Y-%m-%d")
    last_date = dataset.index.max().strftime("%Y-%m-%d")
    return first_date, last_date


def build_manifest(entries: List[Dict[str, object]], path: Path) -> None:
    payload = {
        "generated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "symbol_count": len(entries),
        "symbols": entries,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def process_stocks(args: argparse.Namespace) -> None:
    print("Fetching S&P 500 constituents...")
    sp500 = fetch_sp500_table()
    print(f"  Loaded {len(sp500)} S&P 500 tickers")
    sp500_name_map = dict(zip(sp500["symbol"], sp500["name"]))
    sp500_sector_map = dict(zip(sp500["symbol"], sp500["sector"]))

    print("Loading non-S&P candidate list...")
    extra_candidates = load_extra_candidates()
    print(f"  Candidate pool contains {len(extra_candidates)} symbols")
    candidate_name_map = dict(zip(extra_candidates["symbol"], extra_candidates["name"]))

    universe_for_metadata = set(sp500["symbol"].tolist())
    universe_for_metadata.update(extra_candidates["symbol"].tolist())
    for entry in ETF_TICKERS + SPECIAL_ADDITIONS:
        universe_for_metadata.add(entry["symbol"])

    print(f"Requesting metadata for {len(universe_for_metadata)} equities...")
    metadata = fetch_market_metadata(sorted(universe_for_metadata), args.throttle, progress_label="Equity metadata")

    extra_symbols = shortlist_extras(sp500["symbol"].tolist(), extra_candidates, metadata, args.extra_limit)
    if len(extra_symbols) < args.extra_limit:
        print(
            f"  Warning: only {len(extra_symbols)} non-S&P candidates available. "
            "Add more rows to the candidate CSV for better coverage.",
            file=sys.stderr,
        )

    stock_symbols = dedupe_preserve_order(
        list(sp500["symbol"]) + extra_symbols + [etf["symbol"] for etf in ETF_TICKERS] + [item["symbol"] for item in SPECIAL_ADDITIONS]
    )

    print(f"Downloading history for {len(stock_symbols)} equity and ETF symbols...")
    manifest_entries: List[Dict[str, object]] = []
    skipped_symbols: List[str] = []
    for symbol in tqdm(stock_symbols, desc="Equity/ETF history", unit="symbol"):
        frame = download_history(symbol, args.start, args.end)
        if frame.empty:
            skipped_symbols.append(symbol)
            tqdm.write(f"{symbol}: skipped (no data)")
            continue
        raw_shares = metadata.get(symbol, {}).get("shares_outstanding")
        shares_outstanding = float(raw_shares) if raw_shares else None
        first_date, last_date = persist_history(symbol, frame, STOCK_DIR, shares_outstanding)
        info = metadata.get(symbol, {})
        name = (
            info.get("name")
            or sp500_name_map.get(symbol)
            or candidate_name_map.get(symbol)
            or next((item['name'] for item in SPECIAL_ADDITIONS if item['symbol'] == symbol), None)
            or symbol
        )
        asset_type = "ETF" if any(symbol == etf["symbol"] for etf in ETF_TICKERS) else "STOCK"
        segment = sp500_sector_map.get(symbol) if symbol in sp500_name_map else info.get("sector")
        manifest_entries.append(
            {
                "symbol": symbol,
                "name": name or symbol,
                "asset_type": asset_type,
                "segment": segment,
                "shares_outstanding": shares_outstanding,
                "first_date": first_date,
                "last_date": last_date,
            }
        )
        tqdm.write(f"{symbol}: saved {len(frame):,} rows from {first_date} to {last_date}")
    build_manifest(manifest_entries, STOCK_DIR / "manifest.json")
    tqdm.write(f"Stock manifest written with {len(manifest_entries)} entries")
    if skipped_symbols:
        listed = ', '.join(skipped_symbols[:10])
        suffix = '...' if len(skipped_symbols) > 10 else ''
        tqdm.write(f"Skipped {len(skipped_symbols)} symbols with no data: {listed}{suffix}")


def process_crypto(args: argparse.Namespace) -> None:
    print("Downloading cryptocurrency history...")
    metadata = fetch_market_metadata([item["symbol"] for item in CRYPTO_TICKERS], args.throttle, progress_label="Crypto metadata")
    manifest_entries: List[Dict[str, object]] = []
    for entry in tqdm(CRYPTO_TICKERS, desc="Crypto history", unit="symbol"):
        symbol = entry["symbol"]
        frame = download_history(symbol, args.start, args.end)
        if frame.empty:
            tqdm.write(f"{symbol}: skipped (no data)")
            continue
        first_date, last_date = persist_history(symbol, frame, CRYPTO_DIR, metadata.get(symbol, {}).get("shares_outstanding"))
        manifest_entries.append(
            {
                "symbol": symbol,
                "name": entry["name"],
                "asset_type": "CRYPTO",
                "segment": "Digital Asset",
                "shares_outstanding": metadata.get(symbol, {}).get("shares_outstanding"),
                "first_date": first_date,
                "last_date": last_date,
            }
        )
        tqdm.write(f"{symbol}: saved {len(frame):,} rows from {first_date} to {last_date}")
    build_manifest(manifest_entries, CRYPTO_DIR / "manifest.json")
    tqdm.write(f"Crypto manifest written with {len(manifest_entries)} entries")


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate local market data caches")
    parser.add_argument("--start", default=DEFAULT_START_DATE, help="Start date (YYYY-MM-DD), default 2000-01-03")
    parser.add_argument("--end", default=DEFAULT_END_DATE, help="End date (YYYY-MM-DD), default today")
    parser.add_argument("--extra-limit", type=int, default=100, help="Number of non-S&P equities to include")
    parser.add_argument("--skip-stocks", action="store_true", help="Skip equity/ETF downloads")
    parser.add_argument("--skip-crypto", action="store_true", help="Skip cryptocurrency downloads")
    parser.add_argument("--throttle", type=float, default=0.1, help="Delay between metadata requests (seconds)")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = parse_args(argv)
    ensure_directories()
    if not args.skip_stocks:
        process_stocks(args)
    if not args.skip_crypto:
        process_crypto(args)
    print("All tasks complete")


if __name__ == "__main__":
    main()
