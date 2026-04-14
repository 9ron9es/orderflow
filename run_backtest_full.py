#!/usr/bin/env python3
"""
Backtest runner for FULL dataset (data_full.parquet).
Shows signal stats and performance metrics.
"""

from pathlib import Path
import sys
from nautilus.runners.backtest import run_backtest

if __name__ == "__main__":
    config_path = Path(__file__).parent / "nautilus/config/profiles/backtest.yaml"
    data_file = Path(__file__).parent / "data_full.parquet"
    
    # Fall back to small dataset if full not available
    if not data_file.exists():
        data_file = Path(__file__).parent / "data.parquet"
        print(f"[BACKTEST] data_full.parquet not found, using data.parquet ({data_file.stat().st_size / 1024**2:.1f} MB)")
    else:
        size_mb = data_file.stat().st_size / (1024**2)
        print(f"[BACKTEST] FULL BACKTEST")
        print(f"[BACKTEST] Data: {data_file.name} ({size_mb:.1f} MB)")

    print(f"[BACKTEST] Config: {config_path}")
    print(f"[BACKTEST] ---")

    try:
        run_backtest(config_path=config_path, parquet_path=data_file)
    except Exception as e:
        print(f"[ERROR] Backtest failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
