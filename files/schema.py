"""
config/schema.py — Complete production config schema.

All new Phase 2-6 fields wired: bidirectional, sessions, structure, ML.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.identifiers import InstrumentId

from orderflow.nautilus.signals.registry import SignalsConfig

Profile = Literal["backtest", "paper", "live"]


class OrderflowStrategyConfig(StrategyConfig, frozen=True, kw_only=True):
    """
    Flat frozen config for Nautilus framework.
    Load via load_orderflow_config(path) → OrderflowStrategyConfig.
    """

    instrument_id: InstrumentId
    order_id_tag: str = "OF-001"
    client_id: str = "BINANCE"
    timeframe: str = "5m"
    htf_timeframe: str = "1h"
    lookback_candles: int = 50
    book_depth: int = 5
    book_type: str = "L2_MBP"
    eval_throttle_ms: float = 200.0
    require_orderbook: bool = True

    # ── Structure ──────────────────────────────────────────────────────────
    swing_window: int = 5

    # ── Session filter config (serialised as JSON string for Nautilus compat)
    sessions_config: list | None = None   # list of dicts from YAML sessions:

    # ── Signals ───────────────────────────────────────────────────────────
    signals_config: SignalsConfig | None = None

    # ── Indicator params ──────────────────────────────────────────────────
    imbalance_threshold: float = 0.25
    cvd_smoothing: int = 5
    absorption_min: float = 0.15
    stack_min_rows: int = 3
    ob_imb_threshold: float = 0.15
    large_vol_ratio_min: float = 0.10
    price_bucket_size: float = 1.0
    large_trade_pct: float = 0.90
    divergence_window: int = 3

    # ── Risk ──────────────────────────────────────────────────────────────
    max_position_fraction: float = 0.10
    max_notional_usdt: float | None = 500.0
    max_leverage: float | None = 2.0
    max_daily_loss_pct: float = 3.0
    max_consecutive_losses: int = 4
    max_spread_bps: float = 20.0
    stale_tick_ms: float = 5000.0
    min_top_of_book_qty: float = 0.0
    kill_switch_path: str | None = "orderflow/.kill_switch"
    equity_state_path: str | None = "orderflow/.equity_state.json"

    # ── Execution ─────────────────────────────────────────────────────────
    use_market_entries: bool = False
    entry_post_only: bool = True
    stoploss_pct: float = 0.018
    target_pct: float = 0.036
    trailing_trigger_pct: float = 0.012
    trailing_offset_pct: float = 0.008
    max_time_in_trade_secs: float | None = 3600.0
    max_entry_drift_bps: float = 8.0

    # ── ML ────────────────────────────────────────────────────────────────
    ml_state_path: str = "orderflow/.ml_state.pkl"

    # ── Ops ───────────────────────────────────────────────────────────────
    log_metrics: bool = True
    metrics_dir: str = "orderflow/logs/metrics"
