"""
signals/long/imbalance_continuation.py

FIX: structure.trend is a TrendDirection enum, not a string.
     Comparing TrendDirection.BEARISH != "bearish" is ALWAYS True,
     meaning the HTF trend filter never blocked any long entry.
     Fixed to use .value for string comparison.
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
        absorption_min: float = 0.15,
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

    def evaluate(self, snap, structure, session) -> EntrySignal | None:
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
            "cvd_rising":         ltf.cvd_rising,
            "imbalance":          of.imbalance >= self._imb_threshold,
            "no_sell_absorption": of.absorption >= -self._absorption_min,
            "stacked_imb":        of.stacked_imb >= self._stack_min,
            "ob_imbalance":       of.ob_imbalance >= self._ob_imb_min,
            "large_dom":          large_dom >= self._large_dom_min,
            "no_delta_div":       of.delta_div != 1.0,
            # FIX: was `structure.trend != "bearish"` — enum vs string is always True.
            # structure.trend is TrendDirection.BEARISH, not the string "bearish".
            "htf_not_bearish": (
                not self._require_htf_align
                or structure.trend.value != "bearish"
            ),
        }

        return self._make_signal(conditions)
