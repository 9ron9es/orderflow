"""
features/heatmap.py — Liquidity heatmap engine.

Two signals combined into a single price-level score:
  1. Traded volume (TPO-style): where has the tape actually printed volume?
     High traded volume = price accepted / defended at this level historically.
  2. Resting OB volume: where are large limit orders sitting RIGHT NOW?
     High resting volume = active defense by market makers / institutions.

Together they identify "walls" — price levels that act as support or resistance.

Strategy use:
  - Enter LONG when price is AT a support wall with absorption beginning.
  - Enter SHORT when price is AT a resistance wall with selling beginning.
  - Set stop BELOW the wall (wall breaks = thesis invalidated).
  - Set target AT the next wall above (price gravitates toward liquidity).
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Optional


# ── Data structures ────────────────────────────────────────────────────────────

@dataclass(slots=True, frozen=True)
class HeatmapLevel:
    price: float
    traded_volume: float     # cumulative traded volume at this bucket
    resting_volume: float    # current resting OB size at this level
    total_score: float       # combined normalized [0, 1]
    side: str                # "support" | "resistance"
    distance_bps: float      # distance from current price in bps (always positive)


@dataclass(slots=True)
class HeatmapSnapshot:
    """
    Point-in-time output of the heatmap for signal consumption.

    at_support / at_resistance: True if price is within proximity_bps of a wall.
    wall_strength:              [0, 1] score of the nearest actionable wall.
    target_price:               next wall in the trade direction (profit target anchor).
    stop_price:                 wall boundary for stop placement.
    """
    nearest_support: Optional[HeatmapLevel] = None
    nearest_resistance: Optional[HeatmapLevel] = None
    at_support: bool = False
    at_resistance: bool = False
    support_walls: list[HeatmapLevel] = field(default_factory=list)
    resistance_walls: list[HeatmapLevel] = field(default_factory=list)
    wall_strength: float = 0.0
    long_target_price: Optional[float] = None   # nearest resistance wall → long TP
    short_target_price: Optional[float] = None  # nearest support wall → short TP
    long_stop_price: Optional[float] = None     # just below nearest support wall
    short_stop_price: Optional[float] = None    # just above nearest resistance wall


# ── Engine ─────────────────────────────────────────────────────────────────────

class LiquidityHeatmap:
    """
    Rolling liquidity heatmap over a configurable trade window.

    Parameters
    ----------
    bucket_size : float
        Price granularity per bucket (e.g. 10.0 for BTC @ $1 buckets).
    window_trades : int
        Number of most recent trades to keep in the rolling window.
    wall_percentile : float
        Volume percentile above which a level is considered a "wall" (default 0.80).
    proximity_bps : float
        How close (in bps) price must be to a wall for at_support/at_resistance = True.
    min_walls : int
        Minimum number of wall levels to require before emitting valid signals.
        Below this the market is too thin / data too sparse to trust.
    ob_weight : float
        Weight of resting OB volume relative to traded volume in the combined score.
        1.0 = equal weight; 2.0 = OB volume matters twice as much.
    stop_buffer_bps : float
        Stop price is placed this many bps beyond the wall boundary.
    """

    def __init__(
        self,
        bucket_size: float = 10.0,
        window_trades: int = 5_000,
        wall_percentile: float = 0.80,
        proximity_bps: float = 15.0,
        min_walls: int = 2,
        ob_weight: float = 1.5,
        stop_buffer_bps: float = 5.0,
    ) -> None:
        self._bucket = bucket_size
        self._window = window_trades
        self._wall_pct = wall_percentile
        self._proximity_bps = proximity_bps
        self._min_walls = min_walls
        self._ob_weight = ob_weight
        self._stop_buffer_bps = stop_buffer_bps

        # traded volume per bucket (rolling window via deque of (bucket, vol) events)
        self._traded: dict[int, float] = defaultdict(float)
        self._tape: deque[tuple[int, float]] = deque(maxlen=window_trades)

        # resting OB snapshot (most recent add_ob_snapshot call)
        self._resting: dict[int, float] = {}   # bucket → size

    # ── Feed methods ───────────────────────────────────────────────────────────

    def add_trade(self, price: float, volume: float) -> None:
        """Add a single trade tick to the rolling tape."""
        b = self._bucket_key(price)
        self._tape.append((b, volume))
        self._traded[b] = self._traded.get(b, 0.0) + volume

        # If window is full, evict the oldest entry
        if len(self._tape) == self._tape.maxlen:
            old_b, old_vol = self._tape[0]
            self._traded[old_b] = max(0.0, self._traded[old_b] - old_vol)
            if self._traded[old_b] < 1e-12:
                del self._traded[old_b]

    def add_ob_snapshot(
        self,
        bids: list[tuple[float, float]],   # [(price, size), ...]
        asks: list[tuple[float, float]],
    ) -> None:
        """
        Replace resting OB with the latest book snapshot.
        Call this once per orderbook delta batch — don't call per-tick.
        """
        self._resting = {}
        for px, sz in bids:
            b = self._bucket_key(px)
            self._resting[b] = self._resting.get(b, 0.0) + sz
        for px, sz in asks:
            b = self._bucket_key(px)
            self._resting[b] = self._resting.get(b, 0.0) + sz

    # ── Snapshot ───────────────────────────────────────────────────────────────

    def compute_snapshot(self, current_price: float) -> HeatmapSnapshot:
        """
        Build a HeatmapSnapshot relative to current_price.

        Returns empty snapshot (all None/False) if insufficient data.
        """
        if not self._traded and not self._resting:
            return HeatmapSnapshot()

        # ── Combine scores ────────────────────────────────────────────────
        all_buckets = set(self._traded) | set(self._resting)
        if not all_buckets:
            return HeatmapSnapshot()

        # Normalize traded volume
        max_traded = max((self._traded.get(b, 0.0) for b in all_buckets), default=1.0)
        max_traded = max(max_traded, 1e-12)

        # Normalize resting volume
        max_resting = max((self._resting.get(b, 0.0) for b in all_buckets), default=1.0)
        max_resting = max(max_resting, 1e-12)

        scored: dict[int, float] = {}
        for b in all_buckets:
            t_norm = self._traded.get(b, 0.0) / max_traded
            r_norm = self._resting.get(b, 0.0) / max_resting * self._ob_weight
            scored[b] = (t_norm + r_norm) / (1.0 + self._ob_weight)

        if not scored:
            return HeatmapSnapshot()

        # ── Wall threshold (percentile) ───────────────────────────────────
        scores = sorted(scored.values())
        idx = int(len(scores) * self._wall_pct)
        threshold = scores[min(idx, len(scores) - 1)]

        # ── Classify walls ────────────────────────────────────────────────
        current_b = self._bucket_key(current_price)
        supports: list[HeatmapLevel] = []
        resistances: list[HeatmapLevel] = []

        for b, score in scored.items():
            if score < threshold:
                continue
            level_price = b * self._bucket
            dist_bps = abs(level_price - current_price) / current_price * 10_000.0
            if dist_bps < 1.0:     # too close — at current price, ignore
                continue

            traded = self._traded.get(b, 0.0)
            resting = self._resting.get(b, 0.0)

            if level_price < current_price:
                side = "support"
                lvl = HeatmapLevel(level_price, traded, resting, score, side, dist_bps)
                supports.append(lvl)
            else:
                side = "resistance"
                lvl = HeatmapLevel(level_price, traded, resting, score, side, dist_bps)
                resistances.append(lvl)

        # Sort by distance
        supports.sort(key=lambda x: x.distance_bps)
        resistances.sort(key=lambda x: x.distance_bps)

        if len(supports) + len(resistances) < self._min_walls:
            return HeatmapSnapshot(support_walls=supports, resistance_walls=resistances)

        nearest_sup = supports[0] if supports else None
        nearest_res = resistances[0] if resistances else None

        at_support     = nearest_sup is not None and nearest_sup.distance_bps <= self._proximity_bps
        at_resistance  = nearest_res is not None and nearest_res.distance_bps <= self._proximity_bps

        wall_strength = 0.0
        if at_support and nearest_sup:
            wall_strength = nearest_sup.total_score
        elif at_resistance and nearest_res:
            wall_strength = nearest_res.total_score

        # ── Level-anchored bracket prices ─────────────────────────────────
        buf = self._stop_buffer_bps / 10_000.0

        long_target  = nearest_res.price if nearest_res else None
        short_target = nearest_sup.price if nearest_sup else None

        long_stop  = nearest_sup.price * (1 - buf) if nearest_sup else None
        short_stop = nearest_res.price * (1 + buf) if nearest_res else None

        return HeatmapSnapshot(
            nearest_support=nearest_sup,
            nearest_resistance=nearest_res,
            at_support=at_support,
            at_resistance=at_resistance,
            support_walls=supports,
            resistance_walls=resistances,
            wall_strength=wall_strength,
            long_target_price=long_target,
            short_target_price=short_target,
            long_stop_price=long_stop,
            short_stop_price=short_stop,
        )

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _bucket_key(self, price: float) -> int:
        return int(price / self._bucket)

    @property
    def tape_length(self) -> int:
        return len(self._tape)

    @property
    def is_warm(self) -> bool:
        """True once we have enough tape to be reliable."""
        return len(self._tape) >= int(self._tape.maxlen * 0.20)  # type: ignore[operator]
