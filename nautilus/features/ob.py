"""Order book imbalance from Nautilus ``OrderBook``."""

from __future__ import annotations

from nautilus_trader.model.book import OrderBook


def orderbook_to_imbalance(book: OrderBook | None, depth: int = 5) -> float:
    """Top-``depth`` volume imbalance in [-1, 1], matching legacy OB gate."""
    if book is None:
        return 0.0
    bid_vol = sum(level.size() for level in list(book.bids())[:depth])
    ask_vol = sum(level.size() for level in list(book.asks())[:depth])
    total = bid_vol + ask_vol
    if total <= 0:
        return 0.0
    return (bid_vol - ask_vol) / total
