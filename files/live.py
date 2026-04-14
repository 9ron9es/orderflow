"""
runners/live.py — Production live runner.

Usage
-----
python -m orderflow.nautilus.runners.live --config ~/.config/orderflow/live.yaml

Or in code:
    from orderflow.nautilus.runners.live import run_live
    run_live("~/.config/orderflow/live.yaml")
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

log = logging.getLogger(__name__)


def run_live(config_path: str) -> None:
    """
    Boot the full live stack:
    1. Load config
    2. Create Nautilus LiveNode
    3. Attach Binance adapter
    4. Add OrderflowStrategy
    5. Run (blocks until Ctrl-C or kill switch)
    """
    from nautilus_trader.config import (
        InstrumentProviderConfig,
        LiveExecEngineConfig,
        LoggingConfig,
        TradingNodeConfig,
    )
    from nautilus_trader.live.node import TradingNode

    try:
        from nautilus_trader.adapters.binance.config import (
            BinanceFuturesDataClientConfig,
            BinanceFuturesExecClientConfig,
        )
        from nautilus_trader.adapters.binance.factories import (
            BinanceLiveDataClientFactory,
            BinanceLiveExecClientFactory,
        )
    except ImportError:
        log.error("Binance adapter not installed. pip install nautilus_trader[binance]")
        sys.exit(1)

    from orderflow.nautilus.config.loader import load_orderflow_config
    from orderflow.nautilus.strategy.orderflow_strategy import OrderflowStrategy

    cfg = load_orderflow_config(config_path)

    # Determine testnet vs live
    testnet = "TESTNET" in (cfg.__dict__.get("binance_environment") or "").upper()

    data_client_cfg = BinanceFuturesDataClientConfig(
        api_key=None,           # reads BINANCE_API_KEY env var
        api_secret=None,        # reads BINANCE_API_SECRET env var
        instrument_provider=InstrumentProviderConfig(load_all=True),
        testnet=testnet,
    )
    exec_client_cfg = BinanceFuturesExecClientConfig(
        api_key=None,
        api_secret=None,
        instrument_provider=InstrumentProviderConfig(load_all=True),
        testnet=testnet,
    )

    node_cfg = TradingNodeConfig(
        trader_id="ORDERFLOW-001",
        logging=LoggingConfig(log_level="INFO"),
        exec_engine=LiveExecEngineConfig(reconciliation=True),
        data_clients={"BINANCE": data_client_cfg},
        exec_clients={"BINANCE": exec_client_cfg},
        strategies=[
            dict(
                strategy_path="orderflow.nautilus.strategy.orderflow_strategy:OrderflowStrategy",
                config_path="orderflow.nautilus.config.schema:OrderflowStrategyConfig",
                config=cfg,
            )
        ],
    )

    node = TradingNode(config=node_cfg)
    node.add_data_client_factory("BINANCE", BinanceLiveDataClientFactory)
    node.add_exec_client_factory("BINANCE", BinanceLiveExecClientFactory)
    node.build()

    log.info("Starting live node (testnet=%s)...", testnet)
    try:
        node.run()
    except KeyboardInterrupt:
        log.info("Keyboard interrupt — shutting down cleanly.")
    finally:
        node.dispose()


def run_backtest(config_path: str, data_path: str) -> None:
    """
    Run a deterministic backtest over a tick CSV/Parquet.
    Uses tick-time (not wall-clock) so candle boundaries are reproducible.
    """
    from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
    from nautilus_trader.config import LoggingConfig
    from nautilus_trader.model.enums import OmsType, AccountType
    from nautilus_trader.model.currencies import USDT
    from nautilus_trader.model.objects import Money

    from orderflow.nautilus.config.loader import load_orderflow_config
    from orderflow.nautilus.strategy.orderflow_strategy import OrderflowStrategy

    cfg = load_orderflow_config(config_path)

    engine = BacktestEngine(config=BacktestEngineConfig(
        logging=LoggingConfig(log_level="WARNING"),
    ))

    engine.add_venue(
        venue=cfg.instrument_id.venue,
        oms_type=OmsType.NETTING,
        account_type=AccountType.MARGIN,
        base_currency=None,
        starting_balances=[Money(10_000.0, USDT)],
    )

    # Add data
    data_file = Path(data_path)
    if data_file.suffix == ".parquet":
        import pandas as pd
        from nautilus_trader.persistence.wranglers import TradeTickDataWrangler
        df = pd.read_parquet(data_file)
        # (Instrument must already be added to engine before wrangling)
        # engine.add_data(wrangler.process(df))
        pass  # Caller should add instrument + wrangled data here

    strategy = OrderflowStrategy(cfg)
    engine.add_strategy(strategy)
    engine.run()

    stats = engine.get_result()
    log.info("Backtest complete: %s", stats)
    return stats


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description="Orderflow live/backtest runner")
    parser.add_argument("--config", required=True, help="Path to YAML config")
    parser.add_argument("--mode", choices=["live", "backtest"], default="live")
    parser.add_argument("--data", help="Tick data file (backtest only)")
    args = parser.parse_args()

    if args.mode == "live":
        run_live(args.config)
    else:
        if not args.data:
            parser.error("--data required for backtest mode")
        run_backtest(args.config, args.data)
