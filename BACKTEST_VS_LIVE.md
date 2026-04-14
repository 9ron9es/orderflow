# 📋 BACKTEST vs LIVE DEMO - COMPLETE COMPARISON

## Data Source

### Backtest
```
data.parquet
  ↓
BacktestEngine loads all ticks at once
  ↓
Strategy processes instantly (compressed timeline)
  ↓
All events emitted in seconds
```

### Live Demo
```
Binance DEMO API (WebSocket)
  ↓
TradeTicks stream in real-time
  ↓
Strategy processes tick-by-tick (real timeline)
  ↓
Events emit continuously (depends on market activity)
```

---

## Command to Start

### Backtest
```bash
python run_backtest.py
```

### Live Demo
```bash
python -m orderflow.nautilus.runners.live \
    --config nautilus/config/profiles/live.yaml \
    --trader-id LIVE-DEMO-001
```

---

## Timeline & Duration

### Backtest
- **Duration:** 10-30 seconds (entire backtest complete)
- **Ticks processed:** All ~50-1000 ticks in one go
- **Evals/sec:** Very high (compressed time)
- **Position holds:** Microseconds to seconds (sped up)

### Live Demo
- **Duration:** Indefinite (runs until stopped)
- **Ticks processed:** 1-100 per second (depends on market activity)
- **Evals/sec:** ~1-50 (real-time throttle)
- **Position holds:** Real time (seconds, minutes, hours)

---

## Data Quality

### Backtest
- ✅ All historical ticks present
- ✅ No gaps or missing data
- ✅ No latency variation
- ❌ Can't test live edge cases

### Live Demo
- ✅ Real Binance API ticks
- ✅ Real order book updates
- ✅ Real execution simulation
- ⚠️ May have gaps (low volume periods)
- ⚠️ Network latency varies
- ✅ Tests real-world edge cases

---

## Feature Engine Behavior

### Backtest
```
Tick 1 → Candle 1 → Snapshot → Signal eval
Tick 2 → Candle 1 → Snapshot → Signal eval
Tick 3 → Candle 2 → Snapshot → Signal eval
... (all in ~10 seconds total)
```

### Live Demo
```
Tick 1 → Candle 1 → Snapshot → Signal eval (T+0ms)
... (wait 1-2 seconds) ...
Tick 2 → Candle 1 → Snapshot → Signal eval (T+1000ms)
... (wait 5 seconds) ...
Tick 3 → Candle 2 → Snapshot → Signal eval (T+6000ms)
... (continuous, real timeline)
```

---

## Metrics Logging

### Backtest
```json
orderflow/logs/metrics/orderflow_metrics_2026-04-05.jsonl
[
  {"ts": 1712343215000, "event": "entry_rejected", "data": {...}},
  {"ts": 1712343215100, "event": "entry_rejected", "data": {...}},
  {"ts": 1712343215250, "event": "entry_signal", "data": {...}},
  {"ts": 1712343215400, "event": "position_closed", "data": {...}},
  ... (all in 30 seconds)
]
```

### Live Demo
```json
orderflow/logs/metrics/orderflow_metrics_2026-04-05.jsonl
[
  {"ts": 1712343215000, "event": "entry_rejected", "data": {...}},
  {"ts": 1712343215100, "event": "entry_rejected", "data": {...}},
  ... (5 second gap - no ticks) ...
  {"ts": 1712343220000, "event": "entry_rejected", "data": {...}},
  {"ts": 1712343220150, "event": "entry_signal", "data": {...}},
  ... (2 minute gap - position holding) ...
  {"ts": 1712343340000, "event": "position_closed", "data": {...}},
  ... (continuous, real timeline)
]
```

---

## Dashboard Display

### Backtest
```
╭────────────────────────────────────────────────────╮
│ Status  [FLAT]    Entries  5   Exits  5   Win  80% │
│ Eval rate  0/s    Wins  4      Losses  1   PnL  +425 │
╰────────────────────────────────────────────────────╯

(Updated instantly as backtest runs)
(All trades complete in 10-30 seconds)
(Dashboard shows final stats when backtest ends)
```

### Live Demo
```
╭────────────────────────────────────────────────────╮
│ Status  [IN POSITION]  Entries  1   Exits  0   —   │
│ Eval rate  42.3/s      Wins  0      Losses  0   +0   │
╰────────────────────────────────────────────────────╯

(Updates every 0.5 seconds)
(Position may stay open for minutes)
(Trades accumulate gradually over time)
```

---

## Rejection Reasons Frequency

### Backtest
```
no_signal:       92% (most evals)
stale_tick:      2% (rare, high activity)
spread_depth:    3% (realistic order book)
daily_loss:      0% (not reached)
other:           3%

All reasons appear within 10-30 seconds
```

### Live Demo
```
no_signal:       95%+ (continuous evaluation without triggers)
stale_tick:      1-3% (network gaps, slow periods)
spread_depth:    1-2% (bid-ask varies)
daily_loss:      0% (small account, slow drawdown)
other:           1%

Reasons accumulate over hours/days
```

---

## Entry Signal Frequency

### Backtest
- **Expected:** 1-10 entries in 30 seconds
- **Reason:** Compressed timeline, high tick density
- **Example:** 5 entries in 10 seconds = one per 2 seconds

### Live Demo
- **Expected:** 0-2 entries per hour (depends on conditions)
- **Reason:** Real timeline, sparse signal triggers
- **Example:** 1 entry every 30 minutes is normal

---

## Position Duration

### Backtest
```
Entry: T+10 seconds
Exit:  T+15 seconds
Duration: 5 seconds

Entry: T+20 seconds
Exit:  T+25 seconds
Duration: 5 seconds

(All positions very short, compressed timeline)
```

### Live Demo
```
Entry:  14:32:00
Exit:   14:35:45
Duration: 3 minutes 45 seconds

Entry:  15:10:00
Exit:   15:12:30
Duration: 2 minutes 30 seconds

(Positions can last minutes, hours, or longer)
```

---

## Account Performance Timeline

### Backtest
```
T+0s:    Start (balance: $10,000)
T+10s:   Entry 1 fired (entries: 1)
T+15s:   Exit 1 closed, PnL: +$50 (exits: 1, gross_pnl: +$50)
T+20s:   Entry 2 fired (entries: 2)
T+25s:   Exit 2 closed, PnL: -$30 (exits: 2, gross_pnl: +$20)
...
T+30s:   Backtest complete (5 trades, +$425 total)
```

### Live Demo
```
14:30:00: Start (balance: $10,000)
14:32:00: Entry 1 fired (entries: 1)
14:35:45: Exit 1 closed, PnL: +$50 (exits: 1, gross_pnl: +$50)
14:38:00: Entry 2 fired (entries: 2)
15:10:00: Exit 2 closed, PnL: -$30 (exits: 2, gross_pnl: +$20)
15:12:30: Entry 3 fired (entries: 3)
... (continues indefinitely)
```

---

## When to Use Which

### Use **Backtest** When:
- ✅ Testing strategy logic quickly
- ✅ Developing new signal modules
- ✅ Verifying data flow end-to-end
- ✅ High-volume testing (many scenarios)
- ✅ No API credentials required
- ❌ Not for realistic live testing

### Use **Live Demo** When:
- ✅ Realistic testing with real API
- ✅ Testing live edge cases (gaps, latency)
- ✅ Validating strategy performance in real market
- ✅ Monitoring with dashboards continuously
- ✅ API credentials available (free tier OK)
- ⚠️ Takes longer to see results (real timeline)

---

## Architecture: Same for Both

```
┌─────────────────────────────────────────┐
│          Data Source                    │
│  Backtest: Parquet    Live: Binance API │
└──────────────┬────────────────────────────┘
               │
    ┌──────────▼──────────────┐
    │ OrderflowFeatureEngine  │ (identical)
    └──────────┬──────────────┘
               │
    ┌──────────▼──────────────┐
    │ SignalRegistry          │ (identical)
    └──────────┬──────────────┘
               │
    ┌──────────▼──────────────┐
    │ PreTradeRiskStack       │ (identical)
    └──────────┬──────────────┘
               │
    ┌──────────▼──────────────┐
    │ MetricsLogger (JSONL)   │ (identical)
    └──────────┬──────────────┘
               │
    ┌──────────▼──────────────┐
    │ Dashboard v1 / v2       │ (identical)
    └─────────────────────────┘
```

**Only data source changes. Everything else is identical.**

---

## Troubleshooting Guide

### Backtest Issues

| Problem | Cause | Solution |
|---------|-------|----------|
| Backtest stuck | File read hanging | Ctrl+C, check data.parquet exists |
| No events logged | Ticks exhausted quickly | Adjust thresholds in config |
| Dashboard shows nothing | Backtest finished too fast | Wait 5 sec after starting, launch dashboard sooner |

### Live Demo Issues

| Problem | Cause | Solution |
|---------|-------|----------|
| Connection timeout | API credentials invalid | Verify `$BINANCE_DEMO_API_KEY` set |
| No ticks for 60 sec | Network issue or API down | Wait, retry, check internet |
| Dashboard stale for 5 min | No signals firing | Normal; wait longer or adjust thresholds |
| Eval rate = 0 | Live runner crashed | Check Terminal 1 logs, restart |

---

## Summary

```
              BACKTEST              LIVE DEMO
Data Source   Parquet (historical)  Binance API (real-time)
Start Time    10-30 sec             Indefinite
Trades/Hour   10-100 (compressed)   0-2 (real-time)
Position Dur  Seconds               Minutes+
Testing Type  Logic validation      Realistic trading
Recommendation Initial dev/testing   Ongoing validation
```

Both use the **same core system** (feature engine, signals, risk checks, logging, dashboards).

**Backtest** = Fast feedback loop  
**Live Demo** = Realistic validation

