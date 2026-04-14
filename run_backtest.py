#!/usr/bin/env python3
"""
Quick backtest runner — no arguments needed, uses defaults.
"""

from pathlib import Path
from nautilus.runners.backtest import run_backtest

if __name__ == "__main__":
    config_path = Path(__file__).parent / "nautilus/config/profiles/backtest.yaml"
    parquet_path = Path(__file__).parent / "data.parquet"

    print(f"[BACKTEST] Starting with config: {config_path}")
    print(f"[BACKTEST] Data file: {parquet_path}")
    print(f"[BACKTEST] ---")

    try:
        run_backtest(config_path=config_path, parquet_path=parquet_path)
    except Exception as e:
        print(f"[ERROR] Backtest failed: {e}")
        import traceback
        traceback.print_exc()
