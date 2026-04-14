"""
signals/long/imbalance_continuation.py

Long entry: sustained buy-side imbalance + stacked bars + OB support.
This is the "original" strategy logic refactored as a pluggable module
with corrected thresholds and short-aware structure.

Pattern definition
------------------
1. Strong per-bar buy imbalance (buy >> sell this candle).
2. Multiple consecutive bullish-imbalance bars (stacked_imb ≥ N).
3. CVD EMA rising (cumulative buying pressure building).
4. Order book bid-heavy (institutional support visible in L2).
5. No significant sell-side absorption (large sellers not defending level).
6. No bearish delta divergence.
7. HTF trend is bullish or ranging (not hard bearish).
"""

from __future__ import annotations

from nautilus_trader.model.enums import OrderSide

from orderflow.nautilus.signals.base import EntrySignal, SignalModule


class ImbalanceContinuationLong(SignalModule):

    label = "imbalance_continuation_long"
    side  = OrderSide.BUY

    def __init__(
        self,
        imbalance_threshold: float = 0.25,
        absorption_min: float = 0.15,         # gate: absorption >= -absorption_min
        stack_min_rows: int = 3,
        ob_imb_threshold: float = 0.15,
        large_dom_min: float = 0.10,
        require_htf_align: bool = True,
        **_: object,
    ) -> None:
        self._imb_threshold   = imbalance_threshold
        self._absorption_min  = absorption_min
        self._stack_min       = stack_min_rows
        self._ob_imb_min      = ob_imb_threshold
        self._large_dom_min   = large_dom_min
        self._require_htf_align = require_htf_align

    def evaluate(self, snap, structure, session) -> EntrySignal | None:  # type: ignore[override]
        if not session.active:
            return None

        ltf = snap.ltf
        of  = ltf.flow

        large_sum = of.large_buy_vol + of.large_sell_vol
        large_dom = (
            (of.large_buy_vol - of.large_sell_vol) / large_sum
            if large_sum > 1e-9
            else 0.0
        )

        conditions: dict[str, bool] = {
            "cvd_rising":        ltf.cvd_rising,
            "imbalance":         of.imbalance >= self._imb_threshold,
            # Block if large sellers are absorbing (sell-side dominant)
            "no_sell_absorption": of.absorption >= -self._absorption_min,
            "stacked_imb":       of.stacked_imb >= self._stack_min,
            "ob_imbalance":      of.ob_imbalance >= self._ob_imb_min,
            "large_dom":         large_dom >= self._large_dom_min,
            "no_delta_div":      of.delta_div != 1.0,
            "htf_not_bearish":   (not self._require_htf_align) or (structure.trend != "bearish"),
        }

        return self._make_signal(conditions)