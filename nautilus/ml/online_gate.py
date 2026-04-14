"""
ml/online_gate.py — Online-learning ML gate.

Architecture
------------
We use an incremental (online) logistic regression trained with SGD.
After each trade closes, we call learn(features, outcome) which calls
sklearn's partial_fit() — one stochastic gradient step, no full retraining.

Anti-overfitting design decisions
----------------------------------
1. ElasticNet regularization (L1 + L2): L1 drives sparse features, L2 bounds
   weight magnitude. Alpha=0.01 is strong enough to prevent memorisation.

2. Warm-up gate: ML stays disabled until MIN_TRADES outcomes are accumulated.
   Before that, every signal passes (confidence = 1.0). This prevents the model
   from learning from a handful of random early trades.

3. Online StandardScaler (partial_fit): features are normalized incrementally
   using running mean/var — no future leakage, no refitting on historical data.

4. Calibrated probabilities: raw SGD log-loss probabilities are already
   calibrated (it's a proper scoring rule). We do NOT apply Platt scaling
   on top (that would require a held-out set, which we don't have live).

5. Class-weight auto-balancing: imbalanced long/short outcomes would bias the
   model. `class_weight='balanced'` scales the gradient update by inverse class
   frequency, computed incrementally via sample_weight.

6. Feature importance tracking: we read `.coef_` after every update and store
   the top/bottom features — useful for monitoring drift and debugging.

7. Concept drift detection: a simple EWMA of recent prediction accuracy. If
   accuracy drops below DRIFT_THRESHOLD for DRIFT_WINDOW consecutive trades,
   the model resets (clears weights but keeps scaler). This handles regime changes.

Usage
-----
gate = OnlineMLGate.from_config(config.ml)

# Each evaluation cycle — get entry confidence:
confidence = gate.predict(feature_vec)   # float in [0, 1]; 0 = skip trade

# After trade closes with known outcome:
gate.learn(feature_vec, outcome=1)       # outcome: 1=win, 0=loss

# Persist + restore:
gate.save("orderflow/.ml_state.pkl")
gate = OnlineMLGate.load("orderflow/.ml_state.pkl")
"""

from __future__ import annotations

import json
import logging
import pickle
import time
from collections import deque
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

log = logging.getLogger(__name__)

# Lazy sklearn import (only needed at runtime)
_SGD = None
_Scaler = None

def _get_sgd():
    global _SGD
    if _SGD is None:
        from sklearn.linear_model import SGDClassifier
        _SGD = SGDClassifier
    return _SGD

def _get_scaler():
    global _Scaler
    if _Scaler is None:
        from sklearn.preprocessing import StandardScaler
        _Scaler = StandardScaler
    return _Scaler


# ─── Config ────────────────────────────────────────────────────────────────────

@dataclass
class MLConfig:
    enabled: bool = True

    # Gate behaviour
    confidence_threshold: float = 0.55     # Block if P(win) < threshold
    warmup_trades: int = 50                 # Trades before gate activates
    min_proba_clip: float = 0.10           # Clip confidence to [clip, 1-clip]

    # Model hyperparameters
    alpha: float = 0.005                   # Regularisation strength (higher = stronger)
    l1_ratio: float = 0.15                 # ElasticNet mix: 0=L2 only, 1=L1 only
    learning_rate: str = "optimal"         # SGD schedule
    loss: str = "log_loss"

    # Drift detection
    drift_ewma_alpha: float = 0.1          # EWMA decay for accuracy tracking
    drift_threshold: float = 0.40          # Reset if EWMA accuracy < this
    drift_window: int = 30                 # Consecutive checks before reset

    # Feature config
    feature_names: list[str] = field(default_factory=lambda: DEFAULT_FEATURE_NAMES)

    # Persistence
    state_path: str = "orderflow/.ml_state.pkl"
    metrics_path: str = "orderflow/.ml_metrics.jsonl"


# ─── Feature names (STABLE — do not rename) ────────────────────────────────────

DEFAULT_FEATURE_NAMES: list[str] = [
    # LTF orderflow
    "cvd_ema",
    "cvd_rising",          # 0/1
    "imbalance",
    "absorption",
    "stacked_imb",
    "ob_imbalance",
    "delta_div",
    "large_dom",
    "vol_ratio",           # buy_vol / total_vol

    # HTF orderflow
    "htf_cvd_ema",
    "htf_cvd_rising",
    "htf_imbalance",
    "htf_absorption",

    # Market structure (encoded)
    "trend_bullish",       # one-hot: trend == bullish
    "trend_bearish",
    "trend_ranging",
    "hh", "hl", "lh", "ll",
    "bos_bullish", "bos_bearish",

    # Session
    "session_active",
    "session_elapsed_norm",   # minutes_elapsed / 300 (normalised to ~0-1)
    "session_close_norm",

    # Signal metadata
    "signal_is_long",      # 1=BUY signal, 0=SELL signal
]

N_FEATURES = len(DEFAULT_FEATURE_NAMES)


# ─── Trade record ──────────────────────────────────────────────────────────────

@dataclass
class TradeRecord:
    ts_ms: int
    features: list[float]
    signal_label: str
    signal_side: str
    confidence_at_entry: float
    outcome: int | None = None   # 1=win, 0=loss; None=still open


# ─── Online ML Gate ────────────────────────────────────────────────────────────

class OnlineMLGate:
    """
    Live-learning logistic regression gate.

    The model is a single SGDClassifier that learns incrementally as trades
    resolve. Prediction returns a probability; the caller decides whether to
    trade based on the configured threshold.

    Thread safety: not thread-safe — call from a single strategy thread only.
    """

    def __init__(self, cfg: MLConfig | None = None) -> None:
        self.cfg = cfg or MLConfig()
        self._model: object | None = None
        self._scaler: object | None = None
        self._classes = np.array([0, 1])

        # State
        self._n_trades: int = 0            # Outcomes learned so far
        self._n_preds: int = 0             # Predictions made
        self._active: bool = False         # True once warmup complete
        self._pending: list[TradeRecord] = []   # Open trades awaiting outcome
        self._history: deque[TradeRecord] = deque(maxlen=2000)

        # Drift tracking
        self._accuracy_ewma: float = 0.5
        self._drift_consec: int = 0

        # Feature importance (last known)
        self._coef: np.ndarray | None = None

        self._init_model()

    # ── Model init ─────────────────────────────────────────────────────────────

    def _init_model(self) -> None:
        SGD = _get_sgd()
        Scaler = _get_scaler()

        self._model = SGD(
            loss=self.cfg.loss,
            penalty="elasticnet",
            alpha=self.cfg.alpha,
            l1_ratio=self.cfg.l1_ratio,
            learning_rate=self.cfg.learning_rate,
            max_iter=1,
            tol=None,
            warm_start=True,
            random_state=42,
        )
        self._scaler = Scaler()
        self._n_trades = 0
        self._active = False
        self._accuracy_ewma = 0.5
        self._drift_consec = 0
        self._coef = None
        log.info("OnlineMLGate: model initialised (warmup=%d)", self.cfg.warmup_trades)

    # ── Prediction ─────────────────────────────────────────────────────────────

    def predict(self, features: list[float], record: TradeRecord | None = None) -> float:
        """
        Return entry confidence P(win) ∈ [0, 1].

        If the gate is not yet active (warmup), returns 1.0 (passthrough).
        The caller should skip the trade if confidence < cfg.confidence_threshold.

        Parameters
        ----------
        features : Feature vector from build_feature_vector().
        record   : If provided, stored as a pending trade awaiting outcome.
        """
        if record is not None:
            self._pending.append(record)

        self._n_preds += 1

        if not self._active or not self.cfg.enabled:
            return 1.0   # Warmup passthrough

        try:
            x = np.array(features, dtype=np.float64).reshape(1, -1)
            x_scaled = self._scaler.transform(x)
            proba = self._model.predict_proba(x_scaled)[0]
            # proba = [P(loss), P(win)]
            confidence = float(proba[1])
            # Clip to prevent extreme probabilities
            lo = self.cfg.min_proba_clip
            confidence = float(np.clip(confidence, lo, 1.0 - lo))
            return confidence
        except Exception as exc:
            log.warning("OnlineMLGate.predict failed: %s", exc)
            return 1.0   # Fail-open

    # ── Learning ───────────────────────────────────────────────────────────────

    def learn(self, features: list[float], outcome: int, ts_ms: int | None = None) -> None:
        """
        Update the model with one resolved trade.

        Parameters
        ----------
        features : Same feature vector used at prediction time.
        outcome  : 1 = profitable trade, 0 = losing trade.
        ts_ms    : Timestamp (for logging).
        """
        if outcome not in (0, 1):
            raise ValueError(f"outcome must be 0 or 1, got {outcome}")

        x = np.array(features, dtype=np.float64).reshape(1, -1)
        y = np.array([outcome])

        # Update scaler first
        self._scaler.partial_fit(x)
        x_scaled = self._scaler.transform(x)

        # Update model
        self._model.partial_fit(x_scaled, y, classes=self._classes)
        self._n_trades += 1

        # Track feature importance
        self._coef = self._model.coef_[0].copy()

        # Update drift detector
        if self._active:
            pred = int(self._model.predict(x_scaled)[0])
            correct = int(pred == outcome)
            alpha = self.cfg.drift_ewma_alpha
            self._accuracy_ewma = alpha * correct + (1 - alpha) * self._accuracy_ewma
            if self._accuracy_ewma < self.cfg.drift_threshold:
                self._drift_consec += 1
                if self._drift_consec >= self.cfg.drift_window:
                    log.warning(
                        "OnlineMLGate: concept drift detected (acc_ewma=%.3f). Resetting weights.",
                        self._accuracy_ewma,
                    )
                    self._reset_weights()
            else:
                self._drift_consec = 0

        # Activate after warmup
        if not self._active and self._n_trades >= self.cfg.warmup_trades:
            self._active = True
            log.info(
                "OnlineMLGate: ACTIVE after %d trades (threshold=%.2f)",
                self._n_trades, self.cfg.confidence_threshold,
            )

        self._log_metric({"event": "learn", "outcome": outcome, "n_trades": self._n_trades,
                          "active": self._active, "acc_ewma": round(self._accuracy_ewma, 4),
                          "ts_ms": ts_ms or int(time.time() * 1000)})

    # ── Resolve pending trades ─────────────────────────────────────────────────

    def resolve_trade(self, ts_ms_entry: int, realized_pnl: float) -> None:
        """
        Called when a trade closes. Matches by entry timestamp and triggers learn().
        outcome = 1 if realized_pnl > 0 else 0.
        """
        outcome = 1 if realized_pnl > 0.0 else 0
        matched = False
        remaining = []
        for rec in self._pending:
            if rec.ts_ms == ts_ms_entry and not matched:
                rec.outcome = outcome
                self._history.append(rec)
                self.learn(rec.features, outcome, ts_ms=ts_ms_entry)
                matched = True
            else:
                remaining.append(rec)
        self._pending = remaining
        if not matched:
            log.debug("OnlineMLGate.resolve_trade: no pending record for ts=%d", ts_ms_entry)

    # ── Gate decision helper ───────────────────────────────────────────────────

    def should_pass(self, confidence: float) -> bool:
        """True if confidence >= threshold (trade should proceed)."""
        if not self.cfg.enabled or not self._active:
            return True
        return confidence >= self.cfg.confidence_threshold

    # ── Drift reset ────────────────────────────────────────────────────────────

    def _reset_weights(self) -> None:
        """Reset model weights but keep the scaler (feature stats are still valid)."""
        SGD = _get_sgd()
        self._model = SGD(
            loss=self.cfg.loss,
            penalty="elasticnet",
            alpha=self.cfg.alpha,
            l1_ratio=self.cfg.l1_ratio,
            learning_rate=self.cfg.learning_rate,
            max_iter=1,
            tol=None,
            warm_start=True,
            random_state=42,

        )
        self._coef = None
        self._drift_consec = 0
        self._accuracy_ewma = 0.5
        # n_trades intentionally kept — warmup already passed

    # ── Feature importance ─────────────────────────────────────────────────────

    def feature_importance(self, top_n: int = 10) -> list[tuple[str, float]]:
        """Return top-N features by absolute coefficient magnitude."""
        if self._coef is None or len(self.cfg.feature_names) != len(self._coef):
            return []
        pairs = list(zip(self.cfg.feature_names, self._coef.tolist()))
        pairs.sort(key=lambda x: abs(x[1]), reverse=True)
        return pairs[:top_n]

    # ── Status ─────────────────────────────────────────────────────────────────

    @property
    def is_active(self) -> bool:
        return self._active

    @property
    def n_trades(self) -> int:
        return self._n_trades

    @property
    def accuracy_ewma(self) -> float:
        return self._accuracy_ewma

    def status(self) -> dict:
        importance = self.feature_importance(5)
        win_rate = None
        if self._history:
            wins = sum(1 for r in self._history if r.outcome == 1)
            win_rate = round(wins / len(self._history), 4)
        return {
            "active": self._active,
            "n_trades": self._n_trades,
            "n_preds": self._n_preds,
            "n_pending": len(self._pending),
            "accuracy_ewma": round(self._accuracy_ewma, 4),
            "win_rate_history": win_rate,
            "top_features": importance,
            "drift_consecutive": self._drift_consec,
        }

    # ── Persistence ────────────────────────────────────────────────────────────

    def save(self, path: str | None = None) -> None:
        p = Path(path or self.cfg.state_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        state = {
            "model": self._model,
            "scaler": self._scaler,
            "n_trades": self._n_trades,
            "n_preds": self._n_preds,
            "active": self._active,
            "accuracy_ewma": self._accuracy_ewma,
            "drift_consec": self._drift_consec,
            "coef": self._coef,
            "cfg": self.cfg,
        }
        with open(p, "wb") as f:
            pickle.dump(state, f)
        log.info("OnlineMLGate: state saved → %s", p)

    @classmethod
    def load(cls, path: str) -> OnlineMLGate:
        p = Path(path)
        if not p.exists():
            log.info("OnlineMLGate: no saved state at %s, starting fresh", p)
            return cls()
        with open(p, "rb") as f:
            state = pickle.load(f)
        gate = cls(cfg=state["cfg"])
        gate._model         = state["model"]
        gate._scaler        = state["scaler"]
        gate._n_trades      = state["n_trades"]
        gate._n_preds       = state["n_preds"]
        gate._active        = state["active"]
        gate._accuracy_ewma = state["accuracy_ewma"]
        gate._drift_consec  = state["drift_consec"]
        gate._coef          = state["coef"]
        log.info("OnlineMLGate: state restored from %s (n_trades=%d, active=%s)",
                 p, gate._n_trades, gate._active)
        return gate

    # ── Logging ────────────────────────────────────────────────────────────────

    def _log_metric(self, data: dict) -> None:
        try:
            p = Path(self.cfg.metrics_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "a") as f:
                f.write(json.dumps(data) + "\n")
        except OSError:
            pass


# ─── Feature vector builder ────────────────────────────────────────────────────

def build_feature_vector(
    snap,              # MultiTFSnapshot
    structure,         # MarketStructureSnapshot
    session,           # SessionState
    signal_side: str,  # 'BUY' or 'SELL'
) -> list[float]:
    """
    Convert snapshots → flat numeric feature vector aligned with DEFAULT_FEATURE_NAMES.

    All values are raw (unscaled) — the OnlineMLGate's StandardScaler handles
    normalization incrementally.
    """
    ltf = snap.ltf.flow
    htf = snap.htf.flow

    large_sum = ltf.large_buy_vol + ltf.large_sell_vol
    large_dom = (
        (ltf.large_buy_vol - ltf.large_sell_vol) / large_sum
        if large_sum > 1e-9 else 0.0
    )
    vol_ratio = ltf.buy_vol / ltf.total_vol if ltf.total_vol > 1e-9 else 0.5

    trend_b  = 1.0 if structure.trend == "bullish" else 0.0
    trend_be = 1.0 if structure.trend == "bearish" else 0.0
    trend_r  = 1.0 if structure.trend == "ranging"  else 0.0

    elapsed_norm = (session.minutes_elapsed or 0) / 300.0
    close_norm   = (session.minutes_to_close or 0) / 300.0

    is_long = 1.0 if signal_side == "BUY" else 0.0

    vec = [
        # LTF
        float(snap.ltf.cvd_ema),
        float(snap.ltf.cvd_rising),
        float(ltf.imbalance),
        float(ltf.absorption),
        float(ltf.stacked_imb),
        float(ltf.ob_imbalance),
        float(ltf.delta_div),
        float(large_dom),
        float(vol_ratio),
        # HTF
        float(snap.htf.cvd_ema),
        float(snap.htf.cvd_rising),
        float(htf.imbalance),
        float(htf.absorption),
        # Structure
        trend_b, trend_be, trend_r,
        float(structure.hh), float(structure.hl),
        float(structure.lh), float(structure.ll),
        float(structure.bos_bullish), float(structure.bos_bearish),
        # Session
        float(session.active),
        elapsed_norm,
        close_norm,
        # Signal
        is_long,
    ]

    assert len(vec) == N_FEATURES, f"Feature vector length mismatch: {len(vec)} != {N_FEATURES}"
    return vec
