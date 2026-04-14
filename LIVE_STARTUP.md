# 🚀 LIVE DEMO - CORRECTED STARTUP GUIDE

**Fix Applied:** Module import path issue resolved with wrapper script  
**Status:** ✅ Ready to trade

---

## Step 1: Set Binance Demo Credentials

```bash
cd /home/adem/orderflow

# Set your Binance Demo API credentials
export BINANCE_DEMO_API_KEY="your_demo_api_key_here"
export BINANCE_DEMO_API_SECRET="your_demo_api_secret_here"

# Verify they're set:
echo $BINANCE_DEMO_API_KEY
echo $BINANCE_DEMO_API_SECRET
```

⚠️ **Important:** Get free DEMO credentials from [Binance](https://testnet.binancefuture.com) (no real money required)

---

## Step 2: Launch Live Trader (Terminal 1)

```bash
cd /home/adem/orderflow

# Run the live trader
python run_live.py \
    --config nautilus/config/profiles/live.yaml \
    --trader-id LIVE-DEMO-001
```

**Expected Output:**
```
[INFO] Loading config: nautilus/config/profiles/live.yaml
[INFO] Starting live node (testnet=True)...
[INFO] Connecting to Binance DEMO...
[INFO] [HEARTBEAT] Strategy running...
[INFO] [DATA] Received trade tick: ts=1712343215000, price=42500.5, qty=100, side=BUY
[INFO] [DATA] Adding tick to engine: ts=..., price=..., qty=..., side=...
[INFO] [DATA] Received order book deltas: ...
... (continuous stream of events)
```

**What's happening:**
- Connecting to Binance DEMO testnet
- Subscribing to BTCUSDT-PERP live tick stream
- Receiving order book updates
- Evaluating signals in real-time
- Logging events to `orderflow/logs/metrics/orderflow_metrics_YYYY-MM-DD.jsonl`

**Keep this terminal running** - it's your live trader. Stop with `Ctrl+C`.

---

## Step 3: Monitor Metrics (Terminal 2 - Optional)

In a new terminal:

```bash
cd /home/adem/orderflow

# Watch events being logged in real-time
tail -f orderflow/logs/metrics/orderflow_metrics_*.jsonl | jq .
```

**Expected Output:**
```json
{"ts": 1712343215000, "event": "entry_rejected", "data": {"failed": ["no_signal"], "long_signals": 0, "short_signals": 0}}
{"ts": 1712343215100, "event": "entry_rejected", "data": {"failed": ["no_signal"]}}
{"ts": 1712343215200, "event": "entry_rejected", "data": {"failed": ["stale_tick"]}}
{"ts": 1712343250000, "event": "entry_signal", "data": {"side": "BUY", "label": "imbalance_continuation_long", "price": 42510.5, "qty": "2.5", "notional_usdt": 106275.0, "confidence": 0.85}}
{"ts": 1712343500000, "event": "position_closed", "data": {"realized_pnl": 125.50, "consecutive_losses": 0, "daily_pnl_pct": 1.25}}
```

**What's shown:**
- `entry_rejected` - Signal evaluation failed (shows reason in "failed" list)
- `entry_signal` - Signal passed all checks, order submitted
- `position_closed` - Position closed, PnL locked in

---

## Step 4: Launch Dashboard (Terminal 3)

In another new terminal:

```bash
cd /home/adem/orderflow

# Wait for live trader to connect (10 seconds), then launch dashboard
sleep 10 && python dashboard_v2.py --refresh 0.5
```

**Expected Display (live-updating every 0.5 seconds):**

```
╭────────────────────────────────────────────────────────────────────╮
│                  ⚡ Orderflow Signal Loop                           │
├────────────────────────────────────────────────────────────────────┤
│ Status  [FLAT]           Entries  0      Exits  0   Win rate  —    │
│ Eval rate  42.3/s        Wins  0         Losses  0  Gross PnL  +0  │
╰────────────────────────────────────────────────────────────────────╯

╭────────────────────────────────────────────────────────────────────╮
│              Rejection Reasons (Last 50)                            │
├──────────┬────────────────────┬──────────────────┬────────┤
│ Time     │ Rejected Reason(s) │ Details          │ Count  │
├──────────┼────────────────────┼──────────────────┼────────┤
│ 14:32:45 │ no_signal          │ L: 0 S: 0 EQ: 10K│ 23     │
│ 14:32:44 │ stale_tick         │ —                │ 1      │
│ 14:32:43 │ spread_depth       │ —                │ 1      │
╰──────────┴────────────────────┴──────────────────┴────────╯

╭────────────────────────────────────────────────────────────────────╮
│                        Entry Orders                                 │
├──────────┬──────┬──────────┬────────┬────────────────────┬─────────┤
│ Time     │ Side │ Price    │ Qty    │ Notional (USDT)    │ Signal  │
├──────────┼──────┼──────────┼────────┼────────────────────┼─────────┤
│ 14:33:12 │ BUY  │ 42510.50 │ 2.5    │ 106,276.25         │ imbal.. │
╰──────────┴──────┴──────────┴────────┴────────────────────┴─────────╯

╭────────────────────────────────────────────────────────────────────╮
│                        Exit Orders                                  │
├──────────┬────────────────────┬──────────┤
│ Time     │ Reason             │ PnL      │
├──────────┼────────────────────┼──────────┤
│ 14:33:45 │ stoploss           │ -50.25   │
╰──────────┴────────────────────┴──────────╯
```

**What you're seeing:**
- **Eval rate** (42.3/s): Signal evaluations happening in real-time
- **Rejection reasons**: Why signals are rejected (mostly "no_signal" is normal)
- **Entries/Exits**: Trade prices, quantities, PnL
- **Position state**: [FLAT] = no position, [IN POSITION] = position open
- **PnL totals**: Cumulative profit/loss

---

## 📊 What to Expect

### First 30 Seconds
- Eval rate shows activity (e.g., "42.3/s")
- Rejection reasons accumulate ("no_signal" will dominate)
- No entries yet (waiting for signal conditions)

### After 1-5 Minutes
- Signal fires → entry order submitted to Binance DEMO
- Dashboard shows: **Entries: 1**
- Position opens as **[IN POSITION]**
- Metrics file shows new `entry_signal` event

### After Position Opens
- Eval continues (checking for exit conditions)
- Stoploss or trailing stop may trigger
- Dashboard shows: **Exits: 1**
- Metrics file shows `position_closed` event with realized PnL
- Gross PnL updates

### Continuous Operation
- Eval cycles repeat continuously
- New signals fire periodically (or rarely, depending on conditions)
- Metrics file grows with new events
- Dashboard updates every 0.5 seconds in real-time

---

## ✅ Success Checklist

✓ **Terminal 1 (Trader):**
- Continuous `[INFO] [DATA] Received trade tick` lines
- No errors or exceptions
- Status shows "Running"

✓ **Terminal 2 (Metrics):**
- Metrics file created for today: `orderflow_metrics_2026-04-05.jsonl`
- New events appearing every few seconds (~5-20 per second typical)
- Events are valid JSON

✓ **Terminal 3 (Dashboard):**
- Dashboard displays without errors
- Eval rate > 0 (shows activity)
- Updates every 0.5 seconds
- Rejection reasons accumulate

---

## 🔧 Troubleshooting

### Issue: "ModuleNotFoundError: No module named 'nautilus'"
**Solution:** Use the wrapper script instead:
```bash
python run_live.py --config nautilus/config/profiles/live.yaml
```

### Issue: "Connection timeout"
**Solution:** 
- Verify API credentials: `echo $BINANCE_DEMO_API_KEY`
- Binance API may be slow; wait 30 seconds
- Check internet connection
- Try again: `Ctrl+C` then restart

### Issue: "No ticks received for 60 seconds"
**Solution:**
- Normal if market is quiet
- Check metrics file: `tail orderflow/logs/metrics/orderflow_metrics_*.jsonl`
- Should see entry_rejected events even without ticks

### Issue: "Dashboard shows no data"
**Solution:**
- Wait 10+ seconds after starting trader
- Check metrics file exists: `ls orderflow/logs/metrics/`
- Verify file has events: `tail orderflow/logs/metrics/orderflow_metrics_*.jsonl`
- Launch dashboard with correct log dir if needed

### Issue: "No entry signals in 10+ minutes"
**Solution:**
- Normal! Most periods don't trigger signals
- Check rejection reasons - may all be "no_signal"
- Adjust thresholds in `nautilus/config/profiles/live.yaml` if desired
- Wait longer - signals may fire when conditions align

---

## 📝 Config Parameters (live.yaml)

Can be adjusted in `nautilus/config/profiles/live.yaml`:

```yaml
# Signal trigger thresholds
signal:
  imbalance_threshold: 0.25      # Delta imbalance to trigger (0.0-1.0)
  cvd_smoothing: 5               # CVD EMA period
  absorption_min: 0.15           # Directional absorption threshold
  large_trade_pct: 0.90          # Top 10% = "large"

# Risk limits
risk:
  max_position_fraction: 0.25    # Max 25% of equity per trade
  max_daily_loss_pct: 3.0        # Stop if -3% daily
  max_consecutive_losses: 3      # Stop after 3 losses in a row
  max_spread_bps: 20.0           # Reject if spread > 20 bps

# Execution settings
execution:
  use_market_entries: true       # Market orders (instant fills)
  stoploss_pct: 0.02             # Hard stop at -2%
  trailing_trigger_pct: 0.015    # Trail when +1.5%
```

---

## 🎯 Stop Live Trading

**In Terminal 1:**
```bash
Ctrl+C
```

Graceful shutdown - dashboards and metrics remain available for review.

---

## 📚 Quick Reference

| Command | Purpose |
|---------|---------|
| `python run_live.py --config nautilus/config/profiles/live.yaml` | Start live trader |
| `tail -f orderflow/logs/metrics/orderflow_metrics_*.jsonl \| jq .` | Monitor events |
| `python dashboard_v2.py --refresh 0.5` | Launch dashboard |
| `python dashboard.py --refresh 0.5` | Alternative dashboard (v1) |
| `Ctrl+C` | Stop current process |

---

## ✨ You're Ready!

Three terminals, three commands, live trading with live dashboard.

**Start with:**
```bash
# Terminal 1
cd /home/adem/orderflow
export BINANCE_DEMO_API_KEY=your_key
export BINANCE_DEMO_API_SECRET=your_secret
python run_live.py --config nautilus/config/profiles/live.yaml --trader-id LIVE-DEMO-001

# Terminal 2 (optional)
cd /home/adem/orderflow && tail -f orderflow/logs/metrics/orderflow_metrics_*.jsonl | jq .

# Terminal 3
sleep 10 && cd /home/adem/orderflow && python dashboard_v2.py --refresh 0.5
```

Monitor the dashboards and metrics as the system trades live on Binance DEMO! 🚀

