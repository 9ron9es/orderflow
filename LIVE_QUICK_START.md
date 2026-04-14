# ⚡ LIVE DEMO - QUICK START (3 Terminals)

## Pre-Flight Check
```bash
# Verify API credentials are set
echo $BINANCE_DEMO_API_KEY  # Should not be empty
echo $BINANCE_DEMO_API_SECRET

# If empty, set them:
export BINANCE_DEMO_API_KEY="your_demo_key"
export BINANCE_DEMO_API_SECRET="your_demo_secret"
```

---

## Terminal 1: START LIVE TRADING NODE
```bash
cd /home/adem/orderflow

python -m orderflow.nautilus.runners.live \
    --config nautilus/config/profiles/live.yaml \
    --trader-id LIVE-DEMO-001
```

**What to expect:**
```
[INFO] Loading config: nautilus/config/profiles/live.yaml
[INFO] Starting live node (testnet=True)...
[INFO] Connecting to Binance DEMO...
[INFO] [HEARTBEAT] Strategy running...
[INFO] [DATA] Received trade tick: ts=1712343215000, price=42500.5, qty=100, side=BUY
[INFO] [DATA] Received order book deltas: ...
... (continuous stream of events)
```

**Keep this terminal running** - it's the live trader.

---

## Terminal 2: MONITOR METRICS (Optional but Recommended)
```bash
tail -f orderflow/logs/metrics/orderflow_metrics_*.jsonl | jq .
```

**What to expect:**
```json
{"ts": 1712343215000, "event": "entry_rejected", "data": {"failed": ["no_signal"]}}
{"ts": 1712343216000, "event": "entry_rejected", "data": {"failed": ["no_signal"]}}
{"ts": 1712343250000, "event": "entry_signal", "data": {"side": "BUY", "price": 42510.5, "qty": "2.5", ...}}
{"ts": 1712343500000, "event": "position_closed", "data": {"realized_pnl": 125.75, ...}}
```

**New events appear continuously** as trader operates.

---

## Terminal 3: LAUNCH DASHBOARD
```bash
# Wait a few seconds for live node to connect, then:
sleep 10 && cd /home/adem/orderflow && python dashboard_v2.py --refresh 0.5
```

**Expected display (live-updating):**

```
╔════════════════════════════════════════════════════════════════════════╗
║                    ⚡ Orderflow Signal Loop                            ║
╠════════════════════════════════════════════════════════════════════════╣
│ Status  [FLAT]          Entries  0        Exits  0      Win rate  —   │
│ Eval rate  42.3/s       Wins  0           Losses  0      Gross PnL  0 │
╚════════════════════════════════════════════════════════════════════════╝

╔════════════════════════════════════════════════════════════════════════╗
║              Rejection Reasons (Last 50)                               ║
╠──────────┬────────────────────┬────────────────┬──────────┤
│ Time     │ Rejected Reason(s) │ Details        │ Count    │
├──────────┼────────────────────┼────────────────┼──────────┤
│ 14:32:45 │ no_signal          │ L: 0 S: 0 EQ:10000 │ 23       │
│ 14:32:44 │ stale_tick         │ —              │ 1        │
╚──────────┴────────────────────┴────────────────┴──────────╝

╔════════════════════════════════════════════════════════════════════════╗
║                        Entry Orders                                    ║
╠──────────┬──────┬────────────┬────────┬──────────────────────────┬─────┤
│ Time     │ Side │ Price      │ Qty    │ Notional (USDT)          │ sig │
├──────────┼──────┼────────────┼────────┼──────────────────────────┼─────┤
│ 14:33:12 │ BUY  │ 42510.50   │ 2.5    │ 106,276.25               │ imb │
╚──────────┴──────┴────────────┴────────┴──────────────────────────┴─────╝
```

**Dashboard updates every 0.5 seconds** - watch metrics change live!

---

## What You're Monitoring

### Dashboard Shows
- **Eval rate (evals/sec)**: How many signal evaluations per second (shows system is active)
- **Rejection reasons**: Why signals are rejected (most will be "no_signal")
- **Entry orders**: Price, quantity, notional, signal module
- **Exit orders**: Reason, PnL
- **Position state**: [FLAT] (no position) or [IN POSITION]
- **Gross PnL**: Total profit/loss

### Metrics File Shows
- **entry_rejected**: Signal evaluation failed → log event
- **entry_signal**: Signal passed, order submitted → log event
- **position_closed**: Trade completed, PnL locked → log event
- **Timestamps**: Unix milliseconds (every event)

---

## Expected Behavior

### First 30 seconds
- Eval rate will show (e.g., "42.3/s")
- Rejection reasons accumulate ("no_signal" will dominate)
- No entries yet (waiting for signal conditions)

### After ~1-5 minutes
- Signal fires → entry order submitted
- Dashboard shows: **Entries: 1**, position opens as **[IN POSITION]**
- Metrics file has new entry_signal event

### After position opens
- Signal evaluation continues (check for exit)
- Stoploss or trailing stop may trigger
- Dashboard shows: **Exits: 1**, PnL updates

### Continuous operation
- Eval cycles repeat continuously
- New signals fire periodically (or not, depending on conditions)
- Metrics file grows with new events
- Dashboard updates in real-time

---

## Troubleshooting

### "Connection timeout" (Terminal 1)
→ Binance API slow or credentials invalid  
→ Wait 30 seconds, then Ctrl+C and retry  
→ Verify env vars: `echo $BINANCE_DEMO_API_KEY`

### "No ticks received" (Terminal 1)
→ Normal at first; may take 10+ seconds for first tick  
→ Wait, don't panic  
→ Check Terminal 2 metrics file; should have entry_rejected events

### "Dashboard shows no data" (Terminal 3)
→ Metrics file not created yet; wait 10+ seconds  
→ Check Terminal 2: `tail -f orderflow/logs/metrics/orderflow_metrics_*.jsonl`  
→ If nothing appears, live runner not generating events yet

### "No entry signals firing"
→ This is normal! Most evals will be rejected ("no_signal")  
→ Wait 5-10 minutes; signal conditions may not align  
→ Check config thresholds in live.yaml if suspicions arise

---

## Stop Live Trading

**In Terminal 1:**
```bash
Ctrl+C   # Graceful shutdown
```

Dashboard and metrics continue to show historical data.  
Restart anytime with same command.

---

## Data Files Created

```
orderflow/logs/metrics/
└── orderflow_metrics_2026-04-05.jsonl  ← Live events appended here
    (grows continuously during trading)

Example line:
{"ts": 1712343215000, "event": "entry_rejected", "data": {"failed": ["no_signal"]}}
```

---

## Key Config Parameters (live.yaml)

| Setting | Value | Meaning |
|---------|-------|---------|
| `binance_environment` | DEMO | Use testnet (fake money) |
| `eval_throttle_ms` | 200 | Max 5 evals/sec (5000/200) |
| `max_position_fraction` | 0.25 | Max 25% of equity per trade |
| `max_daily_loss_pct` | 3.0 | Stop if -3% daily |
| `stale_tick_ms` | 5000.0 | Reject if no tick for 5 sec |
| `use_market_entries` | true | Market orders (instant fill) |
| `stoploss_pct` | 0.02 | Hard stop at -2% |

---

## Success Criteria

✅ **You know it's working when:**
- Terminal 1: Continuous [DATA] lines (ticks arriving)
- Terminal 2: Metrics file grows with new events every few seconds
- Terminal 3: Dashboard shows non-zero eval rate (e.g., "42.3/s")
- Terminal 3: Rejection reasons accumulate (mostly "no_signal")
- After 5-10 min: Entry signal fires, position opens, dashboard updates

✅ **Data is flowing correctly when:**
- Entries count increments when entry_signal events arrive
- Exits count increments when position_closed events arrive
- Gross PnL updates with realized_pnl values
- Eval rate > 0 (system evaluating signals continuously)

---

## That's It! 🚀

Three terminals, three commands, live trading with live dashboard.

Monitor Terminals 1 & 2 for raw data/logs.  
Watch Terminal 3 dashboard for real-time metrics.

Good luck!

