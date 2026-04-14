"""
signals/long.py — Volume-profile-anchored long entry signals.

Swap from heatmap: at_support/wall_strength → at_hvn_below/hvn.volume_pct
POC context added: above_poc = bullish bias, below_poc = counter-trend (tighter gates).
LVN awareness: divergence signal explicitly checks we're not at an LVN.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from nautilus_trader.model.enums import OrderSide

from nautilus.signals.base import EntrySignal, SignalModule

if TYPE_CHECKING:
    from nautilus.features.volume_profile import VolumeProfileSnapshot
    from nautilus.features.multi_tf import MultiTFSnapshot
    from nautilus.sessions.filter import SessionState
    from nautilus.structure.market_structure import MarketStructureSnapshot


# ════════════════════════════════════════════════════════════════════════════════
#  1. HVN ABSORPTION LONG  ← PRIMARY SIGNAL
#
#  Enter at a High Volume Node below price (support) with absorption starting.
#  HVN = price level where significant volume has traded = structural defense.
#
#  If price is above POC: with-trend, normal thresholds.
#  If price is below POC: counter-trend, tighter gates.
# ════════════════════════════════════════════════════════════════════════════════

class HVNAbsorptionLong(SignalModule):
    label = "hvn_absorption_long"
    side  = OrderSide.BUY

    def __init__(
        self,
        absorption_min: float = 0.08,
        ob_imb_min: float = 0.05,
        min_hvn_volume_pct: float = 0.03,      # HVN must be meaningful
        max_bearish_stack: float = -3.0,
        require_htf_align: bool = True,
        **_,
    ) -> None:
        self._abs_min    = absorption_min
        self._ob_min     = ob_imb_min
        self._min_hvn    = min_hvn_volume_pct
        self._max_bstack = max_bearish_stack
        self._htf_align  = require_htf_align

    def evaluate(
        self,
        snap: "MultiTFSnapshot",
        structure: "MarketStructureSnapshot",
        session: "SessionState",
        vp: Optional["VolumeProfileSnapshot"] = None,
    ) -> EntrySignal | None:
        if not session.active or vp is None or not vp.is_valid:
            return None

        of  = snap.ltf.flow
        hvn = vp.nearest_hvn_below

        # Tighter absorption threshold when trading against POC
        abs_min = self._abs_min if vp.above_poc else self._abs_min * 1.5

        conditions = {
            "at_hvn_support":     vp.at_hvn_below,
            "hvn_meaningful":     hvn is not None and hvn.volume_pct >= self._min_hvn,
            "not_at_lvn":         not vp.at_lvn,
            "hvn_target_exists":  vp.nearest_hvn_above is not None,
            "absorption_start":   of.absorption >= abs_min,
            "directional_signal": snap.ltf.cvd_rising or of.delta_div == -1.0,
            "ob_bid_present":     of.ob_imbalance >= self._ob_min,
            "not_free_falling":   of.stacked_imb >= self._max_bstack,
            "no_bearish_div":     of.delta_div != 1.0,
            # FIX 3: structure.trend is a TrendDirection enum, not a string.
            # trend != "bearish" compares enum to str → always True (filter never fires).
            "htf_not_bearish":    (not self._htf_align) or structure.trend.value != "bearish",
        }
        return self._make_signal(conditions)


# ════════════════════════════════════════════════════════════════════════════════
#  2. HVN DIVERGENCE LONG
#
#  Bullish delta divergence AT a High Volume Node.
#  Without the HVN, divergence can persist for many bars before reversing.
#  The HVN gives the structural reason the divergence will resolve bullishly.
# ════════════════════════════════════════════════════════════════════════════════

class HVNDivergenceLong(SignalModule):
    label = "hvn_divergence_long"
    side  = OrderSide.BUY

    def __init__(
        self,
        absorption_max: float = 0.12,
        ob_imb_min: float = 0.03,
        min_hvn_volume_pct: float = 0.02,      # Slightly looser — divergence adds confidence
        max_bearish_stack: float = -5.0,
        require_htf_align: bool = True,
        **_,
    ) -> None:
        self._abs_max    = absorption_max
        self._ob_min     = ob_imb_min
        self._min_hvn    = min_hvn_volume_pct
        self._max_bstack = max_bearish_stack
        self._htf_align  = require_htf_align

    def evaluate(
        self,
        snap: "MultiTFSnapshot",
        structure: "MarketStructureSnapshot",
        session: "SessionState",
        vp: Optional["VolumeProfileSnapshot"] = None,
    ) -> EntrySignal | None:
        if not session.active or vp is None or not vp.is_valid:
            return None

        of  = snap.ltf.flow
        hvn = vp.nearest_hvn_below

        conditions = {
            "at_hvn_support":        vp.at_hvn_below,
            "hvn_meaningful":        hvn is not None and hvn.volume_pct >= self._min_hvn,
            "not_at_lvn":            not vp.at_lvn,
            "bullish_divergence":    of.delta_div == -1.0,
            "sellers_not_absorbing": of.absorption >= -self._abs_max,
            "ob_bid_present":        of.ob_imbalance >= self._ob_min,
            "not_free_falling":      of.stacked_imb >= self._max_bstack,
            # FIX 3: enum vs string comparison — use .value
            "htf_allows_long":       (not self._htf_align) or (
                structure.trend.value != "bearish" or structure.structure_break
            ),
        }
        return self._make_signal(conditions)


# ════════════════════════════════════════════════════════════════════════════════
#  3. POC RECLAIM LONG
#
#  Price broke below POC (bearish), now reclaiming it with volume.
#  POC reclaim = shift from bearish to bullish market context.
#  Only fires when price crosses back above POC with absorption.
#
#  This replaces "wall breakout long" with a more precise VP-native concept.
# ════════════════════════════════════════════════════════════════════════════════

class POCReclaimLong(SignalModule):
    label = "poc_reclaim_long"
    side  = OrderSide.BUY

    def __init__(
        self,
        absorption_min: float = 0.08,
        ob_imb_min: float = 0.08,
        imb_min: float = 0.12,
        poc_proximity_bps: float = 20.0,      # how close to POC counts as "at POC"
        require_htf_align: bool = True,
        **_,
    ) -> None:
        self._abs_min    = absorption_min
        self._ob_min     = ob_imb_min
        self._imb_min    = imb_min
        self._poc_prox   = poc_proximity_bps
        self._htf_align  = require_htf_align

    def evaluate(
        self,
        snap: "MultiTFSnapshot",
        structure: "MarketStructureSnapshot",
        session: "SessionState",
        vp: Optional["VolumeProfileSnapshot"] = None,
    ) -> EntrySignal | None:
        if not session.active or vp is None or not vp.is_valid:
            return None
        if vp.poc_price is None:
            return None

        of = snap.ltf.flow

        # Price must be within poc_proximity_bps of POC and above it (reclaim)
        poc_close = vp.poc_distance_bps <= self._poc_prox
        reclaiming = vp.above_poc   # price just crossed above POC

        conditions = {
            "near_poc":          poc_close,
            "poc_reclaim":       reclaiming,
            "not_at_lvn":        not vp.at_lvn,
            "absorption_hold":   of.absorption >= self._abs_min,
            "cvd_rising":        snap.ltf.cvd_rising,
            "buy_imbalance":     of.imbalance >= self._imb_min,
            "not_reversing":     of.stacked_imb >= 1,
            "no_bearish_div":    of.delta_div != 1.0,
            "htf_not_bearish":   (not self._htf_align) or structure.trend.value != "bearish",
        }
        return self._make_signal(conditions)


# ════════════════════════════════════════════════════════════════════════════════
#  4. VALUE AREA LOW BOUNCE LONG  ← SECONDARY / GATED
#
#  Price tests Value Area Low (VAL) and bounces.
#  VAL = bottom of the 70% volume zone = institutional reference support.
#  Counter-trend entries from VAL have high accuracy when value area holds.
# ════════════════════════════════════════════════════════════════════════════════

class VALBounceLong(SignalModule):
    label = "val_bounce_long"
    side  = OrderSide.BUY

    def __init__(
        self,
        absorption_min: float = 0.10,
        ob_imb_min: float = 0.06,
        val_proximity_bps: float = 12.0,
        large_dom_min: float = 0.08,
        require_htf_align: bool = True,
        **_,
    ) -> None:
        self._abs_min   = absorption_min
        self._ob_min    = ob_imb_min
        self._val_prox  = val_proximity_bps
        self._ldom_min  = large_dom_min
        self._htf_align = require_htf_align

    def evaluate(
        self,
        snap: "MultiTFSnapshot",
        structure: "MarketStructureSnapshot",
        session: "SessionState",
        vp: Optional["VolumeProfileSnapshot"] = None,
    ) -> EntrySignal | None:
        if not session.active or vp is None or not vp.is_valid:
            return None
        if vp.val_price is None:
            return None

        of = snap.ltf.flow
        ls = of.large_buy_vol + of.large_sell_vol
        large_dom = (of.large_buy_vol - of.large_sell_vol) / ls if ls > 1e-9 else 0.0

        # Price must be near VAL
        current_price = vp.val_price  # we check proximity via VP snapshot
        val_dist_bps = (
            abs(snap.ltf.close_price - vp.val_price) / vp.val_price * 10_000.0
            if snap.ltf.close_price and vp.val_price else 9999.0
        )

        conditions = {
            "near_val":           val_dist_bps <= self._val_prox,
            "not_at_lvn":         not vp.at_lvn,
            "in_or_below_va":     not vp.above_poc,    # price at or below POC
            "hvn_target_exists":  vp.nearest_hvn_above is not None or vp.vah_price is not None,
            "absorption_start":   of.absorption >= self._abs_min,
            "ob_bid_present":     of.ob_imbalance >= self._ob_min,
            "large_dom":          large_dom >= self._ldom_min,
            "cvd_rising":         snap.ltf.cvd_rising,
            "no_bearish_div":     of.delta_div != 1.0,
            "htf_not_bearish":    (not self._htf_align) or structure.trend.value != "bearish",
        }
        return self._make_signal(conditions)