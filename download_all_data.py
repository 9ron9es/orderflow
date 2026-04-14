#!/usr/bin/env python3
"""
Download ALL available BTCUSDT futures data from Binance (2024-01 through current month).
Combines all months into one large parquet file for backtesting.
"""

import requests
import zipfile
import io
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta
import logging
import sys

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger(__name__)

# ── CONFIG ─────────────────────────────────────────────
SYMBOL = "BTCUSDT"
START_YEAR_MONTH = (2024, 1)    # Start from January 2024
DATA_FILE = "./data_full.parquet"
# ───────────────────────────────────────────────────────

def get_available_months():
    """Get list of (year, month) tuples from START_YEAR_MONTH to today."""
    months = []
    year, month = START_YEAR_MONTH
    today = datetime.now()
    
    while (year, month) <= (today.year, today.month):
        months.append((year, month))
        month += 1
        if month > 12:
            month = 1
            year += 1
    
    return months

def download_month(symbol, year, month, retry=3):
    """Download single month of aggTrades from Binance."""
    url = (
        f"https://data.binance.vision/data/futures/um/monthly/aggTrades/"
        f"{symbol}/{symbol}-aggTrades-{year:04d}-{month:02d}.zip"
    )
    
    log.debug(f"[{year:04d}-{month:02d}] URL: {url}")
    
    for attempt in range(retry):
        try:
            log.info(f"[{year:04d}-{month:02d}] Downloading (attempt {attempt+1}/{retry})...")
            r = requests.get(url, timeout=30)
            r.raise_for_status()
            log.debug(f"[{year:04d}-{month:02d}] HTTP status: {r.status_code}, content size: {len(r.content)/1024:.1f}KB")
            
            z = zipfile.ZipFile(io.BytesIO(r.content))
            csv_name = z.namelist()[0]
            log.debug(f"[{year:04d}-{month:02d}] Archive contains: {csv_name}")
            
            df = pd.read_csv(
                z.open(csv_name),
                header=None,
                skiprows=1,
                names=[
                    "agg_id", "price", "qty", "first_trade_id", "last_trade_id",
                    "ts", "is_buyer_maker", "best_price_match"
                ],
                dtype=str,
                na_filter=False
            )
            # Convert key columns to numeric
            df["price"] = pd.to_numeric(df["price"], errors="coerce")
            df["qty"] = pd.to_numeric(df["qty"], errors="coerce")
            df["ts"] = pd.to_numeric(df["ts"], errors="coerce")
            # Drop any rows with NaN after conversion
            df = df.dropna(subset=["price", "qty", "ts"])
            log.info(f"[{year:04d}-{month:02d}] SUCCESS - {len(df):,} ticks loaded")
            log.debug(f"[{year:04d}-{month:02d}] Price: {df['price'].min():.2f}-{df['price'].max():.2f}, Qty: min={df['qty'].min():.8f}, max={df['qty'].max():.2f}, mean={df['qty'].mean():.6f}")
            return df
        except Exception as e:
            if attempt < retry - 1:
                log.warning(f"[{year:04d}-{month:02d}] Download failed: {e} - retrying ({attempt+1}/{retry-1})...")
            else:
                log.error(f"[{year:04d}-{month:02d}] FAILED after {retry} attempts: {e}")
                return None
    
    return None

def combine_and_save(all_months_data, output_file):
    """Combine all monthly data into one parquet file."""
    log.info(f"Combining {len(all_months_data)} months into single dataset...")
    
    df_combined = pd.concat(all_months_data, ignore_index=True)
    log.debug(f"Before sorting: {len(df_combined)} total rows")
    
    df_combined = df_combined.sort_values("ts").reset_index(drop=True)
    log.debug(f"After sorting: {len(df_combined)} total rows")
    
    ts_min = pd.to_datetime(df_combined['ts'].min(), unit='ms', utc=True)
    ts_max = pd.to_datetime(df_combined['ts'].max(), unit='ms', utc=True)
    log.info(f"Combined data stats:")
    log.info(f"  Total rows: {len(df_combined):,}")
    log.info(f"  Date range: {ts_min} to {ts_max}")
    log.info(f"  Time span: {(ts_max - ts_min).days} days")
    log.debug(f"  Price range: {df_combined['price'].min()}-{df_combined['price'].max()}")
    log.debug(f"  Qty: min={df_combined['qty'].min()}, max={df_combined['qty'].max()}, mean={df_combined['qty'].mean():.2f}")
    
    log.info(f"Saving to parquet: {output_file}")
    df_combined.to_parquet(output_file, index=False)
    file_size = Path(output_file).stat().st_size / (1024**2)
    log.info(f"Saved: {output_file} ({file_size:.1f} MB)")

if __name__ == "__main__":
    months = get_available_months()
    log.info("START: Downloading BTCUSDT futures data")
    log.info(f"Period: {months[0][0]}-{months[0][1]:02d} to {months[-1][0]}-{months[-1][1]:02d}")
    log.info(f"Total months to download: {len(months)}")
    log.debug(f"Months: {months}")
    log.info("-" * 80)
    
    all_data = []
    success_count = 0
    fail_count = 0
    
    for idx, (year, month) in enumerate(months, 1):
        log.info(f"[{idx}/{len(months)}] Processing {year:04d}-{month:02d}")
        df = download_month(SYMBOL, year, month)
        if df is not None:
            all_data.append(df)
            success_count += 1
        else:
            fail_count += 1
    
    log.info("-" * 80)
    log.info(f"DOWNLOAD SUMMARY:")
    log.info(f"  Success: {success_count}/{len(months)} months")
    log.info(f"  Failed: {fail_count}/{len(months)} months")
    
    if all_data:
        log.info(f"Proceeding with {success_count} months of data...")
        if fail_count > 0:
            log.warning(f"Note: {fail_count} months failed (may not exist yet)")
        
        combine_and_save(all_data, DATA_FILE)
        log.info("-" * 80)
        log.info(f"SUCCESS: All data saved to {DATA_FILE}")
        log.info(f"Next step: Run backtest with: python run_backtest_full.py")
    else:
        log.error(f"FAILED: No data downloaded - check internet connection or URL")
        sys.exit(1)
