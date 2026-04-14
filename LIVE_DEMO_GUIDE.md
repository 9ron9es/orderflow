# LIVE DEMO DATA FLOW GUIDE

**Status:** ✅ **LIVE TRADING MODE** (NOT Backtest)  
**Environment:** DEMO (Binance Testnet)  
**Date:** April 5, 2026

---

## 🔴 LIVE DATA FLOW (NOT Backtest)

```
┌──────────────────────────────────────────────────────────────┐
│          BINANCE DEMO API (Real-Time Market Data)            │
│  - TradeTicks (actual ticks)                                │
│  - OrderBookDeltas (L2 order book updates)                  │
│  - Account state (positions, balance, fills)                │
└────────────────────┬─────────────────────────────────────────┘
                     │
            ┌────────▼─────────────┐
            │  BinanceLiveData     │
            │  ClientFactory       │
            │  (Nautilus)          │
            │  - API connection    │
            │  - WebSocket stream  │
            │  - Real-time parsing │
            └────────┬─────────────┘
                     │
         ┌───────────┴───────────┐
         │                       │
    ┌────▼──────┐        ┌──────▼────┐
    │ TradeTick │        │OrderBook   │
    │ Events    │        │Deltas      │
    │(live)     │        │(live)      │
    └────┬──────┘        └──────┬─────┘
         │                      │
         └──────────┬───────────┘
                    │
        ┌───────────▼──────────────┐
        │  OrderflowStrategy       │
        │  (same as backtest)      │
        │ - on_trade_tick()        │
        │ - on_order_book_deltas() │
        │ - _maybe_evaluate()      │
        └───────────┬──────────────┘
                    │
         ┌──────────┴───────────┐
         │                      │
    ┌────▼────────┐      ┌─────▼─────┐
    │Feature      │      │Signal      │
    │Engine       │      │Registry    │
    │             │      │            │
    │• add_tick() │      │•eval_long()│
    │•snapshot()  │      │•eval_short│
    └────┬────────┘      └─────┬─────┘
         │                     │
         └──────────┬──────────┘
                    │
         ┌──────────▼──────────┐
         │ Risk Stack Checks   │
         │ - Daily loss        │
         │ - Consecutive loss  │
         │ - Spread/depth      │
         │ - Stale tick        │
         └──────────┬──────────┘
                    │
              ┌─────▼─────┐
              │ DECISION  │
              ├───────────┤
              │Signal OK? │
              └─┬───────┬─┘
                │       │
           YES  │       │ NO
             ┌──▼──┐   ┌──▼──┐
             │Send │   │Reject
             │Order│   │(log)
             └──┬──┘   └──┬──┘
                │         │
                └─────┬───┘
                      │
        ┌─────────────▼──────────────┐
        │  MetricsLogger             │
        │  .log_event()              │
        │  → JSONL file              │
        │  orderflow/logs/metrics/   │
        │  orderflow_metrics_        │
        │  2026-04-05.jsonl          │
        └─────────────┬──────────────┘
                      │
        ┌─────────────▼──────────────┐
        │   Dashboard (v1 or v2)     │
        │   - Polls JSONL file       │
        │   - Tails new lines        │
        │   - Renders live TUI       │
        │                            │
        │   Shows:                   │
        │   • Eval rate (evals/sec)  │
        │   • Rejection reasons      │
        │   • Entry/exit prices      │
        │   • PnL updates            │
        │   • Position state         │
        └────────────────────────────┘
```

---

## 🟢 LIVE STARTUP COMMAND

```bash
python -m orderflow.nautilus.runners.live \
    --config nautilus/config/profiles/live.yaml \
    --trader-id LIVE-DEMO-001
```

### What This Does:
1. **Loads config** from `nautilus/config/profiles/live.yaml`
2. **Connects to Binance DEMO** (testnet - real API but fake money)
3. **Creates TradingNode** (Nautilus live framework)
4. **Subscribes to:**
   - BTCUSDT-PERP live ticks
   - L2 order book updates (5 levels deep)
5. **Initializes OrderflowStrategy**
6. **Starts event loop** (blocks, streams data in real-time)

---

## ⚙️ LIVE CONFIG (`nautilus/config/profiles/live.yaml`)

```yaml
binance_environment: DEMO          ← ✅ Testnet (recommended for dev)

signal:
  imbalance_threshold: 0.25        ← Delta imbalance to trigger
  cvd_smoothing: 5                 ← CVD EMA period
  absorption_min: 0.15             ← Directional absorption threshold
  large_trade_pct: 0.90            ← Top 10% = "large"

risk:
  max_position_fraction: 0.25      ← Max 25% of equity per trade
  max_notional_usdt: 10000         ← Hard cap per order
  max_daily_loss_pct: 3.0          ← Stop trading if -3% daily
  max_consecutive_losses: 3        ← Stop after 3 losses in a row
  max_spread_bps: 20.0             ← Reject if spread > 20 bps
  stale_tick_ms: 5000.0            ← Reject if no tick for 5 sec

execution:
  use_market_entries: true         ← Market orders (instant fills)
  entry_post_only: false           ← Allow market makers
  stoploss_pct: 0.02               ← Hard stop at -2%
  trailing_trigger_pct: 0.015      ← Trail when +1.5%
  trailing_offset_pct: 0.01        ← Trail by -1%

log_metrics: true                  ← ✅ Log all events to JSONL
metrics_dir: orderflow/logs/metrics ← Log location
```

---

## 📊 DATA FLOW DURING LIVE TRADING

### Timeline Example:

```
T+0ms    Binance DEMO API emits tick: price=42500, qty=100, side=BUY
         └─ TradeTick event → on_trade_tick()
         └─ Engine adds tick
         └─ _maybe_evaluate() triggered

T+1ms    Feature Engine computes snapshot
         └─ Delta: +100
         └─ CVD: +100
         └─ Imbalance: +1.0

T+2ms    Signal Registry evaluates
         └─ Long signal: NO (CVD not rising yet)
         └─ SHORT signal: NO
         └─ Result: None

T+2ms    MetricsLogger writes event
         └─ Event: entry_rejected
         └─ Reason: no_signal
         └─ File grows: +1 line ✅

T+10ms   Binance DEMO API emits: price=42510, qty=200, side=BUY
         └─ Same cycle: add_tick → evaluate → log

T+100ms  Signal FIRES! (CVD rising + imbalance high)
         └─ Signal: BUY (imbalance_continuation_long)
         └─ Risk checks: ALL PASS
         └─ Order submitted

T+100ms  MetricsLogger writes event
         └─ Event: entry_signal
         └─ Price: 42510
         └─ Qty: 2.5
         └─ Notional: 106,275 USDT
         └─ File grows: +1 line ✅

T+200ms  Binance DEMO fills order at 42510
         └─ Position opens: +2.5 BTC
         └─ Dashboard shows: [IN POSITION]

T+500ms  Price rises to 42550 (unrealized +100 USDT)
         └─ Trailing stop activates (trigger at +1.5% = 42,663)

T+2000ms Price drops to 42480 (stoploss triggered)
         └─ Exit order submitted at market
         └─ Position closes

T+2010ms Binance DEMO fills exit order
         └─ Position closed: +2.5 BTC @ 42480
         └─ Realized PnL: -75 USDT

T+2010ms MetricsLogger writes event
         └─ Event: position_closed
         └─ realized_pnl: -75
         └─ File grows: +1 line ✅

[Dashboard polling every 0.5 sec]
         └─ Reads new events
         └─ Updates totals:
            • Entries: 1
            • Exits: 1
            • Losses: 1
            • Gross PnL: -75
            • Win rate: 0%
```

---

## 🚀 HOW TO RUN LIVE DEMO

### Terminal 1: Start Live Trading Node
```bash
cd /home/adem/orderflow

# Ensure API credentials (will read from env)
export BINANCE_DEMO_API_KEY=your_demo_key
export BINANCE_DEMO_API_SECRET=your_demo_secret

# Start the live runner
python -m orderflow.nautilus.runners.live \
    --config nautilus/config/profiles/live.yaml \
    --trader-id LIVE-DEMO-001

# Expected output:
# [INFO] Loading config: nautilus/config/profiles/live.yaml
# [INFO] Starting live node (testnet=True)...
# [INFO] Connecting to Binance DEMO...
# [INFO] Subscribing to BTCUSDT-PERP ticks + order book...
# [INFO] Strategy running. Waiting for signals...
# [INFO] [DATA] Received trade tick: ts=1712343215000, price=42500.5, qty=100, side=BUY
# [INFO] [DATA] Received order book deltas...
# ... (continuous stream of events)
```

### Terminal 2: Monitor Metrics (Optional)
```bash
# Watch events being written
tail -f orderflow/logs/metrics/orderflow_metrics_*.jsonl | jq .

# Expected output:
# {"ts": 1712343215000, "event": "entry_rejected", "data": {"failed": ["no_signal"]}}
# {"ts": 1712343216000, "event": "entry_rejected", "data": {"failed": ["no_signal"]}}
# {"ts": 1712343250000, "event": "entry_signal", "data": {"side": "BUY", "price": 42510.5, ...}}
# {"ts": 1712343500000, "event": "position_closed", "data": {"realized_pnl": 125.75, ...}}
```

### Terminal 3: Launch Dashboard
```bash
# Start after live runner has begun (give it 5 seconds to connect)
sleep 5 && python dashboard_v2.py --refresh 0.5

# Expected display:
# ╭─────────────────────────────────────────────────────────────╮
# │ Status  [IN POSITION]  Entries  1   Exits  0   Win rate  —  │
# │ Eval rate  42.3/s      Wins  0      Losses  0   Gross PnL  0 │
# ╰─────────────────────────────────────────────────────────────╯
#
# ╭─────────────────────────────────────────────────────────────╮
# │                    Rejection Reasons (Last 50)              │
# ├──────────┬────────────────────┬───────────────────┬─────────┤
# │ Time     │ Rejected Reason(s) │ Details           │ Count   │
# ├──────────┼────────────────────┼───────────────────┼─────────┤
# │ 14:32:45 │ no_signal          │ L:0 S:0 EQ:10000  │ 23      │
# │ 14:32:50 │ stale_tick         │ —                 │ 1       │
# ╰──────────┴────────────────────┴───────────────────┴─────────╯
```

---

## 📡 LIVE DATA SOURCES

### 1. **Binance DEMO API** (Primary)
- **Connection:** WebSocket + REST
- **Data:**
  - Trade ticks (every micro-trade)
  - Order book updates (L2 snapshots)
  - Account balance/positions
  - Fill confirmations
  
- **Credentials:**
  - Read from env: `BINANCE_DEMO_API_KEY`, `BINANCE_DEMO_API_SECRET`
  - OR `BINANCE_API_KEY`, `BINANCE_API_SECRET` for live

### 2. **OrderflowFeatureEngine** (Processing)
- Ticks → CandleFlow (5-minute bars)
- Features computed incrementally
- Same as backtest, but on LIVE TICKS

### 3. **MetricsLogger** (Logging)
- Same JSONL format as backtest
- File: `orderflow/logs/metrics/orderflow_metrics_YYYY-MM-DD.jsonl`
- Appends real-time events

### 4. **Dashboards** (Display)
- Both v1 and v2 work identically
- Poll the same metrics log
- Display live-updated state

---

## ✅ VERIFICATION CHECKLIST (LIVE)

### Pre-Launch
- [ ] Binance DEMO API credentials set in env
  ```bash
  export BINANCE_DEMO_API_KEY=...
  export BINANCE_DEMO_API_SECRET=...
  ```
- [ ] Live config exists: `nautilus/config/profiles/live.yaml`
- [ ] Metrics directory writable: `orderflow/logs/metrics/`

### Launch Terminal 1
- [ ] Live runner starts without errors
- [ ] Connects to Binance DEMO
- [ ] Subscribes to BTCUSDT-PERP
- [ ] Receives tick events (see [DATA] log lines)

### Launch Terminal 2 (Optional)
- [ ] Metrics file created for today's date
- [ ] Events appending in real-time
- [ ] entry_rejected events frequent (expected)

### Launch Terminal 3
- [ ] Dashboard connects to metrics file
- [ ] Shows "Eval rate: X.X/s" (active evaluation)
- [ ] Rejection reasons update
- [ ] No errors in TUI rendering

### During Trading
- [ ] Entry signals fire periodically (or reasonably often)
- [ ] Orders submit to Binance DEMO
- [ ] Positions open/close as expected
- [ ] PnL totals update
- [ ] Dashboard shows real-time stats

---

## 🔧 DEBUGGING LIVE MODE

### Issue: "No API credentials found"
```bash
# Check if env vars are set
echo $BINANCE_DEMO_API_KEY
echo $BINANCE_DEMO_API_SECRET

# If empty, set them
export BINANCE_DEMO_API_KEY=your_key
export BINANCE_DEMO_API_SECRET=your_secret
```

### Issue: "Connection timeout"
```bash
# Binance DEMO may be slow; allow 10-30 seconds to connect
# Check logs for connection retry messages
# Verify internet connection
```

### Issue: "No ticks received"
```bash
# May take 10+ seconds for first tick
# Monitor with: tail -f orderflow/logs/metrics/orderflow_metrics_*.jsonl
# Check subscription: should see [DATA] Received trade tick logs
```

### Issue: "Dashboard shows no data"
```bash
# Wait for first event (may take 30+ seconds in low-volume periods)
# Check metrics file manually:
tail orderflow/logs/metrics/orderflow_metrics_*.jsonl | jq .
# If empty, ticks aren't flowing yet
```

### Issue: "Orders not submitting"
```bash
# Check risk stack reasons in rejection log
# Common: "no_signal", "spread_depth", "daily_loss"
# Verify signal parameters in live.yaml are reasonable
```

---

## 📊 LIVE VS BACKTEST COMPARISON

| Aspect | Backtest | Live Demo |
|--------|----------|-----------|
| **Data** | Parquet file (historical) | Binance API (real-time) |
| **Speed** | Fast (all at once) | Real-time (tick-by-tick) |
| **Start Command** | `python run_backtest.py` | `python -m orderflow.nautilus.runners.live --config ...` |
| **Connection** | File I/O | WebSocket + REST |
| **Ticks/sec** | All ticks processed instantly | Depends on market activity (~1-100/sec) |
| **Signal Timing** | Immediate (compressed timeline) | Real-time delays matter |
| **Execution** | Simulated fills | DEMO account (fake money, real API) |
| **Metrics Log** | Same JSONL format | Same JSONL format |
| **Dashboard** | Same (v1 or v2) | Same (v1 or v2) |

---

## 🎯 NEXT STEPS

### 1. **Prepare Credentials**
```bash
export BINANCE_DEMO_API_KEY=your_demo_api_key
export BINANCE_DEMO_API_SECRET=your_demo_api_secret
```

### 2. **Launch Live Node (Terminal 1)**
```bash
cd /home/adem/orderflow
python -m orderflow.nautilus.runners.live \
    --config nautilus/config/profiles/live.yaml \
    --trader-id LIVE-DEMO-001
```

### 3. **Monitor Metrics (Terminal 2 - Optional)**
```bash
tail -f orderflow/logs/metrics/orderflow_metrics_*.jsonl | jq .
```

### 4. **Launch Dashboard (Terminal 3)**
```bash
sleep 10 && python dashboard_v2.py --refresh 0.5
```

### 5. **Observe**
- Dashboard updates in real-time as live events occur
- Signal evaluation rate shows activity
- Rejection reasons aggregate
- Entries/exits accumulate
- PnL updates on closed positions

---

## ✨ KEY DIFFERENCES FROM ANALYSIS DOCS

The previous analysis was for **BACKTEST** (historical data).  
This guide is for **LIVE DEMO** (real-time data from Binance).

**Same components:**
- ✅ OrderflowFeatureEngine
- ✅ SignalRegistry
- ✅ RiskStack
- ✅ MetricsLogger
- ✅ Dashboards

**Different sources:**
- ❌ NOT Parquet files
- ✅ Binance DEMO API (WebSocket ticks)

**Data flow is identical**, just source is real-time instead of historical.

