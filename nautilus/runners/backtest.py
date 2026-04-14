#!/usr/bin/env python3
"""
Backtest orderflow strategy from tick Parquet (``tick_recorder.py`` output).

Example::

    python -m orderflow.nautilus.runners.backtest \\
        --config orderflow/nautilus/config/profiles/backtest.yaml \\
        --parquet ./ticks/BTCUSDT/2024-01-15.parquet
"""

from __future__ import annotations

import argparse
from pathlib import Path

from nautilus_trader.adapters.binance import BINANCE_VENUE
from nautilus_trader.backtest.config import BacktestEngineConfig
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.currencies import USDT
from nautilus_trader.model.enums import AccountType
from nautilus_trader.model.enums import BookType
from nautilus_trader.model.enums import OmsType
from nautilus_trader.model.identifiers import TraderId
from nautilus_trader.model.objects import Money
from nautilus_trader.test_kit.providers import TestInstrumentProvider

from nautilus.config.loader import load_orderflow_config
from nautilus.config.schema import orderflow_strategy_config_from_stack
from nautilus.data.ticks import parquet_ticks_to_trade_ticks
from nautilus.strategy.orderflow_strategy import OrderflowStrategy


def run_backtest(
    *,
    config_path: Path,
    parquet_path: Path,
    trader_id: str = "BACKTESTER-ORDERFLOW-001",
) -> None:
    stack_cfg = load_orderflow_config(config_path)
    strat_cfg = orderflow_strategy_config_from_stack(stack_cfg)

    instrument = TestInstrumentProvider.btcusdt_perp_binance()

    engine_cfg = BacktestEngineConfig(
        trader_id=TraderId(trader_id),
        logging=LoggingConfig(log_level="INFO", log_colors=True, use_pyo3=True),
    )
    engine = BacktestEngine(config=engine_cfg)

    engine.add_venue(
        venue=BINANCE_VENUE,
        oms_type=OmsType.NETTING,
        book_type=BookType.L1_MBP,
        account_type=AccountType.MARGIN,
        base_currency=None,
        starting_balances=[Money(100_000.0, USDT)],
    )

    engine.add_instrument(instrument)

    ticks = parquet_ticks_to_trade_ticks(parquet_path, instrument)
    if not ticks:
        raise SystemExit(f"No trade ticks loaded from {parquet_path}")
    engine.add_data(ticks)

    strategy = OrderflowStrategy(config=strat_cfg)
    engine.add_strategy(strategy=strategy)

    engine.run()

    print(engine.trader.generate_account_report(BINANCE_VENUE))
    print(engine.trader.generate_order_fills_report())
    print(engine.trader.generate_positions_report())

    engine.reset()
    engine.dispose()


def main() -> None:
    p = argparse.ArgumentParser(description="Orderflow backtest (NautilusTrader)")
    p.add_argument("--config", type=Path, required=True)
    p.add_argument("--parquet", type=Path, required=True, help="Parquet file from tick_recorder")
    p.add_argument("--trader-id", default="BACKTESTER-ORDERFLOW-001")
    args = p.parse_args()
    run_backtest(
        config_path=args.config,
        parquet_path=args.parquet,
        trader_id=args.trader_id,
    )


if __name__ == "__main__":
    main()
