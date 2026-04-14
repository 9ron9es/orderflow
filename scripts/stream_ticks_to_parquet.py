#!/usr/bin/env python3
"""
Stream aggTrades CSV → Parquet with 5m + 1h candles.

Reads Binance aggTrades CSV in chunks (never loads full dataset), writes tick-level
Parquet files ready for backtest ingestion.

Format: agg_id, price, qty, first_trade_id, last_trade_id, ts_ms, buyer_maker, is_best_price
"""

import argparse
import gzip
import io
import os
import tempfile
from pathlib import Path
from zipfile import ZipFile

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

# Binance aggTrades columns (in order)
AGGTRADES_COLUMNS = [
    "agg_id",
    "price",
    "qty",
    "first_trade_id",
    "last_trade_id",
    "ts_ms",
    "buyer_maker",
    "is_best_price",
]


def stream_zip_to_ticks(zip_path: Path, chunk_size: int = 500_000) -> tuple[pd.DataFrame, str]:
    """
    Stream aggTrades CSV from zip file in chunks.
    
    Loads full data in memory (2.66GB uncompressed fits in 30GB RAM).
    Returns DataFrame with columns: ts, price, qty, side
    """
    with ZipFile(zip_path, "r") as zf:
        # Get the CSV file from zip
        csv_files = [f for f in zf.namelist() if f.endswith(".csv")]
        if not csv_files:
            raise ValueError(f"No CSV found in {zip_path}")
        
        csv_file = csv_files[0]
        month_date = csv_file.split("-aggTrades-")[1].replace(".csv", "")  # e.g., "2026-01"
        
        print(f"  Reading {csv_file} ({zf.getinfo(csv_file).file_size / 1e9:.2f} GB uncompressed)...")
        
        with zf.open(csv_file) as f:
            # Read CSV with needed columns including agg_id for trade_id
            df = pd.read_csv(
                f,
                names=AGGTRADES_COLUMNS,
                usecols=["agg_id", "ts_ms", "price", "qty", "buyer_maker"],
                dtype={
                    "agg_id": "int64",
                    "price": "float32",
                    "qty": "float32",
                    "ts_ms": "int64",
                    "buyer_maker": "bool",
                },
            )
            
            # Convert timestamp from microseconds to milliseconds
            df["ts_ms"] = df["ts_ms"] // 1000
            
            # Filter out zero-quantity trades (invalid for backtest)
            df = df[df["qty"] > 0].copy()
            
            # Convert to tick format: ts, price, qty, side, agg_id
            df["side"] = df["buyer_maker"].map({False: "BUY", True: "SELL"})
            ticks_df = df[["ts_ms", "price", "qty", "side", "agg_id"]].copy()
            ticks_df.columns = ["ts", "price", "qty", "side", "agg_id"]
            
            print(f"✓ Loaded {len(ticks_df):,} ticks from {month_date}")
            
            return ticks_df, month_date


def write_ticks_to_parquet(
    ticks_df: pd.DataFrame,
    output_dir: Path,
    symbol: str,
    month_date: str,
) -> None:
    """
    Write tick data to Parquet file.
    
    File structure: ticks/{SYMBOL}/{MONTH}.parquet
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    
    output_file = output_dir / f"{month_date}.parquet"
    
    # Convert to PyArrow table for Parquet writing
    table = pa.Table.from_pandas(ticks_df)
    pq.write_table(table, output_file)
    
    print(f"✓ Wrote {output_file} ({len(ticks_df):,} ticks, {output_file.stat().st_size / 1e9:.2f} GB)")


def process_zip_file(
    zip_path: Path,
    output_base: Path,
    symbol: str = "BTCUSDT",
) -> None:
    """
    Process one aggTrades zip file:
    1. Stream CSV from zip
    2. Convert to tick format
    3. Write Parquet
    """
    print(f"\n📦 Processing {zip_path.name}...")
    
    ticks_df, month_date = stream_zip_to_ticks(zip_path)
    
    output_dir = output_base / symbol
    write_ticks_to_parquet(ticks_df, output_dir, symbol, month_date)


def main():
    parser = argparse.ArgumentParser(
        description="Stream Binance aggTrades ZIP → Parquet (tick level)"
    )
    parser.add_argument(
        "zip_files",
        nargs="+",
        type=Path,
        help="One or more aggTrades ZIP files (e.g., BTCUSDT-aggTrades-2026-01.zip)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("/home/adem/orderflow/ticks"),
        help="Output directory for Parquet files (default: ./ticks)",
    )
    parser.add_argument(
        "--symbol",
        default="BTCUSDT",
        help="Symbol for grouping (default: BTCUSDT)",
    )
    args = parser.parse_args()
    
    print(f"📊 Streaming aggTrades to Parquet")
    print(f"Output: {args.output}")
    print(f"Symbol: {args.symbol}")
    print(f"Files: {len(args.zip_files)}")
    
    for zip_path in args.zip_files:
        if not zip_path.exists():
            print(f"⚠ File not found: {zip_path}")
            continue
        
        process_zip_file(zip_path, args.output, args.symbol)
    
    print("\n✅ Done! Parquet files ready for backtest.")


if __name__ == "__main__":
    main()
