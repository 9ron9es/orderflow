"""
Base classes for pluggable entry signal modules.
a
SignalModule: Abstract base for any entry signal (absorption, imbalance, divergence, etc.)
EntrySignal: Immutable dataclass returned by signal.evaluate() with full context.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from nautilus_trader.model.enums import OrderSide

if TYPE_CHECKING:
    from nautilus.features.multi_tf import MultiTFSnapshot
    from nautilus.features.volume_profile import VolumeProfileSnapshot
    from nautilus.sessions.filter import SessionState
    from nautilus.structure.market_structure import MarketStructureSnapshot


@dataclass(slots=True, frozen=True)
class EntrySignal:
    """
    Complete entry signal returned by a SignalModule.

    Attributes
    ----------
    side : OrderSide
        BUY (long) or SELL (short).
    label : str
        Signal name ("absorption_breakout", "imbalance_continuation", etc.).
    confidence : float
        [0.0, 1.0] used for position sizing scaling.
    conditions : dict[str, bool]
        All evaluated conditions for metrics/logging (e.g., {"imb>0.6": True, "stack>=3": True}).
    failed : list[str]
        Conditions that failed and blocked the signal.
    """

    side: OrderSide
    label: str
    confidence: float = 1.0
    conditions: dict[str, bool] = field(default_factory=dict)
    failed: list[str] = field(default_factory=list)


class SignalModule(ABC):
    """
    Abstract base for entry signal detection.

    Subclasses declare class attributes ``label`` and ``side`` and implement
    ``evaluate``. Helpers ``_check`` / ``_make_signal`` build EntrySignal results.
    """

    label: str
    side: OrderSide

    @abstractmethod
    def evaluate(
        self,
        snapshot: "MultiTFSnapshot",
        structure: "MarketStructureSnapshot",
        session: "SessionState",
        vp: "VolumeProfileSnapshot" | None = None,
    ) -> EntrySignal | None:
        ...

    @staticmethod
    def _check(conditions: dict[str, bool]) -> tuple[bool, list[str]]:
        failed = [k for k, v in conditions.items() if not v]
        return len(failed) == 0, failed

    def _make_signal(
        self,
        conditions: dict[str, bool],
        confidence: float = 1.0,
    ) -> EntrySignal | None:
        all_pass, _failed = self._check(conditions)
        if not all_pass:
            return None
        return EntrySignal(
            side=self.side,
            label=self.label,
            confidence=confidence,
            conditions=conditions,
            failed=[],
        )

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(side={self.side}, label={self.label!r})"
