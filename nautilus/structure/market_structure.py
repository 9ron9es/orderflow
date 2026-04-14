"""
Market structure engine for swing-based trend detection and HTF regime analysis.

Detects:
- Swing highs and lows (local price extrema)
- Breakout points (price breaks above swing high or below swing low)
- Trend direction (bullish, bearish, undefined)
- Structure breaks (entry points for trend-following)
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal


class TrendDirection(Enum):
    """Market trend classification."""

    BULLISH = "bullish"
    BEARISH = "bearish"
    UNDEFINED = "undefined"


@dataclass(slots=True, frozen=True)
class SwingLevel:
    """Single swing high or low."""

    price: float
    bar_idx: int                       # Bar index when formed
    is_high: bool                      # True=swing high, False=swing low


@dataclass(slots=True, frozen=True)
class MarketStructureSnapshot:
    """Market structure state snapshot."""

    trend: TrendDirection              # Overall trend
    last_swing_high: SwingLevel | None
    last_swing_low: SwingLevel | None
    structure_break: bool              # True if price broke last structure level
    break_type: Literal["high", "low", None]  # Which level was broken


# Null structure for initialization
NULL_STRUCTURE = MarketStructureSnapshot(
    trend=TrendDirection.UNDEFINED,
    last_swing_high=None,
    last_swing_low=None,
    structure_break=False,
    break_type=None,
)


class MarketStructureEngine:
    """
    Swing-based trend detection.

    Maintains a rolling list of swing highs/lows and detects:
    1. Local extrema (swing formation)
    2. Trend direction (comparing lows and highs)
    3. Structure breaks (entry signals for trend continuation)

    Parameters
    ----------
    swing_window : int
        Lookback bars for swing detection. Higher = stronger swings.
    """

    def __init__(self, swing_window: int = 5) -> None:
        self._swing_window = swing_window
        self._closes: list[float] = []
        self._swing_highs: list[SwingLevel] = []
        self._swing_lows: list[SwingLevel] = []

    def update(
        self,
        close_price_or_candles: float | list,
        close_price: float | None = None,
    ) -> MarketStructureSnapshot:
        """
        Update structure with a new bar close.

        Parameters
        ----------
        close_price_or_candles : float | list
            Either latest candle close price (float) or list of CandleFlow objects.
        close_price : float | None
            If first param is candle list, this is the current close price.

        Returns
        -------
        MarketStructureSnapshot
            Updated market structure state.
        """
        # Handle both calling conventions
        if isinstance(close_price_or_candles, list):
            # Called with (candles, close_price)
            if close_price is None:
                raise ValueError("close_price required when passing candles")
            price_to_add = close_price
        else:
            # Called with just (close_price)
            price_to_add = close_price_or_candles
        self._closes.append(price_to_add)

        # Keep buffer within reasonable size (2x swing_window for lookback)
        max_keep = max(100, self._swing_window * 3)
        if len(self._closes) > max_keep:
            self._closes = self._closes[-max_keep:]

        # Detect new swings
        self._detect_swings()

        # Analyze trend and structure breaks
        return self._evaluate_structure()

    def _detect_swings(self) -> None:
        """Detect and update swing highs/lows."""
        if len(self._closes) < self._swing_window + 1:
            return

        idx = len(self._closes) - 1
        mid_idx = idx - self._swing_window // 2

        if mid_idx < self._swing_window:
            return

        mid_price = self._closes[mid_idx]
        left_prices = self._closes[mid_idx - self._swing_window // 2 : mid_idx]
        right_prices = self._closes[mid_idx + 1 : mid_idx + self._swing_window // 2 + 1]

        # Swing high: price is higher than surrounding bars
        if all(mid_price > p for p in left_prices) and all(
            mid_price > p for p in right_prices
        ):
            swing_high = SwingLevel(price=mid_price, bar_idx=mid_idx, is_high=True)
            # Avoid duplicates near the same price
            if (
                not self._swing_highs
                or abs(self._swing_highs[-1].price - mid_price) > mid_price * 0.001
            ):
                self._swing_highs.append(swing_high)

        # Swing low: price is lower than surrounding bars
        if all(mid_price < p for p in left_prices) and all(
            mid_price < p for p in right_prices
        ):
            swing_low = SwingLevel(price=mid_price, bar_idx=mid_idx, is_high=False)
            # Avoid duplicates near the same price
            if (
                not self._swing_lows
                or abs(self._swing_lows[-1].price - mid_price) > mid_price * 0.001
            ):
                self._swing_lows.append(swing_low)

    def _evaluate_structure(self) -> MarketStructureSnapshot:
        """Evaluate current trend and structure breaks."""
        if not self._closes or (not self._swing_highs and not self._swing_lows):
            return NULL_STRUCTURE

        current_price = self._closes[-1]
        last_swing_high = self._swing_highs[-1] if self._swing_highs else None
        last_swing_low = self._swing_lows[-1] if self._swing_lows else None

        # Determine trend by comparing consecutive swing lows
        trend = TrendDirection.UNDEFINED
        if len(self._swing_lows) >= 2:
            if self._swing_lows[-1].price > self._swing_lows[-2].price:
                trend = TrendDirection.BULLISH
            elif self._swing_lows[-1].price < self._swing_lows[-2].price:
                trend = TrendDirection.BEARISH

        # Detect structure breaks
        structure_break = False
        break_type = None

        if last_swing_high and current_price > last_swing_high.price:
            structure_break = True
            break_type = "high"
        elif last_swing_low and current_price < last_swing_low.price:
            structure_break = True
            break_type = "low"

        return MarketStructureSnapshot(
            trend=trend,
            last_swing_high=last_swing_high,
            last_swing_low=last_swing_low,
            structure_break=structure_break,
            break_type=break_type,
        )

    def reset(self) -> None:
        """Clear all buffers and reset to NULL_STRUCTURE."""
        self._closes = []
        self._swing_highs = []
        self._swing_lows = []

    def __repr__(self) -> str:
        return f"MarketStructureEngine(swing_window={self._swing_window})"
