"""
signals/short/absorption_breakout.py

Short entry: institutional sellers absorb bids at resistance, then price breaks down.

Symmetric inverse of AbsorptionBreakoutLong. Key differences:
- large sellers dominate (large_dom ≤ -threshold)
- absorption is negative (large_sell >> large_buy)
- CVD EMA falling
- OB ask-heavy
- Bearish HTF required if require_htf_align=True
"""

from __future__ import annotations

from nautilus_trader.model.enums import OrderSide

from orderflow.nautilus.signals.base import EntrySignal, SignalModule


class AbsorptionBreakoutShort(SignalModule):

    label = "absorption_breakout_short"
    side  = OrderSide.SELL

    def __init__(
        self,
        absorption_min: float = 0.10,    # gate: absorption <= -absorption_min
        large_dom_min: float = 0.15,     # gate: large_dom <= -large_dom_min
        ob_imb_max: float = -0.10,       # gate: ob_imbalance <= ob_imb_max (ask-heavy)
        require_htf_align: bool = True,
        **_: object,
    ) -> None:
        self._absorption_min = absorption_min
        self._large_dom_min  = large_dom_min
        self._ob_imb_max     = ob_imb_max
        self._require_htf_align = require_htf_align

    def evaluate(self, snap, structure, session) -> EntrySignal | None:  # type: ignore[override]
        if not session.active:
            return None

        ltf = snap.ltf
        htf = snap.htf
        of  = ltf.flow

        large_sum = of.large_buy_vol + of.large_sell_vol
        large_dom = (
            (of.large_buy_vol - of.large_sell_vol) / large_sum
            if large_sum > 1e-9
            else 0.0
        )

        conditions: dict[str, bool] = {
            # Sellers dominate large trades
            "large_dom_bearish":    large_dom <= -self._large_dom_min,
            # Large sellers absorb bids at resistance
            "sell_absorption":      of.absorption <= -self._absorption_min,
            # Macro momentum falling
            "cvd_falling":          not ltf.cvd_rising,
            # Order book ask-heavy
            "ob_ask_heavy":         of.ob_imbalance <= self._ob_imb_max,
            # No bullish divergence (would invalidate short)
            "no_bullish_div":       of.delta_div != -1.0,
            # HTF alignment
            "htf_not_bullish":      (not self._require_htf_align) or (structure.trend != "bullish"),
        }

        return self._make_signal(conditions)