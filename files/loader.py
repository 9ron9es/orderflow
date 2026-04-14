"""
config/loader.py — Load YAML → OrderflowStrategyConfig.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from nautilus_trader.model.identifiers import InstrumentId

from orderflow.nautilus.config.schema import OrderflowStrategyConfig
from orderflow.nautilus.signals.registry import SignalsConfig


def load_orderflow_config(path: str | Path) -> OrderflowStrategyConfig:
    with open(path) as f:
        raw = yaml.safe_load(f)

    instrument_id = InstrumentId.from_str(raw["instrument_id"])

    # ── Sessions ──────────────────────────────────────────────────────────
    sessions_config = raw.get("sessions")   # list of dicts or None

    # ── Signals ───────────────────────────────────────────────────────────
    sig_raw = raw.get("signals", {}) or {}
    module_kwargs = sig_raw.get("module_kwargs", {}) or {}
    # Merge module_kwargs into indicator params at top level too
    signals_config = SignalsConfig(
        long=sig_raw.get("long", ["imbalance_continuation_long", "absorption_breakout_long"]),
        short=sig_raw.get("short", ["imbalance_continuation_short", "absorption_breakout_short"]),
        require_all=bool(sig_raw.get("require_all", False)),
        module_kwargs=module_kwargs,
    )

    # ── Risk ──────────────────────────────────────────────────────────────
    risk = raw.get("risk", {}) or {}

    # ── Execution ─────────────────────────────────────────────────────────
    exe = raw.get("execution", {}) or {}

    # ── ML ────────────────────────────────────────────────────────────────
    ml = raw.get("ml", {}) or {}

    return OrderflowStrategyConfig(
        instrument_id=instrument_id,
        order_id_tag=str(raw.get("order_id_tag", "OF-001")),
        client_id=str(raw.get("client_id", "BINANCE")),
        timeframe=str(raw.get("timeframe", "5m")),
        htf_timeframe=str(raw.get("htf_timeframe", "1h")),
        lookback_candles=int(raw.get("lookback_candles", 50)),
        book_depth=int(raw.get("book_depth", 5)),
        book_type=str(raw.get("book_type", "L2_MBP")),
        eval_throttle_ms=float(raw.get("eval_throttle_ms", 200.0)),
        require_orderbook=bool(raw.get("require_orderbook", True)),
        swing_window=int(raw.get("swing_window", 5)),
        sessions_config=sessions_config,
        signals_config=signals_config,

        # Indicator params (also forwarded to modules via module_kwargs)
        imbalance_threshold=float(module_kwargs.get("imbalance_threshold", 0.25)),
        cvd_smoothing=int(raw.get("cvd_smoothing", 5)),
        absorption_min=float(module_kwargs.get("absorption_min", 0.15)),
        stack_min_rows=int(module_kwargs.get("stack_min_rows", 3)),
        ob_imb_threshold=float(module_kwargs.get("ob_imb_threshold", 0.15)),
        large_vol_ratio_min=float(module_kwargs.get("large_dom_min", 0.10)),
        price_bucket_size=float(raw.get("price_bucket_size", 1.0)),
        large_trade_pct=float(raw.get("large_trade_pct", 0.90)),
        divergence_window=int(raw.get("divergence_window", 3)),

        # Risk
        max_position_fraction=float(risk.get("max_position_fraction", 0.10)),
        max_notional_usdt=risk.get("max_notional_usdt"),
        max_leverage=risk.get("max_leverage"),
        max_daily_loss_pct=float(risk.get("max_daily_loss_pct", 3.0)),
        max_consecutive_losses=int(risk.get("max_consecutive_losses", 4)),
        max_spread_bps=float(risk.get("max_spread_bps", 20.0)),
        stale_tick_ms=float(risk.get("stale_tick_ms", 5000.0)),
        min_top_of_book_qty=float(risk.get("min_top_of_book_qty", 0.0)),
        kill_switch_path=risk.get("kill_switch_path", "orderflow/.kill_switch"),
        equity_state_path=risk.get("equity_state_path", "orderflow/.equity_state.json"),

        # Execution
        use_market_entries=bool(exe.get("use_market_entries", False)),
        entry_post_only=bool(exe.get("entry_post_only", True)),
        stoploss_pct=float(exe.get("stoploss_pct", 0.018)),
        target_pct=float(exe.get("target_pct", 0.036)),
        trailing_trigger_pct=float(exe.get("trailing_trigger_pct", 0.012)),
        trailing_offset_pct=float(exe.get("trailing_offset_pct", 0.008)),
        max_time_in_trade_secs=exe.get("max_time_in_trade_secs"),
        max_entry_drift_bps=float(exe.get("max_entry_drift_bps", 8.0)),

        # ML
        ml_state_path=ml.get("state_path", "orderflow/.ml_state.pkl"),

        # Ops
        log_metrics=bool(raw.get("log_metrics", True)),
        metrics_dir=str(raw.get("metrics_dir", "orderflow/logs/metrics")),
    )
