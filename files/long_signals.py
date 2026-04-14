"""
signals/long/ — All long-side entry modules.

Each class is self-contained. Import individually or let the registry load by name.
"""

from __future__ import annotations

from nautilus_trader.model.enums import OrderSide

from orderflow.nautilus.signals.base import EntrySignal, SignalModule


# ════════════════════════════════════════════════════════════════════════════════
#  1. ABSORPTION BREAKOUT (LONG)
#  Large buyers absorb offers at support → price breaks up.
#  Best in: trending up, post-compression, at key levels.
# ════════════════════════════════════════════════════════════════════════════════

class AbsorptionBreakoutLong(SignalModule):
    """
    Long-side absorption breakout.

    Conditions (all must pass):
    - large_dom ≥ large_dom_min       — large buyers dominate
    - absorption ≥ absorption_min     — net large buying pressure confirmed
    - cvd_rising                       — macro momentum up
    - ob_imbalance ≥ ob_imb_min       — book bid-heavy (support confirmed)
    - no bearish delta divergence
    - HTF not bearish (if require_htf_align)

    Tuning guide:
    Lower absorption_min (0.05) for more signals in choppy markets.
    Raise large_dom_min (0.25) for cleaner breakouts in trending markets.
    """

    label = "absorption_breakout_long"
    side  = OrderSide.BUY

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
            "large_dom":          large_dom >= self._ldom_min,
            "buy_absorption":     of.absorption >= self._abs_min,
            "cvd_rising":         snap.ltf.cvd_rising,
            "ob_bid_heavy":       of.ob_imbalance >= self._ob_min,
            "no_bearish_div":     of.delta_div != 1.0,
            "htf_not_bearish":    (not self._htf_align) or structure.trend != "bearish",
        }
        return self._make_signal(conditions)


# ════════════════════════════════════════════════════════════════════════════════
#  2. IMBALANCE CONTINUATION (LONG)
#  Sustained buy-side flow across multiple bars — trend continuation.
#  Best in: confirmed uptrend, strong volume, London/NY open.
# ════════════════════════════════════════════════════════════════════════════════

class ImbalanceContinuationLong(SignalModule):
    """
    Long-side imbalance continuation.

    Conditions:
    - imbalance ≥ imbalance_threshold  — this bar has strong buy pressure
    - stacked_imb ≥ stack_min_rows    — N consecutive bullish bars
    - absorption ≥ -absorption_min    — no significant sell-side absorption
    - cvd_rising                       — cumulative delta trending up
    - ob_imbalance ≥ ob_imb_threshold  — book confirms
    - large_dom ≥ large_dom_min        — institutions leading
    - no bearish divergence
    - HTF bullish or ranging
    """

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
            "cvd_rising":            snap.ltf.cvd_rising,
            "imbalance":             of.imbalance >= self._imb,
            "no_sell_absorption":    of.absorption >= -self._abs_min,
            "stacked_imb":           of.stacked_imb >= self._stack,
            "ob_imbalance":          of.ob_imbalance >= self._ob_min,
            "large_dom":             large_dom >= self._ldom_min,
            "no_bearish_div":        of.delta_div != 1.0,
            "htf_not_bearish":       (not self._htf_align) or structure.trend != "bearish",
        }
        return self._make_signal(conditions)


# ════════════════════════════════════════════════════════════════════════════════
#  3. DELTA DIVERGENCE REVERSAL (LONG)
#  Price makes new low but delta makes higher low → buyers absorbing at lows.
#  Best in: oversold, at major support, after a liquidation flush.
# ════════════════════════════════════════════════════════════════════════════════

class DivergenceReversalLong(SignalModule):
    """
    Long-side bullish delta divergence reversal.

    Conditions:
    - delta_div == -1.0               — bullish divergence present
    - absorption >= -absorption_min   — sellers not absorbing (net buyers at low)
    - ob_imbalance >= ob_imb_min      — book has bid support
    - stacked_imb >= -1              — imbalance not deeply stacked bearish (no free-fall)
    - HTF ranging or bullish
    """

    label = "divergence_reversal_long"
    side  = OrderSide.BUY

    def __init__(
        self,
        absorption_min: float = 0.10,
        ob_imb_min: float = 0.05,    # Looser — divergences often happen at thin OB
        require_htf_align: bool = True,
        max_bearish_stack: float = -4.0,   # Block if deeply stacked bearish
        **_,
    ) -> None:
        self._abs_min     = absorption_min
        self._ob_min      = ob_imb_min
        self._htf_align   = require_htf_align
        self._max_b_stack = max_bearish_stack

    def evaluate(self, snap, structure, session) -> EntrySignal | None:
        if not session.active:
            return None

        of = snap.ltf.flow

        conditions = {
            "session_active":        True,
            "bullish_div":           of.delta_div == -1.0,
            "no_sell_absorption":    of.absorption >= -self._abs_min,
            "ob_bid_support":        of.ob_imbalance >= self._ob_min,
            "not_deeply_bearish":    of.stacked_imb >= self._max_b_stack,
            # HTF: allow ranging (counter-trend reversals into ranging market)
            "htf_not_hard_bearish":  (not self._htf_align) or structure.trend != "bearish" or structure.bos_bullish,
        }
        return self._make_signal(conditions)


# ════════════════════════════════════════════════════════════════════════════════
#  4. LATE ENTRY CONFIRMATION (LONG)
#  Second bar confirmation after a breakout. Lower risk, lower reward.
#  Best in: after an absorption breakout bar closes, enter on pullback confirmation.
# ════════════════════════════════════════════════════════════════════════════════

class LateEntryConfirmLong(SignalModule):
    """
    Long-side late-entry: wait for the second bar to confirm.

    The pattern:
    - Bar N: strong move up with large positive absorption
    - Bar N+1 (this bar): pullback but still holding imbalance ≥ threshold
                          CVD stays above prior candle's value (no round-trip)

    Conditions:
    - stacked_imb >= 2               — at least 2 bullish bars (N and N-1)
    - imbalance >= imb_continuation  — this bar still net buy (not full reversal)
    - absorption >= -absorption_min  — sellers not retaking
    - cvd_rising                     — macro delta still up
    - htf bullish
    """

    label = "late_entry_confirm_long"
    side  = OrderSide.BUY

    def __init__(
        self,
        imb_continuation: float = 0.10,   # Looser than full signal — we expect slight pull
        absorption_min: float = 0.15,
        stack_min_rows: int = 2,
        require_htf_align: bool = True,
        **_,
    ) -> None:
        self._imb_cont  = imb_continuation
        self._abs_min   = absorption_min
        self._stack     = stack_min_rows
        self._htf_align = require_htf_align

    def evaluate(self, snap, structure, session) -> EntrySignal | None:
        if not session.active:
            return None

        of = snap.ltf.flow

        conditions = {
            "session_active":        True,
            "stacked_imb_2plus":     of.stacked_imb >= self._stack,
            "imb_holding":           of.imbalance >= self._imb_cont,
            "no_sell_absorption":    of.absorption >= -self._abs_min,
            "cvd_still_rising":      snap.ltf.cvd_rising,
            "no_bearish_div":        of.delta_div != 1.0,
            "htf_bullish_or_range":  (not self._htf_align) or structure.trend != "bearish",
        }
        return self._make_signal(conditions)
