# Paper Trading Simulation Layer — User Guide

## Overview

The **paper trading simulation** allows your orderflow bot to:
- Use **real Binance API for market data only** (read-only)
- Execute orders in a **simulated environment** (no real money involved)
- Track P&L and statistics in a persistent JSON state file
- Test strategies risk-free before live trading

---

## Quick Start

### 1. Verify Installation

```bash
# Test paper trader is working
python test_paper_trader.py
```

Expected output:
```
✓ Initializing PaperTrader...
✓ ALL TESTS PASSED
```

### 2. Check Current State

```bash
# View trading summary
python paper_trading_integration.py
```

Output:
```
============================================================
PAPER TRADING SUMMARY
============================================================

Account Balance: $1002.02 USDT
Initial Balance: $1000.00 USDT

Open Positions (0):

Closed Trades (1):
  Total Trades: 1
  Win Rate: 100.0%
  Total PnL: $2.02
  ROI: 0.20%
```

### 3. Reset to Fresh Start

```bash
# Delete all previous trades and reset to $1000
python paper_trading_integration.py reset
```

---

## Architecture

```
Real Binance API (read-only)
    ↓
    ├─ get_klines()        [OK] ← Price history
    ├─ get_ticker()        [OK] ← Current price
    ├─ get_orderbook()     [OK] ← Market depth
    └─ get_trades()        [OK] ← Trade history
    
    ✗ order_market_buy()    [BLOCKED] — No real orders
    ✗ order_market_sell()   [BLOCKED] — No real orders

Paper Trading Engine (simulated)
    ↓
    ├─ place_order()      ← Simulated fill
    ├─ mark_to_market()   ← Unrealized PnL
    └─ get_stats()        ← Trading metrics
    
State Persistence
    ↓
    paper_trades.json     ← Survives restart
```

---

## Core Classes

### `PaperTrader`

Main simulation engine. Located in `paper_trader.py`.

#### Methods

| Method | Purpose | Example |
|--------|---------|---------|
| `place_order()` | Simulate market order | `paper.place_order("BTCUSDT", "BUY", 100, 45000)` |
| `mark_to_market()` | Update unrealized PnL | `paper.mark_to_market("BTCUSDT", 46000)` |
| `get_stats()` | Trading statistics | `stats = paper.get_stats()` |
| `get_account_state()` | Current balance/positions | `state = paper.get_account_state()` |

#### Example Usage

```python
from paper_trader import PaperTrader

# Initialize (loads existing state or creates fresh)
paper = PaperTrader()

# Open a position
result = paper.place_order(
    symbol="BTCUSDT",
    side="BUY",
    usdt_amount=100,
    current_price=45000.0
)
print(result)
# Output: {'status': 'FILLED', 'position': {...}}

# Check unrealized PnL
unrealized = paper.mark_to_market("BTCUSDT", 46000.0)
print(f"Unrealized: ${unrealized:.2f}")  # Output: Unrealized: $2.22

# Close position
result = paper.place_order(
    symbol="BTCUSDT",
    side="SELL",
    usdt_amount=0,
    current_price=46000.0
)
print(f"Realized PnL: ${result['trade']['pnl']:.2f}")

# Get performance stats
stats = paper.get_stats()
print(stats)
# Output: {'total_trades': 1, 'win_rate': '100.0%', 'total_pnl_usdt': 2.02, ...}
```

---

## State File Format

**File:** `paper_trades.json`

```json
{
  "account": {
    "balance_usdt": 1002.02,
    "initial_balance": 1000.0
  },
  "open_positions": {
    "ETHUSDT": {
      "symbol": "ETHUSDT",
      "side": "LONG",
      "entry_price": 2500.0,
      "qty": 0.04,
      "usdt_invested": 100.0,
      "fee_paid": 0.1,
      "opened_at": "2026-04-07T12:30:00",
      "pnl": 4.0
    }
  },
  "closed_trades": [
    {
      "symbol": "BTCUSDT",
      "side": "LONG",
      "entry_price": 45000.0,
      "exit_price": 46000.0,
      "qty": 0.00222222,
      "usdt_invested": 100.0,
      "pnl": 2.02,
      "opened_at": "2026-04-07T10:00:00",
      "closed_at": "2026-04-07T10:15:00"
    }
  ],
  "pending_orders": []
}
```

---

## Integration with Orderflow Strategy

### Option 1: External Test Script (Recommended)

Create a simple test that calls your orderflow signal logic and routes orders to paper trader:

```python
from paper_trader import PaperTrader
from nautilus_trader.adapters.binance import BinanceDataClientConfig
from binance.client import Client

# Setup
paper = PaperTrader()
binance_client = Client(api_key=KEY, api_secret=SECRET)

# Real loop
for symbol in ["BTCUSDT", "ETHUSDT"]:
    # Get live price (read-only from Binance)
    ticker = binance_client.get_symbol_ticker(symbol=symbol)
    current_price = float(ticker["price"])
    
    # Your signal logic (unchanged)
    signal = evaluate_orderflow_signal(symbol, current_price)
    
    # Route to paper trader instead of real execution
    if signal == "BUY":
        result = paper.place_order(symbol, "BUY", 50, current_price)
    elif signal == "SELL":
        result = paper.place_order(symbol, "SELL", 0, current_price)
    
    # Log
    print(f"{symbol} {signal}: {result}")

# Check stats
print(paper.get_stats())
```

### Option 2: Strategy-Level Hook (Advanced)

Override the OrderflowStrategy to intercept orders:

```python
from nautilus.strategy.orderflow_strategy import OrderflowStrategy
from paper_trader import PaperTrader
from nautilus_trader.model.enums import OrderSide

class PaperOrderflowStrategy(OrderflowStrategy):
    def __init__(self, config):
        super().__init__(config)
        self.paper = PaperTrader()
        self._paper_mode = True
        self._last_price = None
    
    def on_trade_tick(self, tick: TradeTick) -> None:
        self._last_price = float(tick.price)
        super().on_trade_tick(tick)
    
    def on_order_submitted(self, event: OrderSubmitted) -> None:
        if self._paper_mode:
            order = event.order
            side = "BUY" if order.side == OrderSide.BUY else "SELL"
            symbol = order.instrument_id.value
            qty_usdt = float(order.quantity) * self._last_price
            
            result = self.paper.place_order(symbol, side, qty_usdt, self._last_price)
            self.log.info(f"Paper: {result}")
        else:
            super().on_order_submitted(event)
```

---

## Command Reference

### View Summary
```bash
python paper_trading_integration.py
```

### Reset to Fresh Start
```bash
python paper_trading_integration.py reset
```

### Run Unit Tests
```bash
python test_paper_trader.py
```

### Interactive Python REPL
```bash
python -c "
from paper_trader import PaperTrader
p = PaperTrader()
p.place_order('BTCUSDT', 'BUY', 100, 45000)
print(p.get_stats())
"
```

---

## Key Features

| Feature | Detail |
|---------|--------|
| **Market Data** | Read-only from Binance (safe) |
| **Order Execution** | Simulated fills at market price |
| **Fee Simulation** | 0.1% per trade (Binance spot) |
| **State Persistence** | JSON file survives restarts |
| **Multi-symbol** | Track multiple positions simultaneously |
| **Unrealized PnL** | Mark-to-market on live prices |
| **Statistics** | Win rate, ROI, total trades |
| **Balance Checks** | Prevents overleveraged trades |

---

## Common Workflows

### Scenario 1: Test Signal Logic

```python
from paper_trader import PaperTrader

# Delete old trades
import os
os.remove("paper_trades.json")

paper = PaperTrader()

# Simulate a trade sequence
trades = [
    ("BTCUSDT", "BUY", 45000, 100),
    ("BTCUSDT", "SELL", 46000, 0),
    ("ETHUSDT", "BUY", 2500, 50),
    ("ETHUSDT", "SELL", 2600, 0),
]

for symbol, side, price, amount in trades:
    result = paper.place_order(symbol, side, amount, price)
    print(f"{symbol} {side} @ {price}: {result.get('status', result.get('error'))}")

# Final report
print("\n" + paper.get_stats())
```

### Scenario 2: Long-Running Live Simulation

```bash
# Reset
python paper_trading_integration.py reset

# Run your orderflow bot with paper trading interceptor
python your_live_script.py --paper-trading

# Check stats daily
python paper_trading_integration.py
```

### Scenario 3: Backtest with Paper Trading

The backtest runner can be configured to use paper trading state:

```bash
python run_backtest.py \
    --config nautilus/config/profiles/backtest.yaml \
    --use-paper-trading
```

---

## Troubleshooting

### Q: "Insufficient balance" error
**A:** Your account doesn't have enough USDT for the trade. Check `paper_trading_integration.py` output to see current balance.

### Q: Positions not persisting between runs
**A:** Check that `paper_trades.json` exists and is writable. Re-run `python test_paper_trader.py` to verify.

### Q: Want to start fresh
**A:** Delete the state file and reset:
```bash
rm paper_trades.json
python paper_trading_integration.py reset
```

### Q: How do I change initial balance?
**A:** Edit `paper_trader.py`, line ~26, change `1000.0` to your desired amount, then reset.

---

## Performance Metrics Explained

From `paper.get_stats()`:

```python
{
    "total_trades": 5,           # Number of closed trades
    "win_rate": "60.0%",         # % of profitable trades
    "total_pnl_usdt": 45.32,     # Sum of all realized P&L
    "current_balance": 1045.32,  # Account equity now
    "roi": "4.53%"               # Return on initial capital
}
```

---

## Safety Checks

✓ Binance API is read-only (market data only)
✓ No real orders are placed
✓ No real money is ever spent
✓ State is local (paper_trades.json)
✓ Can be reset anytime
✓ Fees are simulated (0.1%)
✓ Balance checks prevent losses exceeding account

---

## Next Steps

1. **Test the system**: Run `python test_paper_trader.py`
2. **Check your state**: Run `python paper_trading_integration.py`
3. **Simulate trades**: Use examples in this guide
4. **Integrate with orderflow**: Use Option 1 or 2 above
5. **Monitor performance**: Check stats daily
6. **Go live** (optional): Switch to real trading when confident

---

## Files Reference

| File | Purpose |
|------|---------|
| `paper_trader.py` | Core PaperTrader class |
| `paper_trades.json` | Persistent state |
| `test_paper_trader.py` | Unit tests |
| `paper_trading_integration.py` | Integration guide + CLI tools |

---

For questions or issues, check the code comments in `paper_trader.py` and `paper_trading_integration.py`.
