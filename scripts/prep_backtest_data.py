#!/usr/bin/env python3
"""
Convert Binance aggTrades ZIP → Parquet (for backtest engine).

Usage:
    python scripts/prep_backtest_data.py BTCUSDT-aggTrades-2026-01.zip

Optimized for large files: streams data instead of loading all into memory.
"""

import zipfile
from pathlib import Path
from datetime import datetime
import pandas as pd
from collections import defaultdict


# Binance aggTrades columns (no header in ZIP)
AGGTRADE_COLS = [
    "agg_trade_id", "price", "qty",
    "first_trade_id", "last_trade_id",
    "transact_time_ns", "is_maker", "is_buyer_maker",
]

CHUNK_SIZE = 100_000  # Process in chunks to avoid memory explosion


def read_and_convert_aggtrades_zip(zip_path: Path, output_dir: Path) -> int:
    """Stream read aggTrades CSV, convert to daily Parquet files."""
    print(f"Reading {zip_path.name}...")
    
    with zipfile.ZipFile(zip_path) as zf:
        csv_name = [n for n in zf.namelist() if n.endswith(".csv")][0]
        print(f"  CSV: {csv_name}")
        
        with zf.open(csv_name) as f:
            # Read in chunks
            chunks_read = 0
            total_rows = 0
            daily_buffers = defaultdict(list)
            
            for chunk in pd.read_csv(
                f, 
                header=None, 
                names=AGGTRADE_COLS,
                chunksize=CHUNK_SIZE,
                dtype={
                    "agg_trade_id": int,
                    "price": float,
                    "qty": float,
                    "is_buyer_maker": bool,
                }
            ):
                chunks_read += 1
                
                # Convert timestamp (ns → datetime)
                chunk["transact_time"] = pd.to_datetime(
                    chunk["transact_time_ns"], 
                    unit="ns", 
                    utc=True
                )
                
                # Extract date and group
                chunk["date_str"] = chunk["transact_time"].dt.strftime("%Y-%m-%d")
                
                # Build rows for each day
                for _, row in chunk.iterrows():
                    date_str = row["date_str"]
                    aggressor = "SELL" if row["is_buyer_maker"] else "BUY"
                    
                    daily_buffers[date_str].append({
                        "ts": int(row["transact_time_ns"]),
                        "price": row["price"],
                        "qty": row["qty"],
                        "side": aggressor,
                        "agg_id": str(row["agg_trade_id"]),
                    })
                
                total_rows += len(chunk)
                
                # Flush to disk every N chunks (not current day) to free memory
                if chunks_read % 50 == 0:
                    dates_to_flush = [d for d in daily_buffers.keys() 
                                     if d != chunk["date_str"].iloc[-1]]
                    for date_str in dates_to_flush:
                        df_day = pd.DataFrame(daily_buffers[date_str])
                        df_day = df_day.sort_values("ts")
                        
                        pq_path = output_dir / f"{date_str}.parquet"
                        df_day.to_parquet(pq_path, index=False, compression="snappy")
                        
                        print(f"  Flushed {pq_path.name}: {len(df_day):,} ticks")
                        del daily_buffers[date_str]
                
                if chunks_read % 10 == 0:
                    print(f"  Processed {total_rows:,} rows ({chunks_read} chunks)...")
            
            # Final flush
            for date_str, rows in sorted(daily_buffers.items()):
                if rows:
                    df_day = pd.DataFrame(rows)
                    df_day = df_day.sort_values("ts")
                    
                    pq_path = output_dir / f"{date_str}.parquet"
                    pq_path.parent.mkdir(parents=True, exist_ok=True)
                    df_day.to_parquet(pq_path, index=False, compression="snappy")
                    
                    print(f"  Flushed {pq_path.name}: {len(df_day):,} ticks")
    
    return total_rows


def main():
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python scripts/prep_backtest_data.py <zip_file>")
        sys.exit(1)
    
    zip_file = Path(sys.argv[1])
    if not zip_file.exists():
        print(f"Error: {zip_file} not found")
        sys.exit(1)
    
    output_dir = Path("ticks/BTCUSDT")
    output_dir.mkdir(parents=True, exist_ok=True)
    
    print(f"\nConverting → {output_dir}/...")
    total = read_and_convert_aggtrades_zip(zip_file, output_dir)
    
    # Verify output
    parquet_files = list(output_dir.glob("*.parquet"))
    print(f"\n✓ Conversion complete:")
    print(f"  Total ticks: {total:,}")
    print(f"  Daily files: {len(parquet_files)}")
    if parquet_files:
        print(f"  Date range: {min(f.stem for f in parquet_files)} → {max(f.stem for f in parquet_files)}")


if __name__ == "__main__":
    main()
