# ✅ LIVE DEMO - ISSUE FIXED & READY TO RUN

**Problem:** `ModuleNotFoundError: No module named 'orderflow.nautilus'`  
**Cause:** Python module path issue when using `-m` flag  
**Solution:** ✅ Created wrapper script `run_live.py`  
**Status:** 🟢 **READY TO LAUNCH**

---

## 🚀 How to Start (Copy & Paste)

### Terminal 1: Start Live Trader
```bash
cd /home/adem/orderflow

# Set Binance Demo credentials (get free from testnet.binancefuture.com)
export BINANCE_DEMO_API_KEY="your_demo_api_key"
export BINANCE_DEMO_API_SECRET="your_demo_api_secret"

# Run the live trader (wrapper script handles imports)
python run_live.py \
    --config nautilus/config/profiles/live.yaml \
    --trader-id LIVE-DEMO-001
```

### Terminal 2: Monitor Metrics (Optional)
```bash
cd /home/adem/orderflow
tail -f orderflow/logs/metrics/orderflow_metrics_*.jsonl | jq .
```

### Terminal 3: Launch Dashboard
```bash
sleep 10 && cd /home/adem/orderflow && python dashboard_v2.py --refresh 0.5
```

---

## 🔧 The Fix

**Created:** `run_live.py` wrapper script

**What it does:**
```python
import sys
from pathlib import Path

# Add current directory to Python path
sys.path.insert(0, str(Path(__file__).parent))

# Now nautilus can be imported
from nautilus.runners.live import run_live, main

if __name__ == "__main__":
    main()
```

**Why it works:**
- Adds `/home/adem/orderflow` to Python path
- Makes `nautilus` importable from local directory
- Avoids the need for `python -m orderflow.nautilus...` syntax

---

## ✅ What to Expect

### Immediate (after running):
```
[INFO] Loading config: nautilus/config/profiles/live.yaml
[INFO] Starting live node (testnet=True)...
[INFO] Connecting to Binance DEMO...
[INFO] [HEARTBEAT] Strategy running. Last tick: NEVER
[INFO] [DATA] Received trade tick: ts=1712343215000, price=42500.5, qty=100, side=BUY
[INFO] [DATA] Adding tick to engine: ts=1712343215000, price=42500.5, qty=100, side=buy
```

### Dashboard (Terminal 3):
```
Status  [FLAT]    Entries  0   Exits  0   Win rate  —
Eval rate  42.3/s Wins  0      Losses  0   Gross PnL  +0

Rejection Reasons:
no_signal          23
stale_tick         1
```

### Metrics file (Terminal 2):
```json
{"ts": 1712343215000, "event": "entry_rejected", "data": {"failed": ["no_signal"]}}
{"ts": 1712343250000, "event": "entry_signal", "data": {"side": "BUY", ...}}
```

---

## 📊 Data Flow (Live Mode)

```
Binance DEMO API (WebSocket)
    ↓ Real-time ticks
OrderflowStrategy.on_trade_tick()
    ↓ Process tick
OrderflowFeatureEngine.add_tick()
    ↓ Compute snapshot
SignalRegistry.evaluate_long/short()
    ↓ Check signal conditions
PreTradeRiskStack (all checks)
    ↓ If all pass: submit order
MetricsLogger.log_event()
    ↓ Write to JSONL
Dashboard polls JSONL every 0.5 sec
    ↓ Display live updates
```

---

## 📁 Files Created/Modified

| File | Purpose |
|------|---------|
| `run_live.py` | ✨ **NEW** - Wrapper script (fixes module import issue) |
| `LIVE_STARTUP.md` | ✨ **NEW** - Comprehensive startup guide |
| `QUICK_START.sh` | ✨ **NEW** - Copy-paste commands |
| `nautilus/config/profiles/live.yaml` | Config (unchanged, already correct) |
| `nautilus/runners/live.py` | Source (unchanged, still works) |
| `dashboard_v2.py` | Dashboard (unchanged, still works) |

---

## 🎯 Key Commands

```bash
# Start live trader
python run_live.py --config nautilus/config/profiles/live.yaml

# Monitor metrics
tail -f orderflow/logs/metrics/orderflow_metrics_*.jsonl | jq .

# Launch dashboard
python dashboard_v2.py --refresh 0.5

# Alternative: use dashboard v1
python dashboard.py --refresh 0.5

# Stop trader (in Terminal 1)
Ctrl+C

# Soft kill switch (prevents entry orders)
touch orderflow/.kill_switch

# Re-enable (remove kill switch)
rm orderflow/.kill_switch
```

---

## 🔍 Architecture (Same for Both Backtest & Live)

```
Data Source (Parquet or API)
    ↓
OrderflowFeatureEngine (identical logic)
    ↓
SignalRegistry (identical logic)
    ↓
PreTradeRiskStack (identical logic)
    ↓
Execution (different: backtest vs live)
    ↓
MetricsLogger → JSONL (identical format)
    ↓
Dashboards v1/v2 (identical display)
```

**Only the data source differs.** Everything else is identical between backtest and live.

---

## 📊 Live vs Backtest Timeline

### Backtest (for reference)
```
python run_backtest.py
  → Loads all ~1000 ticks instantly
  → Evaluates all in 10-30 seconds
  → Completes, shows final stats
  → Done
```

### Live (what you're doing now)
```
python run_live.py
  → Connects to Binance DEMO
  → Receives ticks in real-time (1-100/sec)
  → Evaluates continuously (every 200ms throttle)
  → Trades execute in real-time (seconds to minutes)
  → Runs indefinitely (until Ctrl+C)
```

---

## 🎯 Success Indicators

✅ **Terminal 1 (Trader):**
- No errors
- `[DATA] Received trade tick` lines appearing
- Status shows "Running"

✅ **Terminal 2 (Metrics):**
- File created: `orderflow_metrics_2026-04-05.jsonl`
- Events appending (5-20 lines per second)
- Valid JSON format

✅ **Terminal 3 (Dashboard):**
- No rendering errors
- Eval rate > 0 (shows activity)
- Updates every 0.5 seconds
- Rejection reasons accumulate

---

## 🚨 If Something Goes Wrong

| Error | Solution |
|-------|----------|
| `ModuleNotFoundError: No module named 'nautilus'` | Use `python run_live.py` instead of `python -m` |
| `Connection timeout` | Wait 30 sec, verify credentials, try again |
| `No ticks for 60 sec` | Normal if market quiet; check metrics file |
| `Dashboard shows no data` | Wait 10+ sec after starting trader |
| `[ERROR] Config not found` | Use full path: `--config /home/adem/orderflow/nautilus/config/profiles/live.yaml` |

---

## 📚 Documentation

| File | Content |
|------|---------|
| `LIVE_STARTUP.md` | Full startup guide with examples |
| `QUICK_START.sh` | Copy-paste commands for 3 terminals |
| `LIVE_DEMO_GUIDE.md` | Comprehensive live mode documentation |
| `LIVE_QUICK_START.md` | Quick reference |
| `BACKTEST_VS_LIVE.md` | Detailed comparison |
| `LIVE_STATUS.txt` | Status summary |

---

## 🎉 You're Ready!

**Next steps:**
1. Get Binance Demo credentials (free, testnet)
2. Set env vars: `BINANCE_DEMO_API_KEY`, `BINANCE_DEMO_API_SECRET`
3. Run 3 commands in 3 terminals
4. Watch the dashboard as it trades live on testnet

**All systems operational.** The wrapper script fixes the module import issue. You can now start live trading! 🚀

