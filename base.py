"""
signals/base.py — Pluggable signal module interface.

Each entry type (absorption breakout, imbalance continuation, etc.)
implements SignalModule. The strategy iterates registered modules and
submits on the first (or any) that returns an EntrySignal.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from nautilus_trader.model.enums import OrderSide

if TYPE_CHECKING:
    from orderflow.nautilus.features.engine import OrderflowFeatureSnapshot
    from orderflow.nautilus.features.multi_tf import MultiTFSnapshot
    from orderflow.nautilus.sessions.filter import SessionState
    from orderflow.nautilus.structure.market_structure import MarketStructureSnapshot


# ─── Entry signal result ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class EntrySignal:
    """
    Returned by a passing SignalModule.evaluate() call.

    Attributes
    ----------
    side       : BUY (long) or SELL (short).
    label      : Module label for metrics/ML tagging (e.g. 'absorption_breakout').
    confidence : Scalar [0.0, 1.0]. Used to scale position size when an
                 InferenceHook is wired (Phase 6). Without ML, default 1.0.
    conditions : Per-gate bool map — logged verbatim for later ML feature
                 engineering. Keys must be stable across versions.
    failed     : List of condition names that blocked the signal (non-empty
                 only when the module returns None; included here for the
                 rejected-signal metrics path).
    """
    side: OrderSide
    label: str
    confidence: float                            # 0.0–1.0
    conditions: dict[str, bool] = field(default_factory=dict)
    failed: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class RejectedSignal:
    """
    Companion to EntrySignal — emitted when evaluate() returns None so that
    MetricsLogger can record *why* a signal was skipped.
    """
    label: str
    side: OrderSide
    failed: list[str]
    conditions: dict[str, bool] = field(default_factory=dict)


# ─── Signal module ABC ─────────────────────────────────────────────────────────

class SignalModule(ABC):
    """
    Abstract base for a single entry-type strategy.

    Subclasses implement evaluate() and declare:
      - label  : unique snake_case string ID
      - side   : OrderSide.BUY or SELL (determines which list it's registered in)

    The strategy calls evaluate() inside its throttled evaluation loop.
    A module must be *stateless* per call — all necessary state is passed
    in via snapshot objects.  Modules MAY hold lightweight EMA/window state
    across calls, but must not mutate external objects.
    """

    #: Unique string identifier used in config, metrics, and ML labels.
    label: str

    #: Preferred side — modules are stored in separate long/short registries.
    side: OrderSide

    @abstractmethod
    def evaluate(
        self,
        snap: MultiTFSnapshot,
        structure: MarketStructureSnapshot,
        session: SessionState,
    ) -> EntrySignal | None:
        """
        Evaluate entry conditions.

        Returns
        -------
        EntrySignal
            If ALL required conditions pass.
        None
            If any required condition fails (log via RejectedSignal separately).
        """
        ...

    # ── Helpers available to all subclasses ───────────────────────────────────

    @staticmethod
    def _check(conditions: dict[str, bool]) -> tuple[bool, list[str]]:
        """
        Evaluate a condition dict.

        Returns
        -------
        (all_pass, failed_keys)
        """
        failed = [k for k, v in conditions.items() if not v]
        return len(failed) == 0, failed

    def _make_signal(
        self,
        conditions: dict[str, bool],
        confidence: float = 1.0,
    ) -> EntrySignal | None:
        """
        Convenience builder: returns EntrySignal if all conditions pass, else None.
        Subclasses can call this instead of implementing the pass/fail logic manually.
        """
        all_pass, failed = self._check(conditions)
        if not all_pass:
            return None
        return EntrySignal(
            side=self.side,
            label=self.label,
            confidence=confidence,
            conditions=conditions,
            failed=[],
        )

    def _make_rejection(self, conditions: dict[str, bool]) -> RejectedSignal:
        _, failed = self._check(conditions)
        return RejectedSignal(
            label=self.label,
            side=self.side,
            failed=failed,
            conditions=conditions,
        )