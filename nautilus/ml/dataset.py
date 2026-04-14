"""
ML dataset and inference infrastructure for position sizing and order quality.

Accumulates feature rows (one per bar) and forwards them to a trained model
for position size scaling or rejection.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nautilus.features.multi_tf import MultiTFSnapshot


@dataclass(slots=True)
class FeatureRow:
    """Single-bar feature vector for ML."""

    ts_ms: int
    cvd: float
    cvd_ema: float
    cvd_rising: int  # bool as int
    imbalance: float
    absorption: float
    delta_div: float
    stacked_imb: float
    ob_imbalance: float
    large_dom: float  # Dominance of large trades
    buy_vol: float
    sell_vol: float
    total_vol: float
    # HTF features
    htf_cvd: float
    htf_cvd_rising: int  # bool as int
    htf_imbalance: float
    htf_absorption: float
    # Structure features
    trend: int
    hh: int  # Higher high
    hl: int  # Higher low
    lh: int  # Lower high
    ll: int  # Lower low
    bos_bullish: int  # Break of structure bullish
    bos_bearish: int  # Break of structure bearish
    last_high_price: float
    last_low_price: float
    # Session features
    session_name: str
    session_active: int
    session_minutes_elapsed: int
    session_minutes_to_close: int
    # Signal features
    signal_label: str
    signal_side: str
    signal_confidence: float
    features: dict[str, Any] = field(default_factory=dict)  # Extended features


@dataclass(slots=True)
class LabeledRow(FeatureRow):
    """FeatureRow with outcome label (for training)."""

    forward_return: float = 0.0  # Return N bars ahead
    label: int = 0  # Classification: -1 (down), 0 (flat), +1 (up)


class Labeler:
    """
    Converts FeatureRow + outcome price into LabeledRow.

    Used during backtest/live to accumulate labeled datasets for model training.
    """

    def __init__(
        self,
        forward_bars: int = 5,
        return_threshold_pct: float = 0.5,
    ) -> None:
        """
        Initialize labeler.

        Parameters
        ----------
        forward_bars : int
            Bars ahead to compute return label.
        return_threshold_pct : float
            Return threshold (%) to classify as up/down (-1, 0, +1).
        """
        self._forward_bars = forward_bars
        self._threshold = return_threshold_pct / 100.0

    def label(
        self,
        feature_row: FeatureRow,
        entry_price: float,
        exit_price: float,
    ) -> LabeledRow:
        """
        Create labeled row from feature + outcome.

        Parameters
        ----------
        feature_row : FeatureRow
            Original feature row.
        entry_price : float
            Entry price (close of signal bar).
        exit_price : float
            Exit price (N bars forward).

        Returns
        -------
        LabeledRow
            Feature row with computed label.
        """
        fwd_ret = (exit_price - entry_price) / entry_price

        if fwd_ret > self._threshold:
            lbl = 1  # Up
        elif fwd_ret < -self._threshold:
            lbl = -1  # Down
        else:
            lbl = 0  # Flat

        return LabeledRow(
            ts_ms=feature_row.ts_ms,
            cvd=feature_row.cvd,
            cvd_ema=feature_row.cvd_ema,
            cvd_rising=feature_row.cvd_rising,
            imbalance=feature_row.imbalance,
            absorption=feature_row.absorption,
            delta_div=feature_row.delta_div,
            stacked_imb=feature_row.stacked_imb,
            ob_imbalance=feature_row.ob_imbalance,
            large_dom=feature_row.large_dom,
            buy_vol=feature_row.buy_vol,
            sell_vol=feature_row.sell_vol,
            total_vol=feature_row.total_vol,
            htf_cvd=feature_row.htf_cvd,
            htf_cvd_rising=feature_row.htf_cvd_rising,
            htf_imbalance=feature_row.htf_imbalance,
            htf_absorption=feature_row.htf_absorption,
            trend=feature_row.trend,
            hh=feature_row.hh,
            hl=feature_row.hl,
            lh=feature_row.lh,
            ll=feature_row.ll,
            bos_bullish=feature_row.bos_bullish,
            bos_bearish=feature_row.bos_bearish,
            last_high_price=feature_row.last_high_price,
            last_low_price=feature_row.last_low_price,
            session_name=feature_row.session_name,
            session_active=feature_row.session_active,
            session_minutes_elapsed=feature_row.session_minutes_elapsed,
            session_minutes_to_close=feature_row.session_minutes_to_close,
            signal_label=feature_row.signal_label,
            signal_side=feature_row.signal_side,
            signal_confidence=feature_row.signal_confidence,
            features=feature_row.features,
            forward_return=fwd_ret,
            label=lbl,
        )


class PassthroughHook:
    """
    Default ML inference hook: always returns full position size (no scaling).

    Replace with a trained model for dynamic sizing based on signal confidence.
    """

    def predict(self, features: FeatureRow) -> float:
        """
        Predict position size scale [0.0, 1.0].

        Parameters
        ----------
        features : FeatureRow
            Bar features.

        Returns
        -------
        float
            Size scale: 1.0 = full size, 0.5 = half size, 0.0 = no trade.
        """
        return 1.0  # Full size (no ML filtering)

    def __repr__(self) -> str:
        return "PassthroughHook()"


class DatasetBuffer:
    """
    Accumulates labeled feature rows for model training.

    In-memory buffer; must be periodically exported to disk for training.
    """

    def __init__(self, labeler: Labeler | None = None, max_rows: int = 100_000) -> None:
        """
        Initialize buffer.

        Parameters
        ----------
        labeler : Labeler | None
            Labeler instance for creating labeled rows. If None, raw FeatureRow only.
        max_rows : int
            Maximum rows to keep (FIFO eviction when exceeded).
        """
        self._labeler = labeler or Labeler()
        self._max_rows = max_rows
        self._rows: list[FeatureRow | LabeledRow] = []

    def add_feature(self, feature: FeatureRow) -> None:
        """
        Add a feature row to buffer.

        Parameters
        ----------
        feature : FeatureRow
            Feature row to accumulate.
        """
        self._rows.append(feature)
        if len(self._rows) > self._max_rows:
            self._rows = self._rows[-self._max_rows :]

    def add_labeled(self, labeled: LabeledRow) -> None:
        """
        Add a labeled row to buffer.

        Parameters
        ----------
        labeled : LabeledRow
            Labeled feature row (with outcome).
        """
        self._rows.append(labeled)
        if len(self._rows) > self._max_rows:
            self._rows = self._rows[-self._max_rows :]

    def label_last(
        self,
        entry_price: float,
        exit_price: float,
    ) -> None:
        """
        Convert last row from FeatureRow to LabeledRow.

        Parameters
        ----------
        entry_price : float
            Trade entry price.
        exit_price : float
            Trade exit price.
        """
        if not self._rows:
            return
        last = self._rows[-1]
        if isinstance(last, LabeledRow):
            return  # Already labeled
        if isinstance(last, FeatureRow):
            labeled = self._labeler.label(last, entry_price, exit_price)
            self._rows[-1] = labeled

    @property
    def rows(self) -> list[FeatureRow | LabeledRow]:
        """Return all accumulated rows."""
        return self._rows

    def export(self) -> list[dict[str, Any]]:
        """
        Export rows as JSON-serializable dicts.

        Returns
        -------
        list[dict]
            List of row dicts.
        """
        result = []
        for row in self._rows:
            d = {
                "ts_ms": row.ts_ms,
                "cvd": row.cvd,
                "cvd_ema": row.cvd_ema,
                "cvd_rising": row.cvd_rising,
                "imbalance": row.imbalance,
                "absorption": row.absorption,
                "delta_div": row.delta_div,
                "stacked_imb": row.stacked_imb,
                "ob_imbalance": row.ob_imbalance,
                "large_dom": row.large_dom,
                "buy_vol": row.buy_vol,
                "sell_vol": row.sell_vol,
                "total_vol": row.total_vol,
                "htf_cvd": row.htf_cvd,
                "htf_cvd_rising": row.htf_cvd_rising,
                "htf_imbalance": row.htf_imbalance,
                "htf_absorption": row.htf_absorption,
                "trend": row.trend,
                "hh": row.hh,
                "hl": row.hl,
                "lh": row.lh,
                "ll": row.ll,
                "bos_bullish": row.bos_bullish,
                "bos_bearish": row.bos_bearish,
                "last_high_price": row.last_high_price,
                "last_low_price": row.last_low_price,
                "session_name": row.session_name,
                "session_active": row.session_active,
                "session_minutes_elapsed": row.session_minutes_elapsed,
                "session_minutes_to_close": row.session_minutes_to_close,
                "signal_label": row.signal_label,
                "signal_side": row.signal_side,
                "signal_confidence": row.signal_confidence,
            }
            if isinstance(row, LabeledRow):
                d["forward_return"] = row.forward_return
                d["label"] = row.label
            result.append(d)
        return result

    def reset(self) -> None:
        """Clear buffer."""
        self._rows = []

    def __len__(self) -> int:
        return len(self._rows)

    def __repr__(self) -> str:
        return f"DatasetBuffer(rows={len(self._rows)}, max={self._max_rows})"
