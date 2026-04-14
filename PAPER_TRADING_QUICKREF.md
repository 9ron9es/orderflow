# Paper Trading — Quick Reference

## One-Liner Commands

```bash
# Test everything works
python test_paper_trader.py

# View current state
python paper_trading_integration.py

# Reset to fresh start
python paper_trading_integration.py reset

# Run example simulation
python example_paper_trading_integration.py

# Show integration pattern
python example_paper_trading_integration.py --integrate
```

## Import & Use (Python)

```python
from paper_trader import PaperTrader

# Initialize
paper = PaperTrader()

# Buy
paper.place_order("BTCUSDT", "BUY", 100, 45000)

# Sell
paper.place_order("BTCUSDT", "SELL", 0, 46000)

# Check stats
print(paper.get_stats())

# Get current state
state = paper.get_account_state()
```

## Files

| File | Purpose |
|------|---------|
| `paper_trader.py` | Core class (implement here) |
| `paper_trades.json` | State file (don't edit) |
| `test_paper_trader.py` | Unit tests |
| `example_paper_trading_integration.py` | Integration examples |
| `paper_trading_integration.py` | CLI tools & guide |
| `PAPER_TRADING_GUIDE.md` | Full documentation |

## Key Methods

```python
paper.place_order(symbol, side, usdt_amount, current_price)
# Returns: {"status": "FILLED", "position": {...}}
#       or {"error": "Insufficient balance", ...}

paper.mark_to_market(symbol, current_price)
# Returns: unrealized_pnl (float)

paper.get_stats()
# Returns: {"total_trades": N, "win_rate": "X%", "total_pnl_usdt": X, "roi": "X%"}

paper.get_account_state()
# Returns: {"balance_usdt": X, "open_positions": M, "open_trades_count": N, "closed_trades_count": M}
```

## Integration Pattern

```python
# Instead of real Binance orders:
# client.order_market_buy(symbol=symbol, quoteOrderQty=usdt)

# Use paper trading:
from paper_trader import PaperTrader
paper = PaperTrader()
result = paper.place_order(symbol, "BUY", usdt, price)
```

## State Structure

```json
{
  "account": {
    "balance_usdt": 1000.0,
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
      "opened_at": "2026-04-07T12:00:00",
      "pnl": 500.0
    }
  },
  "closed_trades": [...],
  "pending_orders": []
}
```

## Common Issues

| Problem | Solution |
|---------|----------|
| "Insufficient balance" | Check `paper_trading_integration.py` for balance |
| State not persisting | Verify `paper_trades.json` exists |
| Want fresh start | Run `python paper_trading_integration.py reset` |

## Binance API Safety

✓ Safe to use (read-only):
- `get_symbol_ticker()`
- `get_klines()`
- `get_orderbook()`
- `get_trades()`

✗ Blocked by paper trading:
- `order_market_buy()`
- `order_market_sell()`
- `order_limit_buy()`
- `order_limit_sell()`

## Metrics Explained

| Metric | Meaning |
|--------|---------|
| `total_trades` | Number of closed positions |
| `win_rate` | % of trades that made profit |
| `total_pnl_usdt` | Sum of all realized P&L |
| `roi` | Return on initial capital |
| `current_balance` | Account value now |

## Tips

1. **Test first**: Run `test_paper_trader.py` to verify
2. **Check daily**: Use `paper_trading_integration.py` for status
3. **Reset often**: Start fresh with `--reset` for clean testing
4. **Multi-symbol**: Paper trading handles multiple positions
5. **Real prices**: Always use current Binance prices
6. **Read-only**: Binance API is never modified

---

See `PAPER_TRADING_GUIDE.md` for full documentation.
