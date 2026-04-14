#!/usr/bin/env python3
"""
Live trading node: Binance USD-M + orderflow strategy.

Requires API credentials (env ``BINANCE_API_KEY`` / ``BINANCE_API_SECRET`` or
``BINANCE_DEMO_API_KEY`` / ``BINANCE_DEMO_API_SECRET`` for DEMO).

Example::

    export BINANCE_DEMO_API_KEY=...
    export BINANCE_DEMO_API_SECRET=...
    python -m orderflow.nautilus.runners.live \\
        --config orderflow/nautilus/config/profiles/live.yaml
"""

from __future__ import annotations

import argparse
from pathlib import Path

from nautilus_trader.adapters.binance import BINANCE
from nautilus_trader.adapters.binance import BinanceAccountType
from nautilus_trader.adapters.binance import BinanceDataClientConfig
from nautilus_trader.adapters.binance import BinanceExecClientConfig
from nautilus_trader.adapters.binance import BinanceInstrumentProviderConfig
from nautilus_trader.adapters.binance import BinanceLiveDataClientFactory
from nautilus_trader.adapters.binance import BinanceLiveExecClientFactory
from nautilus_trader.adapters.binance.common.enums import BinanceEnvironment
from nautilus_trader.config import CacheConfig
from nautilus_trader.config import LiveExecEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.config import TradingNodeConfig
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.identifiers import TraderId

from nautilus.config.loader import load_orderflow_config
from nautilus.config.schema import orderflow_strategy_config_from_stack
from nautilus.strategy.orderflow_strategy import OrderflowStrategy


def _env_from_str(name: str | None) -> BinanceEnvironment:
    if not name:
        return BinanceEnvironment.LIVE
    upper = name.upper()
    if upper == "DEMO":
        return BinanceEnvironment.DEMO
    if upper == "TESTNET":
        return BinanceEnvironment.TESTNET
    return BinanceEnvironment.LIVE


def run_live(*, config_path: Path, trader_id: str = "LIVE-ORDERFLOW-001") -> None:
    stack = load_orderflow_config(config_path)
    strat_cfg = orderflow_strategy_config_from_stack(stack)
    env = _env_from_str(stack.binance_environment)

    node_cfg = TradingNodeConfig(
        trader_id=TraderId(trader_id),
        logging=LoggingConfig(log_level="DEBUG", log_colors=True, use_pyo3=True),
        exec_engine=LiveExecEngineConfig(
            reconciliation=True,
            purge_closed_orders_interval_mins=1,
            purge_closed_positions_interval_mins=1,
        ),
        cache=CacheConfig(timestamps_as_iso8601=True, flush_on_start=False),
        data_clients={
            BINANCE: BinanceDataClientConfig(
                api_key=None,
                api_secret=None,
                account_type=BinanceAccountType.USDT_FUTURES,
                environment=env,
                instrument_provider=BinanceInstrumentProviderConfig(
                    load_ids=frozenset({stack.instrument_id}),
                    query_commission_rates=True,
                ),
            ),
        },
        exec_clients={
            BINANCE: BinanceExecClientConfig(
                api_key=None,
                api_secret=None,
                account_type=BinanceAccountType.USDT_FUTURES,
                environment=env,
                instrument_provider=BinanceInstrumentProviderConfig(
                    load_ids=frozenset({stack.instrument_id}),
                    query_commission_rates=True,
                ),
                max_retries=3,
            ),
        },
        timeout_connection=120.0,
        timeout_reconciliation=30.0,
        timeout_portfolio=30.0,
        timeout_disconnection=30.0,
        timeout_post_stop=10.0,
    )

    node = TradingNode(config=node_cfg)
    strategy = OrderflowStrategy(config=strat_cfg)
    node.trader.add_strategy(strategy)

    node.add_data_client_factory(BINANCE, BinanceLiveDataClientFactory)
    node.add_exec_client_factory(BINANCE, BinanceLiveExecClientFactory)
    node.build()

    try:
        node.run()
    finally:
        node.dispose()


def main() -> None:
    p = argparse.ArgumentParser(description="Orderflow live trading (NautilusTrader)")
    p.add_argument("--config", type=Path, required=True)
    p.add_argument("--trader-id", default="LIVE-ORDERFLOW-001")
    args = p.parse_args()
    run_live(config_path=args.config, trader_id=args.trader_id)


if __name__ == "__main__":
    main()
