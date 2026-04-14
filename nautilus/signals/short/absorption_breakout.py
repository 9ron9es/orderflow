"""
signals/short/absorption_breakout.py

FIX: structure.trend is a TrendDirection enum, not a string.
     Comparing TrendDirection.BULLISH != "bullish" is ALWAYS True,
     meaning the HTF trend filter never blocked any short entry.
     Fixed to use .value for string comparison.

Also removed: `htf = snap.htf` was assigned but never used.
"""

from __future__ import annotations

from nautilus_trader.model.enums import OrderSide

from nautilus.signals.base import EntrySignal, SignalModule


class AbsorptionBreakoutShort(SignalModule):

    label = "absorption_breakout_short"
    side  = OrderSide.SELL

    def __init__(
        self,
        absorption_min: float = 0.10,
        large_dom_min: float = 0.15,
        ob_imb_max: float = -0.10,
        require_htf_align: bool = True,
        **_: object,
    ) -> None:
        self._absorption_min = absorption_min
        self._large_dom_min  = large_dom_min
        self._ob_imb_max     = ob_imb_max
        self._require_htf_align = require_htf_align

    def evaluate(self, snap, structure, session, vp=None) -> EntrySignal | None:
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
            "large_dom_bearish": large_dom <= -self._large_dom_min,
            "sell_absorption":   of.absorption <= -self._absorption_min,
            "cvd_falling":       not ltf.cvd_rising,
            "ob_ask_heavy":      of.ob_imbalance <= self._ob_imb_max,
            "no_bullish_div":    of.delta_div != -1.0,
            # FIX: was `structure.trend != "bullish"` — enum vs string is always True.
            "htf_not_bullish": (
                not self._require_htf_align
                or structure.trend.value != "bullish"
            ),
        }

        return self._make_signal(conditions)
