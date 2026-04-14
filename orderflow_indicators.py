"""
Orderflow indicators: candle flows, CVD, imbalance, absorption, delta divergence.

Fixes applied vs original:
  - CandleFlow gains close_price field (was incorrectly using max_price as close)
  - large trade threshold now uses all quantities, not unique set
  - ob_imbalance in populate_flows only applied to current (last) candle
  - absorption redefined as directional: (large_buy - large_sell) / total_vol
    range [-1, +1]; positive = net large buying, negative = net large selling
  - delta_divergence uses configurable swing window, not just adjacent bars
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from dataclasses import field
from typing import Optional

import numpy as np


# ─── CandleFlow ────────────────────────────────────────────────────────────────

@dataclass(slots=True)
class CandleFlow:
    """Orderflow metrics for a single candle."""

    open_ts: int
    close_ts: int
    buy_vol: float = 0.0
    sell_vol: float = 0.0
    delta: float = 0.0
    total_vol: float = 0.0
    buy_trades: int = 0
    sell_trades: int = 0
    vwap: float = 0.0
    large_buy_vol: float = 0.0
    large_sell_vol: float = 0.0
    max_price: float = 0.0
    min_price: float = 0.0
    close_price: float = 0.0          # last traded price in candle (was missing)

    # Derived — populated by populate_flows()
    cvd: float = field(default=0.0, init=False)
    ask_vol: float = field(default=0.0, init=False)
    bid_vol: float = field(default=0.0, init=False)
    ob_imbalance: float = field(default=0.0, init=False)
    imbalance: float = field(default=0.0, init=False)

    # FIX: absorption = (large_buy_vol - large_sell_vol) / total_vol
    #      Range [-1, +1]; positive = net large buying pressure (bullish absorption)
    #      negative = net large selling pressure (sell absorption / bearish)
    #      Previously was: (large_buy + large_sell) / total — always ≥ 0, no direction
    absorption: float = field(default=0.0, init=False)

    stacked_imb: float = field(default=0.0, init=False)

    # delta_div: 1.0 = bearish divergence (price up, delta negative)
    #           -1.0 = bullish divergence (price down, delta positive)
    #            0.0 = no divergence
    delta_div: float = field(default=0.0, init=False)


# ─── Candle construction ───────────────────────────────────────────────────────

def ticks_to_candle_flow(
    ticks: list[dict],
    candle_open_ts: int,
    candle_close_ts: int,
    price_bucket_size: float = 1.0,
    large_trade_pct: float = 0.90,
) -> Optional[CandleFlow]:
    """
    Compute candle orderflow from a tick list.

    Parameters
    ----------
    ticks : list[dict]
        Each tick: {ts: int (ms), price: float, qty: float, side: 'buy'|'sell'}
    candle_open_ts : int
        Inclusive candle start (ms).
    candle_close_ts : int
        Exclusive candle end (ms).
    price_bucket_size : float
        Price bucketing granularity for footprint (unused in derived metrics but
        kept for future footprint per-level breakdown).
    large_trade_pct : float
        Percentile threshold for classifying a trade as "large" (e.g. 0.90 = top 10%).

    Returns
    -------
    CandleFlow | None
        None when no ticks fall in the window.
    """
    candle_ticks = [t for t in ticks if candle_open_ts <= t["ts"] < candle_close_ts]
    if not candle_ticks:
        return None

    buy_vol  = sum(t["qty"] for t in candle_ticks if t["side"].upper() == "BUY")
    sell_vol = sum(t["qty"] for t in candle_ticks if t["side"].upper() == "SELL")
    total_vol = buy_vol + sell_vol
    delta = buy_vol - sell_vol

    prices = [t["price"] for t in candle_ticks]
    max_price   = max(prices)
    min_price   = min(prices)
    close_price = candle_ticks[-1]["price"]   # FIX: last trade price, not max

    vwap = (
        sum(t["price"] * t["qty"] for t in candle_ticks) / total_vol
        if total_vol > 0
        else close_price
    )
    # FIX: percentile over ALL quantities, not unique set.
    # Using set() previously collapsed 1000 identical small trades into one
    # value, badly distorting the threshold.
    all_qtys = [t["qty"] for t in candle_ticks]
    large_threshold = float(np.percentile(all_qtys, large_trade_pct * 100))

    # FIX (B4): Use `>` not `>=` to avoid edge case where all trades have same qty
    # (thin markets). If qty == percentile, it's exactly at the threshold,
    # not strictly above it. This prevents 100% volume being classified as "large"
    # when the market has uniform tick sizes.
    large_buy_vol  = sum(t["qty"] for t in candle_ticks if t["side"].upper() == "BUY"  and t["qty"] > large_threshold)
    large_sell_vol = sum(t["qty"] for t in candle_ticks if t["side"].upper() == "SELL" and t["qty"] > large_threshold)

    buy_trades  = sum(1 for t in candle_ticks if t["side"].upper() == "BUY")
    sell_trades = sum(1 for t in candle_ticks if t["side"].upper() == "SELL")

    return CandleFlow(
        open_ts=candle_open_ts,
        close_ts=candle_close_ts,
        buy_vol=buy_vol,
        sell_vol=sell_vol,
        delta=delta,
        total_vol=total_vol,
        buy_trades=buy_trades,
        sell_trades=sell_trades,
        vwap=vwap,
        large_buy_vol=large_buy_vol,
        large_sell_vol=large_sell_vol,
        max_price=max_price,
        min_price=min_price,
        close_price=close_price,
    )


# ─── Derived metric helpers ────────────────────────────────────────────────────

def compute_cvd(flows: list[CandleFlow]) -> list[float]:
    """Cumulative Volume Delta across a list of candle flows."""
    cvd: list[float] = []
    running = 0.0
    for f in flows:
        running += f.delta
        cvd.append(running)
    return cvd


def compute_volume_imbalance(flows: list[CandleFlow]) -> list[float]:
    """Per-candle directional volume imbalance: (buy - sell) / total ∈ [-1, 1]."""
    return [
        (f.buy_vol - f.sell_vol) / f.total_vol if f.total_vol > 0 else 0.0
        for f in flows
    ]


def compute_absorption(flows: list[CandleFlow]) -> list[float]:
    """
    Directional large-trade absorption: (large_buy - large_sell) / total_vol.

    Range [-1, +1].
    Positive → large buyers dominating (bullish, buy-side absorption).
    Negative → large sellers dominating (bearish, sell-side absorption).
    """
    result = []
    for f in flows:
        if f.total_vol > 1e-9:
            result.append((f.large_buy_vol - f.large_sell_vol) / f.total_vol)
        else:
            result.append(0.0)
    return result


def compute_stacked_imbalance(flows: list[CandleFlow], window: int = 3) -> list[float]:
    """Consecutive directional candle count (positive = bullish run, negative = bearish)."""
    imb = compute_volume_imbalance(flows)
    stacked = []
    for i, f in enumerate(flows):
        if i == 0:
            stacked.append(1.0 if imb[i] > 0 else (-1.0 if imb[i] < 0 else 0.0))
            continue
        prev = stacked[i - 1]
        if imb[i] > 0 and prev > 0:
            stacked.append(prev + 1.0)
        elif imb[i] < 0 and prev < 0:
            stacked.append(prev - 1.0)
        else:
            stacked.append(1.0 if imb[i] > 0 else (-1.0 if imb[i] < 0 else 0.0))
    return stacked


def compute_vwap_deviation(flows: list[CandleFlow], closes: list[float]) -> list[float]:
    """Close deviation from candle VWAP (%)."""
    return [
        (closes[i] - f.vwap) / f.vwap * 100 if f.vwap > 0 else 0.0
        for i, f in enumerate(flows)
    ]


def compute_delta_divergence(
    flows: list[CandleFlow],
    closes: list[float],
    window: int = 3,
) -> list[float]:
    """
    Swing-based delta divergence over ``window`` bars.

    FIX: previously compared only adjacent bars (i vs i-1), which fired on any
    single green bar with negative delta. Now compares bar i against bar i-window,
    requiring a multi-bar swing to confirm divergence.

    Returns
    -------
    list[float]
        1.0  = bearish divergence (price swing high, delta swing low)
       -1.0  = bullish divergence (price swing low, delta swing high)
        0.0  = no divergence
    """
    n = len(flows)
    divergence = [0.0] * n
    for i in range(window, n):
        price_change = closes[i] - closes[i - window]
        delta_change = flows[i].delta - flows[i - window].delta
        if price_change > 0 and delta_change < 0:
            divergence[i] = 1.0    # bearish: price up, momentum down
        elif price_change < 0 and delta_change > 0:
            divergence[i] = -1.0  # bullish: price down, momentum up
    return divergence


def compute_orderbook_imbalance(ob: dict | None, depth: int = 5) -> float:
    """
    Order book volume imbalance: (bid_qty - ask_qty) / total ∈ [-1, 1].

    Parameters
    ----------
    ob : dict | None
        {'bids': [[price, qty], ...], 'asks': [[price, qty], ...]}
    depth : int
        Number of levels to include from each side.
    """
    if ob is None:
        return 0.0
    bid_qty = sum(float(b[1]) for b in ob.get("bids", [])[:depth])
    ask_qty = sum(float(a[1]) for a in ob.get("asks", [])[:depth])
    total = bid_qty + ask_qty
    return (bid_qty - ask_qty) / total if total > 0 else 0.0


# ─── populate_flows ────────────────────────────────────────────────────────────

def populate_flows(
    flows: list[CandleFlow],
    closes: list[float],
    running_cvd: float = 0.0,
    ob_imbalance: float = 0.0,
    divergence_window: int = 3,
) -> None:
    """
    Mutate flows in-place with all derived metrics.

    Parameters
    ----------
    flows : list[CandleFlow]
        Candle flows (modified in place).
    closes : list[float]
        Close price per candle (must align with flows).
    running_cvd : float
        CVD starting value (carry-over from previous session).
    ob_imbalance : float
        Current live order book imbalance — applied ONLY to the last candle.
        FIX: previously stamped on all candles, causing a data leak where
        historical candles received the current book state.
    divergence_window : int
        Swing window for delta divergence (see compute_delta_divergence).
    """
    if not flows:
        return

    # ── CVD (running across all candles) ───────────────────────────────────
    running = running_cvd
    for f in flows:
        running += f.delta
        f.cvd = running
        f.bid_vol = f.buy_vol
        f.ask_vol = f.sell_vol

    # FIX: ob_imbalance is a live snapshot — only meaningful on the current
    # (last) candle. Historical candles keep their default 0.0.
    flows[-1].ob_imbalance = ob_imbalance

    # ── Per-candle metrics ─────────────────────────────────────────────────
    for i, f in enumerate(flows):
        # Volume imbalance
        f.imbalance = (
            (f.buy_vol - f.sell_vol) / f.total_vol if f.total_vol > 0 else 0.0
        )

        # Absorption: directional large-trade pressure, range [-1, +1]
        # FIX: was (large_buy + large_sell) / total → always ≥ 0, no direction
        f.absorption = (
            (f.large_buy_vol - f.large_sell_vol) / f.total_vol
            if f.total_vol > 1e-9
            else 0.0
        )

        # Stacked imbalance: consecutive directional candle run
        if i == 0:
            f.stacked_imb = 1.0 if f.imbalance > 0 else (-1.0 if f.imbalance < 0 else 0.0)
        else:
            prev = flows[i - 1]
            if f.imbalance > 0 and prev.stacked_imb > 0:
                f.stacked_imb = prev.stacked_imb + 1.0
            elif f.imbalance < 0 and prev.stacked_imb < 0:
                f.stacked_imb = prev.stacked_imb - 1.0
            else:
                f.stacked_imb = 1.0 if f.imbalance > 0 else (-1.0 if f.imbalance < 0 else 0.0)

    # ── Delta divergence (swing-based, needs full flow list) ───────────────
    divergence = compute_delta_divergence(flows, closes, window=divergence_window)
    for f, div in zip(flows, divergence):
        f.delta_div = div