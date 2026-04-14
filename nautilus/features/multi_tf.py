"""
Multi-timeframe orderflow engine combining low-timeframe (LTF) signals with
high-timeframe (HTF) trend context.

Design
------
- LTF engine: fast signal detection (1m, 3m, 5m)
- HTF engine: regime / market structure (1h, 4h)
- Combined snapshot: (ltf_candles, htf_candles, cvd_ema, structure)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from nautilus.features.engine import (
    OrderflowFeatureEngine,
    OrderflowFeatureSnapshot,
)


@dataclass(slots=True)
class MultiTFSnapshot:
    """Combined LTF + HTF snapshot for signal evaluation."""

    ts_ms: int                          # Evaluation timestamp
    ltf: OrderflowFeatureSnapshot       # Low-timeframe candle + CVD EMA
    htf: OrderflowFeatureSnapshot | None  # High-timeframe candle (nullable)


class MultiTFEngine:
    """
    Maintains separate OrderflowFeatureEngine instances for LTF and HTF,
    synchronizing ticks and evaluation across both.

    Parameters
    ----------
    ltf : str
        Low-timeframe identifier ("1m", "3m", "5m", "15m", etc.)
    htf : str
        High-timeframe identifier ("1h", "4h", "1d", etc.)
    lookback_candles : int
        Lookback window for both engines.
    price_bucket_size : float
        Price bucketing for large-trade detection.
    large_trade_pct : float
        Percentile threshold (0.0–1.0) for large trades.
    cvd_smoothing : int
        EMA smoothing window for CVD slope detection.
    divergence_window : int
        Bars back for divergence detection (delta vs price).
    """

    def __init__(
        self,
        ltf: str,
        htf: str,
        lookback_candles: int = 50,
        price_bucket_size: float = 1.0,
        large_trade_pct: float = 0.90,
        cvd_smoothing: int = 5,
        divergence_window: int = 3,
    ) -> None:
        self._ltf_engine = OrderflowFeatureEngine(
            timeframe=ltf,
            lookback_candles=lookback_candles,
            price_bucket_size=price_bucket_size,
            large_trade_pct=large_trade_pct,
            cvd_smoothing=cvd_smoothing,
            divergence_window=divergence_window,
        )

        self._htf_engine = OrderflowFeatureEngine(
            timeframe=htf,
            lookback_candles=lookback_candles,
            price_bucket_size=price_bucket_size,
            large_trade_pct=large_trade_pct,
            cvd_smoothing=cvd_smoothing,
            divergence_window=divergence_window,
        )

    # ── Tick ingestion (synced across both) ─────────────────────────────────

    def add_tick(
        self,
        ts_ms: int,
        price: float,
        qty: float,
        side: Literal["buy", "sell"],
    ) -> None:
        """Ingest a tick into both LTF and HTF engines."""
        self._ltf_engine.add_tick(ts_ms, price, qty, side)
        self._htf_engine.add_tick(ts_ms, price, qty, side)

    # ── Order book imbalance (synced) ──────────────────────────────────────

    def set_orderbook_imbalance_value(self, value: float) -> None:
        """Set order book imbalance on both engines."""
        self._ltf_engine.set_orderbook_imbalance_value(value)
        self._htf_engine.set_orderbook_imbalance_value(value)

    def update_from_wall_clock(self) -> None:
        """Update both engines from wall clock to seal any completed candles."""
        self._ltf_engine.update_from_wall_clock()
        self._htf_engine.update_from_wall_clock()

    # ── Snapshot computation ───────────────────────────────────────────────

    def compute_snapshot(
        self, now_ms: int | None = None
    ) -> MultiTFSnapshot | None:
        """
        Compute combined LTF + HTF snapshot.

        Returns None if either engine has insufficient data.
        """
        ltf_snap = self._ltf_engine.compute_snapshot(now_ms)
        if ltf_snap is None:
            return None

        htf_snap = self._htf_engine.compute_snapshot(now_ms)
        # HTF can be None if insufficient candles, but we still return LTF valid

        return MultiTFSnapshot(
            ts_ms=now_ms or ltf_snap.ts_ms,
            ltf=ltf_snap,
            htf=htf_snap,
        )

    # ── Direct engine access (for advanced use) ────────────────────────────

    @property
    def ltf_engine(self) -> OrderflowFeatureEngine:
        """Access LTF engine directly."""
        return self._ltf_engine

    @property
    def htf_engine(self) -> OrderflowFeatureEngine:
        """Access HTF engine directly."""
        return self._htf_engine

    def completed_candles(self, timeframe: str) -> list | None:
        """
        Get completed candles from the requested timeframe engine.

        Parameters
        ----------
        timeframe : str
            Pass the HTF timeframe string (e.g. "1h", "4h") to get HTF candles,
            or the LTF timeframe string (e.g. "5m") to get LTF candles.
            Also accepts the aliases "htf" and "ltf".
        """
        # FIX 8: old code used a hardcoded list ("htf","h","1h","4h","1d") which
        # silently returned LTF candles for any other HTF timeframe (e.g. "2h","30m").
        # Now we compare directly against the configured engine timeframe strings.
        htf_tf = self._htf_engine._timeframe if hasattr(self._htf_engine, '_timeframe') else None
        ltf_tf = self._ltf_engine._timeframe if hasattr(self._ltf_engine, '_timeframe') else None

        t = timeframe.lower()
        is_htf = (
            t == "htf"
            or (htf_tf is not None and t == htf_tf.lower())
            or (ltf_tf is not None and t != ltf_tf.lower() and t not in ("ltf",))
        )

        if t == "ltf" or (ltf_tf is not None and t == ltf_tf.lower()):
            return list(self._ltf_engine._completed_candles) if self._ltf_engine._completed_candles else None

        # Default: treat unrecognised strings as HTF request
        return list(self._htf_engine._completed_candles) if self._htf_engine._completed_candles else None
