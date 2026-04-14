"""
ops/ml_monitor.py — Real-time ML gate status monitor.

Run in a separate terminal while the strategy is live:
    python -m orderflow.nautilus.ops.ml_monitor --state orderflow/.ml_state.pkl

Prints:
  - Gate status (active / warmup)
  - N trades learned
  - EWMA accuracy
  - Top features by importance
  - Recent win/loss streak
  - Drift detection status
"""

from __future__ import annotations

import argparse
import json
import os
import pickle
import time
from datetime import datetime
from pathlib import Path


CLEAR = "\033[H\033[J"
GREEN = "\033[92m"
RED   = "\033[91m"
YELLOW = "\033[93m"
CYAN  = "\033[96m"
BOLD  = "\033[1m"
RESET = "\033[0m"


def load_gate(path: str):
    """Load OnlineMLGate from pickle without importing the full stack."""
    p = Path(path)
    if not p.exists():
        return None
    with open(p, "rb") as f:
        return pickle.load(f)


def tail_metrics(metrics_path: str, n: int = 15) -> list[dict]:
    p = Path(metrics_path)
    if not p.exists():
        return []
    lines = p.read_text().strip().split("\n")
    result = []
    for line in lines[-n:]:
        try:
            result.append(json.loads(line))
        except Exception:
            pass
    return result


def render(state_path: str, metrics_path: str) -> None:
    state = None
    try:
        with open(state_path, "rb") as f:
            state = pickle.load(f)
    except Exception:
        pass

    print(CLEAR, end="")
    print(f"{BOLD}{CYAN}═══ ORDERFLOW ML GATE MONITOR ═══{RESET}")
    print(f"State: {state_path}   [{datetime.now().strftime('%H:%M:%S')}]")
    print()

    if state is None:
        print(f"{RED}No state file found at {state_path}{RESET}")
        return

    active = state.get("active", False)
    n_trades = state.get("n_trades", 0)
    n_preds  = state.get("n_preds", 0)
    acc_ewma = state.get("accuracy_ewma", 0.5)
    drift_c  = state.get("drift_consec", 0)
    coef     = state.get("coef")
    cfg      = state.get("cfg")
    warmup   = cfg.warmup_trades if cfg else 50
    threshold = cfg.confidence_threshold if cfg else 0.55
    drift_thresh = cfg.drift_threshold if cfg else 0.40
    feat_names = cfg.feature_names if cfg else []

    gate_str = f"{GREEN}ACTIVE{RESET}" if active else f"{YELLOW}WARMUP ({n_trades}/{warmup}){RESET}"
    acc_color = GREEN if acc_ewma >= 0.52 else (YELLOW if acc_ewma >= 0.45 else RED)

    print(f"  Gate:         {gate_str}")
    print(f"  Trades learned: {BOLD}{n_trades}{RESET}  |  Predictions: {n_preds}")
    print(f"  Accuracy EWMA:  {acc_color}{acc_ewma:.3f}{RESET}  (threshold for reset: {drift_thresh:.2f})")
    print(f"  Confidence threshold: {threshold:.2f}")
    print(f"  Drift consecutive:    {drift_c}")
    print()

    # Feature importance
    if coef is not None and feat_names and len(feat_names) == len(coef):
        pairs = sorted(zip(feat_names, coef), key=lambda x: abs(x[1]), reverse=True)
        print(f"{BOLD}  Top features (by |coef|):{RESET}")
        for name, w in pairs[:8]:
            bar_len = min(int(abs(w) * 30), 30)
            bar_color = GREEN if w > 0 else RED
            bar = bar_color + "█" * bar_len + RESET
            print(f"    {name:<30} {w:+.4f}  {bar}")
        print()

    # Recent events from metrics
    events = tail_metrics(metrics_path, n=20)
    if events:
        print(f"{BOLD}  Recent events:{RESET}")
        for e in events[-8:]:
            ev = e.get("event", "?")
            ts = e.get("ts_ms", "")
            outcome = e.get("outcome", "")
            pnl = e.get("realized_pnl", "")

            if ev == "learn":
                oc_str = f"{GREEN}WIN{RESET}" if outcome == 1 else f"{RED}LOSS{RESET}"
                acc = e.get("acc_ewma", "")
                print(f"    {ev:<25} outcome={oc_str}  acc={acc:.3f}  trades={e.get('n_trades', '')}")
            elif ev in ("entry_submitted", "entry_ml_blocked"):
                color = GREEN if ev == "entry_submitted" else RED
                label = e.get("label", "")
                conf = e.get("confidence", e.get("ml_confidence", ""))
                print(f"    {color}{ev:<35}{RESET} {label:<35} conf={conf:.3f}" if conf else f"    {color}{ev}{RESET}")
            elif ev == "position_closed":
                pnl_color = GREEN if float(pnl or 0) > 0 else RED
                print(f"    {ev:<25} pnl={pnl_color}{pnl:.2f}{RESET}")
            else:
                print(f"    {ev}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state",   default="orderflow/.ml_state.pkl")
    parser.add_argument("--metrics", default="orderflow/.ml_metrics.jsonl")
    parser.add_argument("--interval", type=float, default=5.0, help="Refresh seconds")
    args = parser.parse_args()

    try:
        while True:
            render(args.state, args.metrics)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nMonitor stopped.")


if __name__ == "__main__":
    main()
