"""
scripts/download_backtest_data.py — Binance historical data downloader.

Downloads and ingests into your Nautilus catalog:
  1. aggTrades  (data.binance.vision, free) → TradeTick
  2. 1h klines  (data.binance.vision, free) → Bar (for HTF structure engine)

Usage
-----
    # Download last 90 days
    python scripts/download_backtest_data.py --days 90

    # Specific date range
    python scripts/download_backtest_data.py --start 2024-10-01 --end 2025-01-01

    # Dry run (show what would be downloaded, no actual download)
    python scripts/download_backtest_data.py --days 90 --dry-run

    # Skip catalog ingestion (raw files only)
    python scripts/download_backtest_data.py --days 90 --no-ingest

Output layout
-------------
    data/
      raw/
        aggTrades/BTCUSDT/BTCUSDT-aggTrades-2024-10.zip   ← kept for reuse
        klines/BTCUSDT/1h/BTCUSDT-1h-2024-10.zip
      catalog/                                             ← Nautilus ParquetDataCatalog
        data/
          trade_tick/
          bar/
"""

from __future__ import annotations

import argparse
import hashlib
import io
import logging
import sys
import zipfile
from datetime import date, timedelta
from pathlib import Path
from typing import Iterator
from urllib.request import urlopen, Request
from urllib.error import HTTPError

import pandas as pd

# Nautilus imports — these will fail if nautilus_trader isn't installed,
# which is fine if you run with --no-ingest.
try:
    from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
    from nautilus_trader.model.enums import AggressorSide
    from nautilus_trader.model.data import TradeTick, Bar, BarType, BarSpecification
    from nautilus_trader.model.enums import BarAggregation, PriceType
    from nautilus_trader.model.objects import Price, Quantity
    from nautilus_trader.persistence.catalog import ParquetDataCatalog
    from nautilus_trader.core.datetime import dt_to_unix_nanos
    NAUTILUS_AVAILABLE = True
except ImportError:
    NAUTILUS_AVAILABLE = False

log = logging.getLogger("downloader")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)

# ── Config ─────────────────────────────────────────────────────────────────────

BASE_URL  = "https://data.binance.vision/data/spot/monthly"
SYMBOL    = "BTCUSDT"
DATA_DIR  = Path("data")
RAW_DIR   = DATA_DIR / "raw"
CATALOG_DIR = DATA_DIR / "catalog"

CHUNK_SIZE = 1024 * 256   # 256 KB read chunks
USER_AGENT = "nautilus-backtest-downloader/1.0"


# ── URL helpers ────────────────────────────────────────────────────────────────

def aggtrades_url(symbol: str, year: int, month: int) -> str:
    return f"{BASE_URL}/aggTrades/{symbol}/{symbol}-aggTrades-{year}-{month:02d}.zip"

def klines_url(symbol: str, interval: str, year: int, month: int) -> str:
    return f"{BASE_URL}/klines/{symbol}/{interval}/{symbol}-{interval}-{year}-{month:02d}.zip"

def checksum_url(data_url: str) -> str:
    return data_url + ".CHECKSUM"


# ── Month iterator ─────────────────────────────────────────────────────────────

def months_between(start: date, end: date) -> Iterator[tuple[int, int]]:
    """Yield (year, month) tuples from start to end inclusive."""
    cur = date(start.year, start.month, 1)
    end_month = date(end.year, end.month, 1)
    while cur <= end_month:
        yield cur.year, cur.month
        # Advance one month
        if cur.month == 12:
            cur = date(cur.year + 1, 1, 1)
        else:
            cur = date(cur.year, cur.month + 1, 1)


# ── Download helpers ───────────────────────────────────────────────────────────

def _fetch(url: str, dest: Path, dry_run: bool = False) -> bool:
    """
    Download url to dest. Returns True if downloaded, False if skipped (already exists).
    Verifies SHA256 checksum if a .CHECKSUM file is available.
    """
    if dest.exists():
        log.info("  SKIP (exists) %s", dest.name)
        return False

    if dry_run:
        log.info("  DRY-RUN would download %s", url)
        return False

    dest.parent.mkdir(parents=True, exist_ok=True)

    log.info("  GET %s", url)
    req = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(req) as resp:
            data = b""
            while chunk := resp.read(CHUNK_SIZE):
                data += chunk
    except HTTPError as e:
        if e.code == 404:
            log.warning("  404 — not yet available: %s", url)
            return False
        raise

    # Verify checksum if available
    try:
        ck_req = Request(checksum_url(url), headers={"User-Agent": USER_AGENT})
        with urlopen(ck_req) as resp:
            ck_line = resp.read().decode().strip()
            expected_sha = ck_line.split()[0]
            actual_sha = hashlib.sha256(data).hexdigest()
            if actual_sha != expected_sha:
                log.error("  CHECKSUM MISMATCH for %s — file not saved", dest.name)
                return False
            log.info("  checksum OK")
    except HTTPError:
        log.debug("  no checksum file available, skipping verification")

    dest.write_bytes(data)
    log.info("  saved → %s (%.1f MB)", dest, len(data) / 1e6)
    return True


# ── CSV parsers ────────────────────────────────────────────────────────────────

# aggTrades columns:
# agg_trade_id, price, qty, first_trade_id, last_trade_id, transact_time, is_buyer_maker

AGGTRADE_COLS = [
    "agg_trade_id", "price", "qty",
    "first_trade_id", "last_trade_id",
    "transact_time", "is_buyer_maker",
]

# klines columns (Binance standard 12-column format):
KLINES_COLS = [
    "open_time", "open", "high", "low", "close", "volume",
    "close_time", "quote_asset_volume", "num_trades",
    "taker_buy_base", "taker_buy_quote", "ignore",
]


def read_aggtrades_zip(zip_path: Path) -> pd.DataFrame:
    with zipfile.ZipFile(zip_path) as zf:
        csv_name = [n for n in zf.namelist() if n.endswith(".csv")][0]
        with zf.open(csv_name) as f:
            df = pd.read_csv(f, header=None, names=AGGTRADE_COLS)
    df["transact_time"] = pd.to_datetime(df["transact_time"], unit="ms", utc=True)
    df["price"] = df["price"].astype(float)
    df["qty"]   = df["qty"].astype(float)
    return df


def read_klines_zip(zip_path: Path) -> pd.DataFrame:
    with zipfile.ZipFile(zip_path) as zf:
        csv_name = [n for n in zf.namelist() if n.endswith(".csv")][0]
        with zf.open(csv_name) as f:
            df = pd.read_csv(f, header=None, names=KLINES_COLS)
    df["open_time"]  = pd.to_datetime(df["open_time"],  unit="ms", utc=True)
    df["close_time"] = pd.to_datetime(df["close_time"], unit="ms", utc=True)
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
    return df


# ── Nautilus ingestion ────────────────────────────────────────────────────────

def _instrument_id(symbol: str) -> "InstrumentId":
    return InstrumentId(Symbol(symbol), Venue("BINANCE"))


def ingest_aggtrades(df: pd.DataFrame, catalog: "ParquetDataCatalog", symbol: str) -> int:
    """Convert aggTrades DataFrame → list[TradeTick] and write to catalog."""
    iid = _instrument_id(symbol)
    ticks = []

    for row in df.itertuples(index=False):
        side = (
            AggressorSide.SELLER   # buyer is maker → seller is aggressor
            if row.is_buyer_maker
            else AggressorSide.BUYER
        )
        ts_ns = int(row.transact_time.timestamp() * 1e9)
        tick = TradeTick(
            instrument_id=iid,
            price=Price.from_str(f"{row.price:.2f}"),
            size=Quantity.from_str(f"{row.qty:.8f}"),
            aggressor_side=side,
            trade_id=str(row.agg_trade_id),
            ts_event=ts_ns,
            ts_init=ts_ns,
        )
        ticks.append(tick)

    catalog.write_data(ticks)
    return len(ticks)


def ingest_klines(df: pd.DataFrame, catalog: "ParquetDataCatalog", symbol: str, interval: str) -> int:
    """Convert klines DataFrame → list[Bar] and write to catalog."""
    iid = _instrument_id(symbol)

    # Map interval string to Nautilus BarSpecification
    interval_map = {
        "1h": (1, BarAggregation.HOUR),
        "4h": (4, BarAggregation.HOUR),
        "1d": (1, BarAggregation.DAY),
        "15m": (15, BarAggregation.MINUTE),
        "5m": (5, BarAggregation.MINUTE),
    }
    if interval not in interval_map:
        raise ValueError(f"Unsupported interval: {interval}. Add it to interval_map.")
    step, agg = interval_map[interval]

    bar_spec = BarSpecification(step, agg, PriceType.LAST)
    bar_type = BarType(iid, bar_spec)
    bars = []

    for row in df.itertuples(index=False):
        ts_event = int(row.close_time.timestamp() * 1e9)
        ts_init  = ts_event
        bar = Bar(
            bar_type=bar_type,
            open=Price.from_str(f"{row.open:.2f}"),
            high=Price.from_str(f"{row.high:.2f}"),
            low=Price.from_str(f"{row.low:.2f}"),
            close=Price.from_str(f"{row.close:.2f}"),
            volume=Quantity.from_str(f"{row.volume:.8f}"),
            ts_event=ts_event,
            ts_init=ts_init,
        )
        bars.append(bar)

    catalog.write_data(bars)
    return len(bars)


# ── Main download + ingest flow ────────────────────────────────────────────────

def run(
    start: date,
    end: date,
    symbol: str = SYMBOL,
    klines_interval: str = "1h",
    dry_run: bool = False,
    no_ingest: bool = False,
    raw_dir: Path = RAW_DIR,
    catalog_dir: Path = CATALOG_DIR,
) -> None:
    log.info("=" * 60)
    log.info("Binance backtest data downloader")
    log.info("  symbol   : %s", symbol)
    log.info("  range    : %s → %s", start, end)
    log.info("  klines   : %s", klines_interval)
    log.info("  dry_run  : %s", dry_run)
    log.info("  no_ingest: %s", no_ingest)
    log.info("=" * 60)

    if not NAUTILUS_AVAILABLE and not no_ingest:
        log.warning("nautilus_trader not importable — forcing --no-ingest")
        no_ingest = True

    catalog = None
    if not no_ingest:
        catalog_dir.mkdir(parents=True, exist_ok=True)
        catalog = ParquetDataCatalog(str(catalog_dir))
        log.info("Catalog: %s", catalog_dir.resolve())

    trade_dir  = raw_dir / "aggTrades" / symbol
    klines_dir = raw_dir / "klines" / symbol / klines_interval

    total_ticks = 0
    total_bars  = 0

    for year, month in months_between(start, end):
        log.info("")
        log.info("── %d-%02d ─────────────────────────────", year, month)

        # ── aggTrades ─────────────────────────────────────────────────────
        trade_zip = trade_dir / f"{symbol}-aggTrades-{year}-{month:02d}.zip"
        url = aggtrades_url(symbol, year, month)
        downloaded = _fetch(url, trade_zip, dry_run=dry_run)

        if not dry_run and not no_ingest and trade_zip.exists():
            log.info("  ingesting aggTrades …")
            df = read_aggtrades_zip(trade_zip)
            n  = ingest_aggtrades(df, catalog, symbol)
            total_ticks += n
            log.info("  ingested %s trade ticks", f"{n:,}")

        # ── klines ────────────────────────────────────────────────────────
        klines_zip = klines_dir / f"{symbol}-{klines_interval}-{year}-{month:02d}.zip"
        url = klines_url(symbol, klines_interval, year, month)
        _fetch(url, klines_zip, dry_run=dry_run)

        if not dry_run and not no_ingest and klines_zip.exists():
            log.info("  ingesting %s klines …", klines_interval)
            df = read_klines_zip(klines_zip)
            n  = ingest_klines(df, catalog, symbol, klines_interval)
            total_bars += n
            log.info("  ingested %s bars", f"{n:,}")

    log.info("")
    log.info("=" * 60)
    if not dry_run and not no_ingest:
        log.info("DONE  trade ticks: %s  bars: %s", f"{total_ticks:,}", f"{total_bars:,}")
        log.info("Catalog path: %s", catalog_dir.resolve())
    elif dry_run:
        log.info("DRY RUN complete — nothing downloaded or ingested")
    else:
        log.info("DONE  raw files in %s", raw_dir.resolve())
    log.info("=" * 60)


# ── CLI ────────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Download Binance historical data and ingest into Nautilus catalog.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/download_backtest_data.py --days 90
  python scripts/download_backtest_data.py --start 2024-07-01 --end 2025-01-01
  python scripts/download_backtest_data.py --days 30 --dry-run
  python scripts/download_backtest_data.py --days 90 --no-ingest
  python scripts/download_backtest_data.py --days 90 --symbol ETHUSDT --interval 1h
        """,
    )

    date_group = p.add_mutually_exclusive_group(required=True)
    date_group.add_argument(
        "--days", type=int,
        help="Download last N days of data (counting backwards from today).",
    )
    date_group.add_argument(
        "--start", type=date.fromisoformat,
        help="Start date (YYYY-MM-DD). Requires --end.",
    )

    p.add_argument("--end", type=date.fromisoformat, default=None,
                   help="End date (YYYY-MM-DD). Required with --start.")
    p.add_argument("--symbol", default=SYMBOL,
                   help=f"Binance symbol (default: {SYMBOL})")
    p.add_argument("--interval", default="1h",
                   help="Klines interval (default: 1h). Options: 5m 15m 1h 4h 1d")
    p.add_argument("--raw-dir", type=Path, default=RAW_DIR,
                   help=f"Raw zip storage directory (default: {RAW_DIR})")
    p.add_argument("--catalog-dir", type=Path, default=CATALOG_DIR,
                   help=f"Nautilus catalog directory (default: {CATALOG_DIR})")
    p.add_argument("--dry-run", action="store_true",
                   help="Print what would be downloaded without downloading.")
    p.add_argument("--no-ingest", action="store_true",
                   help="Download raw zips only, skip Nautilus catalog ingestion.")

    return p.parse_args()


def main() -> None:
    args = parse_args()

    if args.days:
        end   = date.today() - timedelta(days=1)   # yesterday (current month may be incomplete)
        start = end - timedelta(days=args.days)
    else:
        if args.end is None:
            log.error("--end is required when using --start")
            sys.exit(1)
        start = args.start
        end   = args.end

    run(
        start=start,
        end=end,
        symbol=args.symbol,
        klines_interval=args.interval,
        dry_run=args.dry_run,
        no_ingest=args.no_ingest,
        raw_dir=args.raw_dir,
        catalog_dir=args.catalog_dir,
    )


if __name__ == "__main__":
    main()