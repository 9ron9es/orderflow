#!/usr/bin/env python3
"""
Backtest orderflow strategy on tick data.

Runs full orderflow strategy on converted Parquet tick data.
Logs results to backtest_results.json
"""

import json
import sys
from datetime import datetime
from pathlib import Path
from time import perf_counter

# Ensure project root is importable when running as a script.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from nautilus_trader.adapters.binance import BINANCE_VENUE
from nautilus_trader.backtest.config import BacktestEngineConfig
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.currencies import USDT
from nautilus_trader.model.enums import AccountType, BookType, OmsType
from nautilus_trader.model.identifiers import TraderId
from nautilus_trader.model.objects import Money
from nautilus_trader.test_kit.providers import TestInstrumentProvider

from nautilus.config.loader import load_orderflow_config
from nautilus.config.schema import orderflow_strategy_config_from_stack
from nautilus.data.ticks import parquet_ticks_to_trade_ticks
from nautilus.strategy.orderflow_strategy import OrderflowStrategy


def run_backtest(
    config_path: Path,
    parquet_path: Path,
    trader_id: str = "BACKTESTER-ORDERFLOW-001",
) -> dict:
    """
    Run backtest and return results dict.
    """
    run_t0 = perf_counter()
    print(f"\n{'='*80}")
    print(f"BACKTEST: {parquet_path.name}")
    print(f"Config: {config_path.name}")
    print(f"{'='*80}\n")

    # Load config
    t0 = perf_counter()
    print("[1/5] Loading config...")
    stack_cfg = load_orderflow_config(config_path)
    strat_cfg = orderflow_strategy_config_from_stack(stack_cfg)
    print(f"      done in {perf_counter() - t0:.2f}s")

    # Create instrument
    instrument = TestInstrumentProvider.btcusdt_perp_binance()

    # Create backtest engine
    t0 = perf_counter()
    print("[2/5] Building backtest engine...")
    engine_cfg = BacktestEngineConfig(
        trader_id=TraderId(trader_id),
        logging=LoggingConfig(log_level="INFO", log_colors=True, use_pyo3=True),
    )
    engine = BacktestEngine(config=engine_cfg)

    # Add venue
    engine.add_venue(
        venue=BINANCE_VENUE,
        oms_type=OmsType.NETTING,
        book_type=BookType.L1_MBP,
        account_type=AccountType.MARGIN,
        base_currency=None,
        starting_balances=[Money(100_000.0, USDT)],
    )

    # Add instrument
    engine.add_instrument(instrument)
    print(f"      done in {perf_counter() - t0:.2f}s")

    # Load ticks
    t0 = perf_counter()
    print("[3/5] Loading and converting ticks...")
    print(f"      source: {parquet_path}")
    ticks = parquet_ticks_to_trade_ticks(parquet_path, instrument)
    if not ticks:
        raise SystemExit(f"No trade ticks loaded from {parquet_path}")
    print(f"      loaded {len(ticks):,} ticks in {perf_counter() - t0:.2f}s")
    engine.add_data(ticks)

    # Add strategy
    t0 = perf_counter()
    print("[4/5] Wiring strategy...")
    strategy = OrderflowStrategy(config=strat_cfg)
    engine.add_strategy(strategy=strategy)
    print(f"      done in {perf_counter() - t0:.2f}s")

    # Run backtest
    t0 = perf_counter()
    print("[5/5] Running backtest engine... (this can take several minutes on large files)")
    engine.run()
    print(f"      backtest finished in {perf_counter() - t0:.2f}s\n")

    # Generate reports
    account_report = engine.trader.generate_account_report(BINANCE_VENUE)
    fills_report = engine.trader.generate_order_fills_report()
    positions_report = engine.trader.generate_positions_report()

    print(account_report)
    print(fills_report)
    print(positions_report)

    # Extract key metrics
    results = {
        "timestamp": datetime.now().isoformat(),
        "parquet_file": str(parquet_path),
        "ticks_count": len(ticks),
        "account_report": str(account_report),
        "fills_report": str(fills_report),
        "positions_report": str(positions_report),
    }

    engine.reset()
    engine.dispose()
    print(f"Total runtime: {perf_counter() - run_t0:.2f}s")

    return results


def main():
    config_path = Path("/home/adem/orderflow/nautilus/config/profiles/backtest.yaml")
    parquet_path = Path("/home/adem/orderflow/ticks/BTCUSDT/2026-01.parquet")
    
    if not config_path.exists():
        raise SystemExit(f"Config not found: {config_path}")
    if not parquet_path.exists():
        raise SystemExit(f"Parquet not found: {parquet_path}")
    
    # Run backtest
    results = run_backtest(config_path, parquet_path)
    
    # Save results
    output_file = Path("/home/adem/orderflow/backtest_results.json")
    with open(output_file, "w") as f:
        json.dump(results, f, indent=2)
    
    print(f"\n✅ Results saved to {output_file}\n")


if __name__ == "__main__":
    main()
