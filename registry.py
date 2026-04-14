
"""
signals/registry.py — Config-driven signal module loader.

Usage
-----
registry = SignalRegistry.from_config(config.signals)
long_modules  = registry.long_modules
short_modules = registry.short_modules

# In strategy evaluation loop:
for module in long_modules:
    signal = module.evaluate(snap, structure, session)
    if signal:
        return signal   # first match wins (or collect all if require_all=False)
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from nautilus_trader.model.enums import OrderSide

from nautilus.signals.base import SignalModule
from nautilus.config.schema import SignalsConfig

if TYPE_CHECKING:
    pass


# ─── Built-in module registry ──────────────────────────────────────────────────

#: Maps label → (module_path, class_name).
#: Add new modules here — no other file needs to change.
_BUILTIN_MODULES: dict[str, tuple[str, str]] = {
    # Long entries
    "absorption_breakout_long":      ("nautilus.signals.long.absorption_breakout",      "AbsorptionBreakoutLong"),
    "imbalance_continuation_long":   ("nautilus.signals.long.imbalance_continuation",   "ImbalanceContinuationLong"),
    "divergence_reversal_long":      ("nautilus.signals.long.divergence_reversal",      "DivergenceReversalLong"),
    "late_entry_confirm_long":       ("nautilus.signals.long.late_entry_confirm",       "LateEntryConfirmLong"),
    # Short entries
    "absorption_breakout_short":     ("nautilus.signals.short.absorption_breakout",     "AbsorptionBreakoutShort"),
    "imbalance_continuation_short":  ("nautilus.signals.short.imbalance_continuation",  "ImbalanceContinuationShort"),
    "divergence_reversal_short":     ("nautilus.signals.short.divergence_reversal",     "DivergenceReversalShort"),
}


# ─── Registry ─────────────────────────────────────────────────────────────────

class SignalRegistry:
    """
    Loads and owns instantiated SignalModule objects.
    Thread-safe for read (evaluate) once fully constructed.
    """

    def __init__(
        self,
        long_modules: list[SignalModule],
        short_modules: list[SignalModule],
        require_all: bool = False,
    ) -> None:
        self._long: list[SignalModule] = long_modules
        self._short: list[SignalModule] = short_modules
        self.require_all = require_all

    @property
    def long_modules(self) -> list[SignalModule]:
        return self._long

    @property
    def short_modules(self) -> list[SignalModule]:
        return self._short

    # ── Factory ───────────────────────────────────────────────────────────────

    @classmethod
    def from_config(cls, cfg: SignalsConfig) -> SignalRegistry:
        """Instantiate all configured modules."""
        kwargs = cfg.module_kwargs or {}
        long_mods  = [cls._load(label, kwargs) for label in cfg.long]
        short_mods = [cls._load(label, kwargs) for label in cfg.short]
        return cls(long_mods, short_mods, cfg.require_all)

    @classmethod
    def _load(cls, label: str, kwargs: dict) -> SignalModule:
        """Resolve label to class and instantiate."""
        if label not in _BUILTIN_MODULES:
            raise ValueError(
                f"Unknown signal module {label!r}. "
                f"Available: {sorted(_BUILTIN_MODULES)}"
            )
        mod_path, cls_name = _BUILTIN_MODULES[label]
        try:
            module = importlib.import_module(mod_path)
            klass = getattr(module, cls_name)
        except (ImportError, AttributeError) as exc:
            raise ImportError(
                f"Could not load signal module {label!r} from {mod_path}: {exc}"
            ) from exc
        return klass(**kwargs)

    # ── Evaluation helpers ────────────────────────────────────────────────────

    def evaluate_long(self, snap, structure, session):
        """
        Evaluate all long modules.

        Returns
        -------
        list[EntrySignal]
            Passing signals.  Empty = no entry.
            With require_all=False, return after first match.
        """
        from orderflow.nautilus.signals.base import EntrySignal
        results: list[EntrySignal] = []
        for module in self._long:
            sig = module.evaluate(snap, structure, session)
            if sig:
                results.append(sig)
                if not self.require_all:
                    break
        return results

    def evaluate_short(self, snap, structure, session):
        """Same as evaluate_long but for short modules."""
        from orderflow.nautilus.signals.base import EntrySignal
        results: list[EntrySignal] = []
        for module in self._short:
            sig = module.evaluate(snap, structure, session)
            if sig:
                results.append(sig)
                if not self.require_all:
                    break
        return results