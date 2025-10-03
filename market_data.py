import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, Optional

import pandas as pd


@dataclass(frozen=True)
class SymbolMetadata:
    symbol: str
    name: str
    asset_type: str  # e.g. STOCK, ETF, CRYPTO
    segment: Optional[str] = None
    shares_outstanding: Optional[float] = None


class MarketDataCatalog:
    """Local file-based market data registry."""

    def __init__(self, base_dir: Path | str = Path("data")) -> None:
        self.base_dir = Path(base_dir)
        self.stock_dir = self.base_dir / "stocks"
        self.crypto_dir = self.base_dir / "crypto"
        self._manifest: Dict[str, SymbolMetadata] = {}
        self.fallback_files: Dict[str, Path] = {
            'BTC-USD': Path('btc_full_historical_data.csv'),
            'ETH-USD': Path('eth_full_historical_data.csv'),
        }
        self._load_manifest()
        self._cache: Dict[str, pd.DataFrame] = {}

    # ------------------------------------------------------------------
    # Manifest helpers
    def _load_manifest(self) -> None:
        manifest_path = self.stock_dir / "manifest.json"
        if not manifest_path.exists():
            return

        with manifest_path.open("r", encoding="utf-8") as manifest_file:
            payload = json.load(manifest_file)

        entries = payload.get("symbols", []) if isinstance(payload, dict) else payload
        for entry in entries:
            symbol = entry.get("symbol")
            if not symbol:
                continue
            metadata = SymbolMetadata(
                symbol=symbol,
                name=entry.get("name", symbol),
                asset_type=entry.get("asset_type", "STOCK"),
                segment=entry.get("segment"),
                shares_outstanding=entry.get("shares_outstanding"),
            )
            self._manifest[symbol.upper()] = metadata

    def list_symbols(self, include_crypto: bool = True) -> Iterable[SymbolMetadata]:
        metadata = list(self._manifest.values())
        if include_crypto:
            crypto_manifest_path = self.crypto_dir / "manifest.json"
            if crypto_manifest_path.exists():
                with crypto_manifest_path.open("r", encoding="utf-8") as handle:
                    payload = json.load(handle)
                entries = payload.get("symbols", []) if isinstance(payload, dict) else payload
                for entry in entries:
                    symbol = entry.get("symbol")
                    if not symbol:
                        continue
                    metadata.append(
                        SymbolMetadata(
                            symbol=symbol,
                            name=entry.get("name", symbol),
                            asset_type=entry.get("asset_type", "CRYPTO"),
                            segment=entry.get("segment"),
                            shares_outstanding=entry.get("shares_outstanding"),
                        )
                    )
        return sorted(metadata, key=lambda item: item.symbol)

    def get_metadata(self, symbol: str) -> Optional[SymbolMetadata]:
        symbol_upper = symbol.upper()
        if symbol_upper in self._manifest:
            return self._manifest[symbol_upper]

        crypto_manifest_path = self.crypto_dir / "manifest.json"
        if crypto_manifest_path.exists():
            with crypto_manifest_path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
            entries = payload.get("symbols", []) if isinstance(payload, dict) else payload
            for entry in entries:
                if entry.get("symbol", "").upper() == symbol_upper:
                    return SymbolMetadata(
                        symbol=symbol_upper,
                        name=entry.get("name", symbol_upper),
                        asset_type=entry.get("asset_type", "CRYPTO"),
                        segment=entry.get("segment"),
                        shares_outstanding=entry.get("shares_outstanding"),
                    )
        return None

    # ------------------------------------------------------------------
    # Data access helpers
    def _resolve_path(self, symbol: str) -> Optional[Path]:
        symbol_upper = symbol.upper()
        fallback = self.fallback_files.get(symbol_upper)
        if fallback and fallback.exists():
            return fallback

        candidates = [
            self.stock_dir / f"{symbol_upper}.parquet",
            self.stock_dir / f"{symbol_upper}.csv",
            self.crypto_dir / f"{symbol_upper}.parquet",
            self.crypto_dir / f"{symbol_upper}.csv",
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return None

    def _load_frame(self, symbol: str) -> Optional[pd.DataFrame]:
        symbol_upper = symbol.upper()
        if symbol_upper in self._cache:
            return self._cache[symbol_upper]

        path = self._resolve_path(symbol_upper)
        if not path:
            return None

        if path.suffix == ".parquet":
            frame = pd.read_parquet(path)
        else:
            frame = pd.read_csv(path)
            if not frame.empty:
                first_col = frame.columns[0]
                if isinstance(first_col, str) and first_col.lower() in {"date", "unnamed: 0"}:
                    frame[first_col] = pd.to_datetime(frame[first_col], errors='coerce')
                if 'Date' in frame.columns:
                    frame['Date'] = pd.to_datetime(frame['Date'], errors='coerce')

        # Normalise expected columns/date index
        rename_map = {}
        if 'Date' in frame.columns:
            rename_map['Date'] = 'date'
        if len(frame.columns) and isinstance(frame.columns[0], str) and frame.columns[0].lower() == 'unnamed: 0':
            rename_map[frame.columns[0]] = 'date'
        if rename_map:
            frame = frame.rename(columns=rename_map)
        if 'date' not in frame.columns and frame.index.name in {'date', 'Date'}:
            frame = frame.rename_axis('date').reset_index()
        if 'date' in frame.columns:
            frame['date'] = pd.to_datetime(frame['date'], errors='coerce')
            frame = frame.dropna(subset=['date'])
            frame = frame.sort_values('date').set_index('date')

        self._cache[symbol_upper] = frame
        return frame

    def get_history(self, symbol: str) -> Optional[pd.DataFrame]:
        frame = self._load_frame(symbol)
        if frame is None:
            return None
        return frame

    def get_first_available_date(self, symbol: str) -> Optional[datetime]:
        frame = self._load_frame(symbol)
        if frame is None or frame.empty:
            return None
        return frame.index.min().to_pydatetime()

    def get_latest_market_cap(self, symbol: str) -> Optional[float]:
        frame = self._load_frame(symbol)
        if frame is None or frame.empty:
            return None
        market_cap_column = None
        for candidate in ("MarketCap", "market_cap", "marketcap"):
            if candidate in frame.columns:
                market_cap_column = candidate
                break
        if market_cap_column is None:
            metadata = self.get_metadata(symbol)
            if metadata and metadata.shares_outstanding:
                close_column = None
                for candidate in ("Close", "close", "Adj Close", "adj_close"):
                    if candidate in frame.columns:
                        close_column = candidate
                        break
                if close_column is None:
                    return None
                latest_close = frame[close_column].dropna().iloc[-1]
                return float(latest_close) * float(metadata.shares_outstanding)
            return None
        latest_value = frame[market_cap_column].dropna().iloc[-1]
        return float(latest_value)


catalog = MarketDataCatalog()
