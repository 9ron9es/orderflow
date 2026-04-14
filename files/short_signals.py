"""
signals/short/ — All short-side entry modules.

Mirror-symmetric to long signals. All thresholds are negated appropriately.
"""

from __future__ import annotations

from nautilus_trader.model.enums import OrderSide

from orderflow.nautilus.signals.base import EntrySignal, SignalModule


# ════════════════════════════════════════════════════════════════════════════════
#  1. ABSORPTION BREAKOUT (SHORT)
#  Large sellers absorb bids at resistance → price breaks down.
#  Best in: trending down, post-distribution, at key resistance.
# ════════════════════════════════════════════════════════════════════════════════

class AbsorptionBreakoutShort(SignalModule):
    """
    Short-side absorption breakout.

    Conditions (all must pass):
    - large_dom ≤ -large_dom_min      — large sellers dominate
    - absorption ≤ -absorption_min    — net large selling pressure confirmed
    - NOT cvd_rising                  — macro momentum falling
    - ob_imbalance ≤ -ob_imb_min     — book ask-heavy (resistance confirmed)
    - no bullish delta divergence
    - HTF not bullish (if require_htf_align)
    """

    label = "absorption_breakout_short"
    side  = OrderSide.SELL

    def __init__(
        self,
        absorption_min: float = 0.10,
        large_dom_min: float = 0.15,
        ob_imb_min: float = 0.10,
        require_htf_align: bool = True,
        **_,
    ) -> None:
        self._abs_min   = absorption_min
        self._ldom_min  = large_dom_min
        self._ob_min    = ob_imb_min
        self._htf_align = require_htf_align

    def evaluate(self, snap, structure, session) -> EntrySignal | None:
        if not session.active:
            return None

        of = snap.ltf.flow
        ls = of.large_buy_vol + of.large_sell_vol
        large_dom = (of.large_buy_vol - of.large_sell_vol) / ls if ls > 1e-9 else 0.0

        conditions = {
            "session_active":     True,
            "large_dom_bearish":  large_dom <= -self._ldom_min,
            "sell_absorption":    of.absorption <= -self._abs_min,
            "cvd_falling":        not snap.ltf.cvd_rising,
            "ob_ask_heavy":       of.ob_imbalance <= -self._ob_min,
            "no_bullish_div":     of.delta_div != -1.0,
            "htf_not_bullish":    (not self._htf_align) or structure.trend != "bullish",
        }
        return self._make_signal(conditions)


# ════════════════════════════════════════════════════════════════════════════════
#  2. IMBALANCE CONTINUATION (SHORT)
#  Sustained sell-side flow across multiple bars — downtrend continuation.
# ════════════════════════════════════════════════════════════════════════════════

class ImbalanceContinuationShort(SignalModule):
    """
    Short-side imbalance continuation.

    Conditions:
    - imbalance ≤ -imbalance_threshold — this bar has strong sell pressure
    - stacked_imb ≤ -stack_min_rows   — N consecutive bearish bars
    - absorption ≤ absorption_min      — no significant buy-side absorption
    - NOT cvd_rising                   — cumulative delta falling
    - ob_imbalance ≤ -ob_imb_threshold — book ask-heavy
    - large_dom ≤ -large_dom_min       — institutions selling
    - no bullish divergence
    - HTF bearish or ranging
    """

    label = "imbalance_continuation_short"
    side  = OrderSide.SELL

    def __init__(
        self,
        imbalance_threshold: float = 0.25,
        absorption_min: float = 0.15,
        stack_min_rows: int = 3,
        ob_imb_threshold: float = 0.15,
        large_dom_min: float = 0.10,
        require_htf_align: bool = True,
        **_,
    ) -> None:
        self._imb       = imbalance_threshold
        self._abs_min   = absorption_min
        self._stack     = stack_min_rows
        self._ob_min    = ob_imb_threshold
        self._ldom_min  = large_dom_min
        self._htf_align = require_htf_align

    def evaluate(self, snap, structure, session) -> EntrySignal | None:
        if not session.active:
            return None

        of = snap.ltf.flow
        ls = of.large_buy_vol + of.large_sell_vol
        large_dom = (of.large_buy_vol - of.large_sell_vol) / ls if ls > 1e-9 else 0.0

        conditions = {
            "session_active":        True,
            "cvd_falling":           not snap.ltf.cvd_rising,
            "sell_imbalance":        of.imbalance <= -self._imb,
            "no_buy_absorption":     of.absorption <= self._abs_min,
            "stacked_bearish":       of.stacked_imb <= -self._stack,
            "ob_ask_heavy":          of.ob_imbalance <= -self._ob_min,
            "large_dom_bearish":     large_dom <= -self._ldom_min,
            "no_bullish_div":        of.delta_div != -1.0,
            "htf_not_bullish":       (not self._htf_align) or structure.trend != "bullish",
        }
        return self._make_signal(conditions)


# ════════════════════════════════════════════════════════════════════════════════
#  3. DELTA DIVERGENCE REVERSAL (SHORT)
#  Price makes new high but delta makes lower high → sellers defending top.
#  Best in: overbought, at major resistance, after a squeeze / stop hunt.
# ════════════════════════════════════════════════════════════════════════════════

class DivergenceReversalShort(SignalModule):
    """
    Short-side bearish delta divergence reversal.

    Conditions:
    - delta_div == 1.0                — bearish divergence present
    - absorption <= absorption_min    — buyers not absorbing (net sellers at high)
    - ob_imbalance <= -ob_imb_min     — book has ask resistance
    - stacked_imb <= 4                — not deeply stacked bullish (no parabolic)
    - HTF ranging or bearish
    """

    label = "divergence_reversal_short"
    side  = OrderSide.SELL

    def __init__(
        self,
        absorption_min: float = 0.10,
        ob_imb_min: float = 0.05,
        require_htf_align: bool = True,
        max_bullish_stack: float = 4.0,
        **_,
    ) -> None:
        self._abs_min     = absorption_min
        self._ob_min      = ob_imb_min
        self._htf_align   = require_htf_align
        self._max_b_stack = max_bullish_stack

    def evaluate(self, snap, structure, session) -> EntrySignal | None:
        if not session.active:
            return None

        of = snap.ltf.flow

        conditions = {
            "session_active":        True,
            "bearish_div":           of.delta_div == 1.0,
            "no_buy_absorption":     of.absorption <= self._abs_min,
            "ob_ask_resistance":     of.ob_imbalance <= -self._ob_min,
            "not_deeply_bullish":    of.stacked_imb <= self._max_b_stack,
            "htf_not_hard_bullish":  (not self._htf_align) or structure.trend != "bullish" or structure.bos_bearish,
        }
        return self._make_signal(conditions)
