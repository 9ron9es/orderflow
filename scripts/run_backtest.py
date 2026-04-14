"""
scripts/run_backtest.py — Wire the catalog into a Nautilus BacktestEngine.

Assumes download_backtest_data.py has already been run.

Usage
-----
    python scripts/run_backtest.py --start 2024-10-01 --end 2025-01-01
    python scripts/run_backtest.py --start 2024-10-01 --end 2025-01-01 --config config/backtest.yaml
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.backtest.models import FillModel, LatencyModel
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.currencies import USDT
from nautilus_trader.model.enums import AccountType, OmsType, book_type_from_str
from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
from nautilus_trader.model.objects import Money
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from nautilus_trader.test_kit.providers import TestInstrumentProvider

from nautilus.config.schema import OrderflowStrategyConfig, SignalsConfig
from nautilus.strategy.orderflow_strategy import OrderflowStrategy


CATALOG_DIR = Path("data/catalog")


def build_engine(start: datetime, end: datetime, initial_balance: float = 10_000.0) -> BacktestEngine:
    engine = BacktestEngine(
        config=BacktestEngineConfig(
            logging=LoggingConfig(log_level="WARNING"),   # reduce noise
            run_analysis=True,
        )
    )

    # Venue: simulated Binance with realistic latency + slippage
    engine.add_venue(
        venue=Venue("BINANCE"),
        oms_type=OmsType.NETTING,
        account_type=AccountType.MARGIN,
        base_currency=None,
        starting_balances=[Money(initial_balance, USDT)],
        fill_model=FillModel(
            prob_fill_on_limit=0.8,      # limit order fill probability
            prob_slippage=0.3,           # slippage on market orders
            random_seed=42,
        ),
        latency_model=LatencyModel(
            base_latency_nanos=int(50e6),   # 50ms base latency
        ),
        book_type=book_type_from_str("L2_MBP"),
    )

    # Instrument
    instrument = TestInstrumentProvider.btcusdt_binance()
    engine.add_instrument(instrument)

    # Load data from catalog
    catalog = ParquetDataCatalog(str(CATALOG_DIR))
    start_ns = int(start.timestamp() * 1e9)
    end_ns   = int(end.timestamp() * 1e9)

    trade_ticks = catalog.trade_ticks(
        instrument_ids=[str(instrument.id)],
        start=start_ns,
        end=end_ns,
    )
    if not trade_ticks:
        raise RuntimeError(
            f"No trade ticks found in catalog for range {start} → {end}. "
            "Run download_backtest_data.py first."
        )
    engine.add_data(trade_ticks)

    bars = catalog.bars(
        instrument_ids=[str(instrument.id)],
        bar_types=["BTCUSDT.BINANCE-1-HOUR-LAST-EXTERNAL"],
        start=start_ns,
        end=end_ns,
    )
    if bars:
        engine.add_data(bars)
    else:
        print("WARNING: no 1h bars found — HTF structure engine will not fire")

    # Strategy config — mirrors live.yaml but with backtest-safe overrides
    strategy_config = OrderflowStrategyConfig(
        instrument_id=instrument.id,
        client_id="BINANCE",
        order_id_tag="BT-001",

        # Timeframes
        timeframe="5m",
        htf_timeframe="1h",

        # Features
        lookback_candles=50,
        price_bucket_size=1.0,
        large_trade_pct=0.90,
        cvd_smoothing=5,
        divergence_window=3,
        swing_window=5,

        # Book (disable for backtest — no live OB replay)
        book_depth=5,
        book_type="L2_MBP",
        require_orderbook=False,     # no L2 replay → disable OB gate

        # VP config
        vp_config={
            "bucket_size": 10.0,
            "window_trades": 8_000,
            "value_area_pct": 0.70,
            "hvn_percentile": 0.75,
            "lvn_percentile": 0.25,
            "proximity_bps": 15.0,
            "min_buckets": 10,
            "stop_buffer_bps": 5.0,
            "session_mode": False,
        },

        # Signals
        signals_config=SignalsConfig(
            long=["hvn_absorption_long", "hvn_divergence_long", "poc_reclaim_long"],
            short=["hvn_absorption_short", "hvn_divergence_short", "poc_rejection_short"],
        ),

        # Signal thresholds
        imbalance_threshold=0.20,
        absorption_min=0.08,
        ob_imb_threshold=0.05,

        # Risk
        max_position_fraction=0.25,
        max_notional_usdt=20_000.0,
        max_daily_loss_pct=3.0,
        max_consecutive_losses=4,
        max_spread_bps=999.0,        # disabled in backtest (no live spread data)
        stale_tick_ms=5_000.0,
        min_top_of_book_qty=0.0,     # disabled
        kill_switch_path=None,
        max_leverage=3.0,
        equity_state_path=None,      # no disk persistence in backtest
        loss_cooldown_secs=90.0,

        # Execution
        use_market_entries=True,
        entry_post_only=False,
        stoploss_pct=0.015,
        target_pct=0.030,
        trailing_trigger_pct=0.012,
        trailing_offset_pct=0.006,
        min_hold_secs=15.0,
        max_time_in_trade_secs=1200.0,
        eval_throttle_ms=200,

        # Logging
        log_metrics=False,           # turn on if you want per-event CSV output
        metrics_dir="data/bt_metrics",
    )

    engine.add_strategy(OrderflowStrategy(config=strategy_config))
    return engine


def run(start: datetime, end: datetime, initial_balance: float = 10_000.0) -> None:
    print(f"Backtest: {start.date()} → {end.date()}  |  balance: ${initial_balance:,.0f}")
    print("Building engine …")

    engine = build_engine(start, end, initial_balance)

    print("Running …")
    engine.run(start=start, end=end)

    # ── Results ───────────────────────────────────────────────────────────
    stats = engine.get_result()
    account = engine.portfolio.account(Venue("BINANCE"))

    print()
    print("=" * 50)
    print("BACKTEST RESULTS")
    print("=" * 50)

    if stats:
        for k, v in stats.items():
            print(f"  {k:<35} {v}")

    if account:
        bal = account.balance(USDT)
        if bal:
            final  = float(bal.total.as_double())
            pnl    = final - initial_balance
            pnl_pct = pnl / initial_balance * 100
            print()
            print(f"  Final balance:   ${final:>12,.2f}")
            print(f"  Net PnL:         ${pnl:>+12,.2f}  ({pnl_pct:+.2f}%)")

    print("=" * 50)
    engine.dispose()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run Nautilus backtest from local catalog.")
    p.add_argument("--start", required=True, type=lambda s: datetime.fromisoformat(s).replace(tzinfo=UTC))
    p.add_argument("--end",   required=True, type=lambda s: datetime.fromisoformat(s).replace(tzinfo=UTC))
    p.add_argument("--balance", type=float, default=10_000.0, help="Initial USDT balance")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    run(start=args.start, end=args.end, initial_balance=args.balance)