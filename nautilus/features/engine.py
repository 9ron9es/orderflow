"""
Incremental orderflow feature engine built on tick fidelity.

Fixes applied vs original:
  - close_price now uses CandleFlow.close_price (last trade) not max_price (candle high)
  - Completed candles are cached — only the current incomplete bar recomputes on
    each evaluation. Previously all candles were rebuilt from scratch every 200 ms.
  - ob_imbalance is only applied to the current candle via populate_flows;
    historical cached candles are not retroactively contaminated.
  - divergence_window is forwarded to populate_flows.
  - [FIX B2 v2] cvd_rising now uses candle-to-candle CVD comparison, not a
    3-item EMA history sampled every 200ms. The old approach produced a 400ms
    slope on a 5m candle — pure noise that caused instant entries AND exits.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from typing import Literal

import sys
from pathlib import Path

_parent = Path(__file__).parent.parent.parent
if str(_parent) not in sys.path:
    sys.path.insert(0, str(_parent))

from orderflow_indicators import CandleFlow
from orderflow_indicators import compute_orderbook_imbalance
from orderflow_indicators import populate_flows
from orderflow_indicators import ticks_to_candle_flow


TF_MS: dict[str, int] = {
    "1m":  60_000,
    "3m":  180_000,
    "5m":  300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h":  3_600_000,
}


@dataclass(slots=True)
class OrderflowFeatureSnapshot:
    """Latest bar orderflow snapshot + CVD EMA slope context."""

    ts_ms: int
    flow: CandleFlow
    close_price: float
    cvd_ema: float
    cvd_rising: bool
    ob_imbalance: float


class OrderflowFeatureEngine:
    """
    Maintains a rolling tick buffer and computes candle flows incrementally.

    Design
    ------
    Closed candles are cached after their window ends and never rebuilt.
    Only the current incomplete bar is recomputed on each ``compute_snapshot``
    call, reducing tick iteration from O(all_lookback_ticks) to
    O(ticks_in_current_bar) per evaluation cycle.

    cvd_rising definition
    ---------------------
    Compare the running CVD at the end of the PREVIOUS completed candle to
    the running CVD of the CURRENT (partial) candle.  This is equivalent to
    asking "is this candle accumulating net buy pressure so far?" — a stable
    candle-level signal that does not flip every 200ms on noise.

    The old approach (3-item EMA deque at 200ms throttle) was comparing EMA
    values 400ms apart on a 5m candle — sensitive to single large sell ticks
    mid-candle, and guaranteed to flip immediately after entry.
    """

    def __init__(
        self,
        timeframe: str,
        lookback_candles: int = 50,
        price_bucket_size: float = 1.0,
        large_trade_pct: float = 0.90,
        cvd_smoothing: int = 5,
        divergence_window: int = 3,
    ) -> None:
        if timeframe not in TF_MS:
            raise ValueError(f"Unsupported timeframe {timeframe!r}; choose from {list(TF_MS)}")

        self._timeframe = timeframe          # FIX 8: expose for multi_tf routing
        self._tf_ms = TF_MS[timeframe]
        self._lookback_candles = lookback_candles
        self._lookback_ms = lookback_candles * self._tf_ms
        self._price_bucket_size = price_bucket_size
        self._large_trade_pct = large_trade_pct
        self._cvd_smoothing = cvd_smoothing
        self._divergence_window = divergence_window

        self._alpha = 2.0 / (cvd_smoothing + 1)

        self._ticks: deque[dict] = deque()
        self._completed_candles: deque[CandleFlow] = deque(maxlen=lookback_candles)
        self._current_candle_open_ms: int = 0

        # CVD EMA — used for the cvd_ema field in the snapshot (ML features etc.)
        # NOT used for cvd_rising any more.
        self._cvd_ema: float | None = None

        # Fallback EMA history for warmup (< 2 candles completed)
        self._prev_ema: float | None = None

        # Session CVD carry-over (FIX B1)
        self._session_cvd_base: float = 0.0

        # Live order book imbalance
        self._ob_imbalance: float = 0.0

        # Exposed for portfolio / metrics callers
        self._running_cvd: float = 0.0

    # ── Public properties ──────────────────────────────────────────────────────

    @property
    def running_cvd(self) -> float:
        return self._running_cvd

    # ── Order book imbalance setters ───────────────────────────────────────────

    def set_orderbook_imbalance(self, ob: dict | None, depth: int = 5) -> None:
        self._ob_imbalance = compute_orderbook_imbalance(ob, depth=depth)

    def set_orderbook_imbalance_value(self, value: float) -> None:
        self._ob_imbalance = float(value)

    # ── Tick ingestion ─────────────────────────────────────────────────────────

    def add_tick(
        self,
        ts_ms: int,
        price: float,
        qty: float,
        side: Literal["buy", "sell"],
    ) -> None:
        self._ticks.append({"ts": ts_ms, "price": price, "qty": qty, "side": side})
        self._trim_buffer(ts_ms)

        candle_open = (ts_ms // self._tf_ms) * self._tf_ms
        if candle_open > self._current_candle_open_ms and self._current_candle_open_ms > 0:
            self._seal_current_candle(candle_open)
        if self._current_candle_open_ms == 0:
            self._current_candle_open_ms = candle_open
        
        if len(self._ticks) <= 5 or len(self._ticks) % 10 == 0:
            msg = f"[ENGINE] add_tick #{len(self._ticks)}: price={price}, side={side}, candle_open={self._current_candle_open_ms}, completed={len(self._completed_candles)}"

    def update_from_wall_clock(self) -> None:
        now_ms = int(time.time() * 1000)
        self._trim_buffer(now_ms)
        candle_open = (now_ms // self._tf_ms) * self._tf_ms
        if candle_open > self._current_candle_open_ms and self._current_candle_open_ms > 0:
            self._seal_current_candle(candle_open)

    # ── Snapshot computation ───────────────────────────────────────────────────

    def compute_snapshot(self, now_ms: int | None = None) -> OrderflowFeatureSnapshot | None:
        
        if now_ms is None:
            now_ms = self._ticks[-1]["ts"] if self._ticks else int(time.time() * 1000)
        
        # DEBUG: Log every snapshot call
        msg = f"[SNAPSHOT] Called with ticks={len(self._ticks)}, completed_candles={len(self._completed_candles)}, now_ms={now_ms}"
        
        if not self._ticks and not self._completed_candles:
            return None

        # If we have ticks but NO completed candles yet (still in first candle period),
        # compute the CURRENT incomplete candle and return it anyway
        # This allows signals during candle formation, not just after candle close
        if self._ticks and not self._completed_candles:
            
            current_candle_open = (now_ms // self._tf_ms) * self._tf_ms
            current_candle_close = current_candle_open + self._tf_ms
            current_ticks = [t for t in self._ticks if t["ts"] >= current_candle_open]
            
            
            current_flow = ticks_to_candle_flow(
                current_ticks,
                current_candle_open,
                current_candle_close,
                price_bucket_size=self._price_bucket_size,
                large_trade_pct=self._large_trade_pct,
            )
            if current_flow is None:
                current_flow = self._zero_flow(current_candle_open)

            # Compute with just current candle (no history yet)
            all_flows = [current_flow]
            closes = [current_flow.close_price]
            
            populate_flows(
                all_flows,
                closes,
                running_cvd=self._session_cvd_base,
                ob_imbalance=self._ob_imbalance,
                divergence_window=self._divergence_window,
            )
            
            last = all_flows[-1]
            close_px = last.close_price
            cvd_ema_now = self._cvd_ema or last.cvd
            if self._cvd_ema is not None:
                cvd_ema_now = self._cvd_ema + self._alpha * (last.cvd - self._cvd_ema)
                self._cvd_ema = cvd_ema_now
            else:
                self._cvd_ema = last.cvd
            
            # Single candle - can't compute rising/falling, just say False
            cvd_rising = False
            
            snapshot = OrderflowFeatureSnapshot(
                ts_ms=int(last.open_ts),
                flow=last,
                close_price=close_px,
                cvd_ema=cvd_ema_now,
                cvd_rising=cvd_rising,
                ob_imbalance=self._ob_imbalance,
            )
            return snapshot

        current_candle_open = (now_ms // self._tf_ms) * self._tf_ms
        current_candle_close = current_candle_open + self._tf_ms

        current_ticks = [t for t in self._ticks if t["ts"] >= current_candle_open]
        current_flow = ticks_to_candle_flow(
            current_ticks,
            current_candle_open,
            current_candle_close,
            price_bucket_size=self._price_bucket_size,
            large_trade_pct=self._large_trade_pct,
        )
        if current_flow is None:
            current_flow = self._zero_flow(current_candle_open)

        all_flows: list[CandleFlow] = list(self._completed_candles) + [current_flow]
        if len(all_flows) > self._lookback_candles:
            all_flows = all_flows[-self._lookback_candles:]

        closes = [f.close_price for f in all_flows]

        populate_flows(
            all_flows,
            closes,
            running_cvd=self._session_cvd_base,
            ob_imbalance=self._ob_imbalance,
            divergence_window=self._divergence_window,
        )

        last = all_flows[-1]
        close_px = last.close_price
        self._running_cvd = last.cvd

        # ── CVD EMA (kept for ML feature row, not for cvd_rising) ────────────
        self._prev_ema = self._cvd_ema
        cvd_ema_now = self._ema_update(last.cvd)

        # ── cvd_rising: candle-to-candle comparison ──────────────────────────
        #
        # Compare cumulative CVD at end of previous completed candle vs now.
        # Because populate_flows accumulates CVD across all_flows with the
        # same session_cvd_base, all_flows[-2].cvd and all_flows[-1].cvd are
        # on the same scale — the difference IS the current candle's net delta.
        #
        # This only flips when the current candle genuinely accumulates net
        # selling, not when a single large tick hits during a 200ms window.
        #
        if len(all_flows) >= 2:
            cvd_rising = all_flows[-1].cvd > all_flows[-2].cvd
        else:
            # Warmup: fewer than 2 candles — fall back to EMA diff
            cvd_rising = (
                self._prev_ema is not None and cvd_ema_now > self._prev_ema
            )

        return OrderflowFeatureSnapshot(
            ts_ms=int(last.open_ts),
            flow=last,
            close_price=close_px,
            cvd_ema=cvd_ema_now,
            cvd_rising=cvd_rising,
            ob_imbalance=self._ob_imbalance,
        )

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _seal_current_candle(self, new_candle_open_ms: int) -> None:
        if self._current_candle_open_ms == 0:
            self._current_candle_open_ms = new_candle_open_ms
            return

        candle_close = self._current_candle_open_ms + self._tf_ms
        closed_ticks = [
            t for t in self._ticks
            if self._current_candle_open_ms <= t["ts"] < candle_close
        ]
        flow = ticks_to_candle_flow(
            closed_ticks,
            self._current_candle_open_ms,
            candle_close,
            price_bucket_size=self._price_bucket_size,
            large_trade_pct=self._large_trade_pct,
        )
        if flow is not None:
            self._completed_candles.append(flow)
            self._session_cvd_base += flow.delta  # FIX B1: carry CVD across candles

        self._current_candle_open_ms = new_candle_open_ms

    def _trim_buffer(self, reference_ts_ms: int) -> None:
        cutoff = reference_ts_ms - self._lookback_ms - self._tf_ms
        while self._ticks and self._ticks[0]["ts"] < cutoff:
            self._ticks.popleft()

    def _zero_flow(self, open_ts: int) -> CandleFlow:
        return CandleFlow(
            open_ts=open_ts,
            close_ts=open_ts + self._tf_ms,
        )

    def _ema_update(self, cvd: float) -> float:
        if self._cvd_ema is None:
            self._cvd_ema = cvd
        else:
            self._cvd_ema = self._alpha * cvd + (1.0 - self._alpha) * self._cvd_ema
        return self._cvd_ema

    def reset(self) -> None:
        self._ticks.clear()
        self._completed_candles.clear()
        self._current_candle_open_ms = 0
        self._running_cvd = 0.0
        self._cvd_ema = None
        self._prev_ema = None
        self._session_cvd_base = 0.0
        self._ob_imbalance = 0.0
