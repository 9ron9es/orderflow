"""
Convert recorded tick Parquet (legacy Redis recorder format) into Nautilus ``TradeTick``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import AggressorSide
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.persistence.wranglers import TradeTickDataWrangler


def trade_tick_to_side_dict(tick: TradeTick) -> dict[str, Any] | None:
    """Map Nautilus trade tick to orderflow engine tick dict."""
    ts_ms = int(tick.ts_event / 1_000_000)
    price = float(tick.price)
    qty = float(tick.size)
    
    if tick.aggressor_side == AggressorSide.BUYER:
        side = "BUY"
    elif tick.aggressor_side == AggressorSide.SELLER:
        side = "SELL"
    else:
        # Fallback: infer from previous price or use default
        # For now, default to BUY if aggressor side not set (better than rejecting)
        side = "BUY"
    
    return {"ts": ts_ms, "price": price, "qty": qty, "side": side}


def parquet_ticks_to_trade_ticks(
    parquet_path: str | Path,
    instrument: Instrument,
    *,
    ts_init_delta: int = 0,
) -> list:
    """
    Load ``tick_recorder.py`` Parquet (columns: ts, price, qty, side, agg_id).

    Returns
    -------
    list[TradeTick]
    """
    path = Path(parquet_path)
    df = pd.read_parquet(path)
    if df.empty:
        return []

    df = df.sort_values("ts")
    # Map side string -> BUY/SELL for wrangler
    def _side_row(r) -> str:
        s = str(r["side"]).lower()
        if s in ("buy", "b", "true"):
            return "BUY"
        if s in ("sell", "s", "false"):
            return "SELL"
        return "BUY"

    df["side"] = df.apply(_side_row, axis=1)
    df["trade_id"] = df["agg_id"].astype(str)
    
    # Scale quantity by 1M to preserve decimal precision (BTC has small decimals)
    # E.g., 0.00001 BTC -> 10 (units)
    df["quantity"] = (df["qty"].astype(float) * 1_000_000).astype("int64")
    df["quantity"] = df["quantity"].clip(lower=1)  # Ensure minimum 1
    
    df["price"] = df["price"].astype(float)
    df["timestamp"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    
    # Filter out duplicates
    df = df.drop_duplicates(subset=["timestamp", "trade_id"]).copy()
    print(f"  Loaded {len(df):,} unique ticks")
    
    df = df.set_index("timestamp")

    wrangler = TradeTickDataWrangler(instrument=instrument)
    return wrangler.process(df, ts_init_delta=ts_init_delta)
