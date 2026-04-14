"""
signals/base.py + registry.py — Signal module ABC and config-driven loader.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from nautilus_trader.model.enums import OrderSide


@dataclass(frozen=True)
class EntrySignal:
    side: OrderSide
    label: str
    confidence: float = 1.0
    conditions: dict[str, bool] = field(default_factory=dict)
    failed: list[str] = field(default_factory=list)


class SignalModule(ABC):
    label: str
    side: OrderSide

    @abstractmethod
    def evaluate(self, snap, structure, session) -> EntrySignal | None: ...

    @staticmethod
    def _check(conditions: dict[str, bool]) -> tuple[bool, list[str]]:
        failed = [k for k, v in conditions.items() if not v]
        return len(failed) == 0, failed

    def _make_signal(self, conditions: dict[str, bool], confidence: float = 1.0) -> EntrySignal | None:
        all_pass, failed = self._check(conditions)
        if not all_pass:
            return None
        return EntrySignal(side=self.side, label=self.label, confidence=confidence, conditions=conditions)


# ─── Registry ─────────────────────────────────────────────────────────────────

from dataclasses import dataclass as _dc

@_dc
class SignalsConfig:
    long: list[str] = field(default_factory=lambda: [
        "imbalance_continuation_long",
        "absorption_breakout_long",
    ])
    short: list[str] = field(default_factory=lambda: [
        "imbalance_continuation_short",
        "absorption_breakout_short",
    ])
    require_all: bool = False
    module_kwargs: dict = field(default_factory=dict)


# Maps label → class (avoids importlib overhead)
def _build_module_map():
    from orderflow.nautilus.signals.long_signals import (
        AbsorptionBreakoutLong,
        ImbalanceContinuationLong,
        DivergenceReversalLong,
        LateEntryConfirmLong,
    )
    from orderflow.nautilus.signals.short_signals import (
        AbsorptionBreakoutShort,
        ImbalanceContinuationShort,
        DivergenceReversalShort,
    )
    return {
        "absorption_breakout_long":     AbsorptionBreakoutLong,
        "imbalance_continuation_long":  ImbalanceContinuationLong,
        "divergence_reversal_long":     DivergenceReversalLong,
        "late_entry_confirm_long":      LateEntryConfirmLong,
        "absorption_breakout_short":    AbsorptionBreakoutShort,
        "imbalance_continuation_short": ImbalanceContinuationShort,
        "divergence_reversal_short":    DivergenceReversalShort,
    }


class SignalRegistry:
    def __init__(self, long_modules, short_modules, require_all=False):
        self._long = long_modules
        self._short = short_modules
        self.require_all = require_all

    @property
    def long_modules(self): return self._long
    @property
    def short_modules(self): return self._short

    @classmethod
    def from_config(cls, cfg: SignalsConfig) -> SignalRegistry:
        module_map = _build_module_map()
        kwargs = cfg.module_kwargs or {}

        def load(label):
            if label not in module_map:
                raise ValueError(f"Unknown signal: {label!r}. Available: {sorted(module_map)}")
            return module_map[label](**kwargs)

        return cls(
            [load(l) for l in cfg.long],
            [load(l) for l in cfg.short],
            cfg.require_all,
        )

    def evaluate_long(self, snap, structure, session) -> list[EntrySignal]:
        results = []
        for m in self._long:
            sig = m.evaluate(snap, structure, session)
            if sig:
                results.append(sig)
                if not self.require_all:
                    break
        return results

    def evaluate_short(self, snap, structure, session) -> list[EntrySignal]:
        results = []
        for m in self._short:
            sig = m.evaluate(snap, structure, session)
            if sig:
                results.append(sig)
                if not self.require_all:
                    break
        return results
