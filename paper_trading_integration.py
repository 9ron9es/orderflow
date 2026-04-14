"""
paper_trading_integration.py — Integration guide for paper trading with Nautilus strategy.

This module provides integration hooks to enable paper trading simulation for the 
OrderflowStrategy while using Binance API for market data only (read-only).

Architecture:
    - Real Binance API: Market data, orderbook, ticks (read-only)
    - PaperTrader: Simulated order execution (paper_trades.json state)
    - Orderflow signals: Unchanged logic
    - Integration point: OrderflowStrategy or custom execution adapter

Usage (for Nautilus-based execution):
=======================================
The PaperTrader can be used in two ways:

1. EXTERNAL SIMULATION (recommended for testing):
   - Run backtest with paper trading disabled (uses real Binance data)
   - In live mode, replace the exec client with paper trading
   
2. STRATEGY-LEVEL HOOK (custom implementation):
   - Override on_order_submitted() in OrderflowStrategy
   - Route to PaperTrader instead of Binance exec client

Example: Strategy-level integration
====================================
from paper_trader import PaperTrader

class PaperOrderflowStrategy(OrderflowStrategy):
    def __init__(self, config):
        super().__init__(config)
        self.paper = PaperTrader()
        self._paper_mode = True  # Set to False for real trading
    
    def on_order_submitted(self, event: OrderSubmitted) -> None:
        if self._paper_mode:
            order = event.order
            current_price = self._last_price  # Track from on_trade_tick
            
            # Convert Nautilus OrderSide to paper trader side
            side = "BUY" if order.side == OrderSide.BUY else "SELL"
            symbol = order.instrument_id.value
            
            # For entry orders
            if order.order_type == OrderType.MARKET:
                qty_usdt = float(order.quantity) * current_price
                result = self.paper.place_order(symbol, side, qty_usdt, current_price)
                
                # Log result (e.g., fill status, balance update)
                if result.get("error"):
                    self.log.error(f"Paper trade error: {result['error']}")
                else:
                    self.log.info(f"Paper trade {side}: {result}")

Data flow with paper trading:
=============================
1. Binance API (read-only)
   ├─ Ticker data
   ├─ Orderbook
   └─ Trade ticks
        ↓
2. Multi-TF Engine (unchanged)
   ├─ Liquidity heatmap
   ├─ Market structure
   └─ Signals
        ↓
3. OrderflowStrategy.on_signal() (unchanged)
   ├─ Noise filter
   ├─ Signal evaluation
   └─ Entry/exit decision
        ↓
4. [PAPER TRADING INTERCEPT]
   ├─ Intercept: self.submit_order() → paper.place_order()
   ├─ State: paper_trades.json (persistent)
   └─ Return: simulated fill/rejection
        ↓
5. Metrics & Analysis
   ├─ Trades logged
   ├─ PnL calculated
   └─ Stats: win_rate, ROI, etc.

Configuration options (future):
===============================
In live.yaml or backtest config:

trading_mode: "paper"  # or "live"

paper_trading:
  initial_balance_usdt: 1000.0
  fee_rate_bps: 10  # 0.1% = 10 bps
  slippage_bps: 5
  leverage: 1
  position_limit_usdt: 500  # max per trade

Paper trading JSON state:
=========================
{
  "account": {
    "balance_usdt": 950.0,
    "initial_balance": 1000.0
  },
  "open_positions": {
    "BTCUSDT": {
      "symbol": "BTCUSDT",
      "side": "LONG",
      "entry_price": 45000.0,
      "qty": 0.01,
      "usdt_invested": 450.0,
      "fee_paid": 0.45,
      "opened_at": "2026-04-07T12:30:00",
      "pnl": 500.0
    }
  },
  "closed_trades": [
    {
      "symbol": "ETHUSDT",
      "entry_price": 2500.0,
      "exit_price": 2550.0,
      "qty": 0.1,
      "pnl": 49.75,
      "opened_at": "2026-04-07T10:00:00",
      "closed_at": "2026-04-07T10:15:00"
    }
  ]
}

Key integration points:
=======================
1. /paper_trader.py
   - PaperTrader class (core simulation engine)
   - place_order() — simulates market fills
   - mark_to_market() — updates unrealized PnL
   - get_stats() — returns trading statistics

2. nautilus/strategy/orderflow_strategy.py (OPTIONAL)
   - Can add _paper_mode flag to switch execution
   - Or: Keep unchanged, use at runner level

3. nautilus/runners/live.py (OPTIONAL)
   - Can add --paper-trading flag
   - Routes to PaperTrader instead of Binance exec client

4. paper_trades.json
   - Persistent state file (survives between runs)
   - Updated on every order
   - Can be reset by deleting file

Best practices for testing:
===========================
1. Start fresh: delete paper_trades.json
2. Set small initial balance: 100-1000 USDT
3. Use 1h backtest data: verify signal logic first
4. Then run paper trading with live data
5. Compare PnL vs live (if applicable)
6. Never use paper trading API credentials for real trading

Limitations to note:
====================
- Fills are immediate at price (no slippage simulation yet)
- No partial fills or partial order cancellations
- No rejected orders (only balance check)
- No latency simulation
- Round numbers: quantities rounded to 8 decimals
- Fee: Fixed 0.1% (Binance spot rate)
"""

from paper_trader import PaperTrader
from pathlib import Path
import json


def reset_paper_trades():
    """Reset the paper trading state to initial balance."""
    paper = PaperTrader()
    initial_state = {
        "account": {"balance_usdt": 1000.0, "initial_balance": 1000.0},
        "open_positions": {},
        "closed_trades": [],
        "pending_orders": []
    }
    paper._save(initial_state)
    print("✓ Paper trading state reset to 1000 USDT")


def load_paper_trades():
    """Load current paper trading state."""
    paper = PaperTrader()
    return paper.state


def print_paper_trading_summary():
    """Print trading summary to console."""
    paper = PaperTrader()
    state = paper.state
    
    print("\n" + "="*60)
    print("PAPER TRADING SUMMARY")
    print("="*60)
    
    print(f"\nAccount Balance: ${state['account']['balance_usdt']:.2f} USDT")
    print(f"Initial Balance: ${state['account']['initial_balance']:.2f} USDT")
    
    if state['open_positions']:
        print(f"\nOpen Positions ({len(state['open_positions'])}):")
        for symbol, pos in state['open_positions'].items():
            print(f"  {symbol}:")
            print(f"    Entry: {pos['entry_price']:.2f} | Qty: {pos['qty']:.6f}")
            print(f"    Unrealized PnL: ${pos['pnl']:.2f}")
    
    if state['closed_trades']:
        print(f"\nClosed Trades ({len(state['closed_trades'])}):")
        stats = paper.get_stats()
        print(f"  Total Trades: {stats.get('total_trades', 0)}")
        print(f"  Win Rate: {stats.get('win_rate', 'N/A')}")
        print(f"  Total PnL: ${stats.get('total_pnl_usdt', 0):.2f}")
        print(f"  ROI: {stats.get('roi', 'N/A')}")
    
    print("\n" + "="*60 + "\n")


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "reset":
        reset_paper_trades()
    else:
        print_paper_trading_summary()
