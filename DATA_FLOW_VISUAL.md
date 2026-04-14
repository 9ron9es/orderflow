# 🔍 ORDERFLOW DATA FLOW - VISUAL MAP

## The Complete Picture

```
                        ┏━━━━━━━━━━━━━━━━━━━━━━━━┓
                        ┃   PARQUET TICKS        ┃
                        ┃  (data.parquet)        ┃
                        ┃   158 KB ~50+ ticks    ┃
                        ┗━━━━┳━━━━━━━━━━━━━━━━━━┛
                             │
                    ┌────────▼───────────┐
                    │ BacktestEngine     │
                    │ (Nautilus)         │
                    │ • Loads data       │
                    │ • Emits events     │
                    └────────┬───────────┘
                             │
              ┌──────────────┴──────────────┐
              │                             │
    ┌─────────▼──────────┐      ┌──────────▼─────────┐
    │  TradeTick Events  │      │ OrderBookDeltas    │
    │  (per tick)        │      │ (per book update)  │
    └─────────┬──────────┘      └──────────┬─────────┘
              │                             │
              └──────────────┬──────────────┘
                             │
            ┌────────────────▼────────────────┐
            │  OrderflowStrategy              │
            │  .on_trade_tick()               │
            │  .on_order_book_deltas()        │
            │  ._maybe_evaluate()             │
            └──────────┬──────────────────────┘
                       │
        ┌──────────────┼──────────────┐
        │              │              │
   ┌────▼────┐   ┌────▼────┐   ┌────▼────┐
   │ Feature │   │  Signal  │   │   Risk  │
   │ Engine  │   │Registry  │   │  Stack  │
   └────┬────┘   └────┬────┘   └────┬────┘
        │              │              │
        │ Computes:    │ Evaluates:   │ Checks:
        │ • Delta      │ • Long       │ • Kill switch
        │ • CVD        │ • Short      │ • Stale tick
        │ • Imbal.     │              │ • Daily loss
        │ • Absorp.    │ Returns:     │ • Spread/depth
        │ • Diverge    │ EntrySignal  │ • Consec. losses
        │              │ or None      │ • Leverage
        │              │              │
        └──────────────┼──────────────┘
                       │
                ┌──────▼──────┐
                │ DECISION    │
                ├─────────────┤
                │ Signal OK?  │
                └──┬──────┬───┘
                   │      │
              YES  │      │  NO
                ┌──▼──┐  ┌──▼──────┐
                │Send │  │ Reject  │
                │Order│  │(log)    │
                └──┬──┘  └──┬──────┘
                   │        │
                ┌──▼────────▼──┐
                │ MetricsLogger │
                │  .log_event() │
                └──────┬────────┘
                       │
         ┌─────────────▼─────────────┐
         │      JSONL LOG FILE       │
         │   orderflow_metrics_      │
         │   2026-04-05.jsonl        │
         │                           │
         │ {"ts": ..., "event":      │
         │  "entry_signal", ...}     │
         │                           │
         │ {"ts": ..., "event":      │
         │  "entry_rejected", ...}   │
         │                           │
         │ {"ts": ..., "event":      │
         │  "position_closed", ...}  │
         └────────┬──────┬───────────┘
                  │      │
        ┌─────────▼──┐   │
        │Dashboard v1│   │
        │ dashboard  │   │
        │    .py     │   │
        └────────────┘   │
                         │
                ┌────────▼──────────┐
                │Dashboard v2       │
                │dashboard_v2.py    │
                │(focused view)     │
                └───────────────────┘
                
         Both display:
         ✓ Entry/exit counts
         ✓ PnL metrics
         ✓ Position state
         ✓ Rejection reasons
         ✓ Eval rate (v2)
         ✓ Risk status
```

---

## Data Flow Sequence (During Backtest)

```
TIME    COMPONENT              ACTION                    LOG EVENT
────────────────────────────────────────────────────────────────
T+0ms   Backtest              Load parquet ticks
        
T+1ms   Engine                Tick 1: price=42500, qty=100, side=BUY
                              add_tick() → _maybe_evaluate()

T+1ms   Feature Engine        compute_snapshot()
                              → Delta: +100, CVD: +100, Imb: +1.0
                              
T+1ms   Signal Registry       evaluate_long(snapshot)
                              → No signal (CVD not rising yet)
                              
T+1ms   MetricsLogger         entry_rejected              ✓ LOGGED
                              {"failed": ["no_signal"]}

T+5ms   Backtest              Tick 2: price=42505, qty=200, side=BUY
                              add_tick() → _maybe_evaluate()

T+5ms   Feature Engine        compute_snapshot()
                              → Delta: +300, CVD: +300, Imb: +1.0
                              
T+5ms   Signal Registry       evaluate_long(snapshot)
                              → SIGNAL! (CVD rising + imbalance high)
                              
T+5ms   Risk Stack            check_* passes all
                              
T+5ms   OrderflowStrategy     submit_order()
                              
T+5ms   MetricsLogger         entry_signal               ✓ LOGGED
                              {"side": "BUY", "price": 42505, ...}

T+10ms  Backtest              Tick 3: price=42510, qty=50, side=SELL
                              add_tick() → _maybe_evaluate()

...     (continued evaluation cycles)

T+200ms Backtest              Order fills at 42506
                              Position opens: +100 contracts
                              
T+500ms Backtest              Price moves to 42550
                              Unrealized PnL: +4400 USDT
                              Dashboard shows: [IN POSITION]
                              
T+1000ms Backtest             Price drops to 42480 (stoploss hit)
                              Position closes at 42480
                              
T+1000ms MetricsLogger        position_closed            ✓ LOGGED
                              {"realized_pnl": -2000, ...}
                              
        Dashboard             Updates totals:
                              ✓ Exits: 1
                              ✓ Losses: 1
                              ✓ Gross PnL: -2000
                              ✓ Position: [FLAT]
```

---

## What Gets Written to Metrics Log

```json
┌─ entry_rejected event
│  (Signal evaluation failed)
│
├─ {
│    "ts": 1712343215000,
│    "event": "entry_rejected",
│    "data": {
│      "failed": ["no_signal"],
│      "long_signals": 0,
│      "short_signals": 0
│    }
│  }
│
├─ entry_signal event
│  (Signal passed, order submitted)
│
├─ {
│    "ts": 1712343216000,
│    "event": "entry_signal",
│    "data": {
│      "side": "BUY",
│      "label": "imbalance_continuation_long",
│      "price": 42500.50,
│      "qty": "2.5",
│      "notional_usdt": 106251.25,
│      "confidence": 0.85,
│      "conditions": {
│        "imbalance": 0.95,
│        "cvd_rising": true,
│        "absorption": 1.2
│      }
│    }
│  }
│
├─ position_closed event
│  (Trade completed, PnL locked in)
│
└─ {
     "ts": 1712343245000,
     "event": "position_closed",
     "data": {
       "realized_pnl": 125.50,
       "consecutive_losses": 0,
       "daily_pnl_pct": 1.25
     }
   }
```

---

## Dashboard State Machine

```
BotState (accumulates events)
├── Counters
│   ├─ total_entries: int (incremented by entry_signal events)
│   ├─ total_exits: int (incremented by exit/position_closed events)
│   ├─ total_wins: int (incremented when pnl >= 0)
│   ├─ total_losses: int (incremented when pnl < 0)
│   └─ gross_pnl: float (summed from all PnLs)
│
├── Position State
│   ├─ position_open: bool
│   ├─ entry_price: float | None
│   ├─ entry_ts: str
│   └─ entry_side: "BUY" | "SELL" | None
│
├── Risk State
│   ├─ risk_halted: bool
│   ├─ halt_reason: str
│   ├─ consecutive_losses: int
│   └─ daily_pnl_pct: float | None
│
├── Collections (queues with maxlen)
│   ├─ entries: deque[dict] (20 max) → [ts, side, price, qty, notional]
│   ├─ exits: deque[dict] (20 max) → [ts, reason, pnl]
│   ├─ rejections: deque[dict] (50-10 max) → [ts, failed_reasons]
│   ├─ errors: deque[dict] (30 max) → [ts, msg]
│   └─ warnings: deque[dict] (30 max) → [ts, msg]
│
└── Evaluation Metrics (v2 only)
    ├─ eval_count: int (incremented by each rejection)
    ├─ eval_rate_per_sec: float (computed from eval_times)
    ├─ eval_times: deque[float] (10 max) → timestamps
    ├─ rejection_reasons: dict[str, int] → reason → count
    └─ last_rejection_details: dict → latest rejection breakdown


UPDATE CYCLE (every 0.5 sec):
1. find_latest_log()          → Locate JSONL file
2. tail_new_lines()           → Read only new lines (efficient)
3. apply_events()             → For each event:
                                  ├─ Parse JSON
                                  ├─ Route by event type
                                  └─ Update BotState
4. render_dashboard(state)    → Rich TUI display
```

---

## How Data Reaches Each Dashboard

```
Dashboard v1 Display          Dashboard v2 Display
────────────────────────────  ─────────────────────────────
                              
From entry_rejected:          From entry_rejected:
├─ rejection_reasons[]        ├─ eval_count increment
├─ rejection reason in list   ├─ eval_rate calculation
└─ timestamp                  ├─ rejection_reasons aggregation
                              └─ last_rejection_details
From entry_signal:            
├─ total_entries +1           From entry_signal:
├─ entries.append()           ├─ total_entries +1
├─ position_open = True       ├─ entries.append()
├─ entry_price, entry_ts      └─ position_open = True
└─ entry_side                 
                              From exit/position_closed:
From exit/position_closed:    ├─ total_exits +1
├─ total_exits +1             ├─ exits.append()
├─ position_open = False      ├─ gross_pnl += pnl
├─ exits.append()             └─ total_wins/losses
├─ gross_pnl += pnl           
├─ total_wins/losses          From risk_halt:
└─ pnl color coding           ├─ risk_halted = True
                              └─ halt_reason
From risk_halt:               
├─ risk_halted = True         Position State:
└─ halt_reason                ├─ Current position: open/flat
                              ├─ Entry price
From position_closed:         ├─ Entry time
├─ consecutive_losses         └─ Unrealized/realized PnL
├─ daily_pnl_pct              
└─ displayed in status        Running Totals:
                              ├─ Total entries/exits
                              ├─ Win/loss counts
                              ├─ Gross PnL
                              ├─ Win rate
                              └─ Eval rate/sec
```

---

## Component Integration Points

```
┌──────────────────────────────────────────────────────────────┐
│ Data Sources                                                 │
│ ├─ Parquet: data.parquet (nautilus/data/ticks.py)          │
│ ├─ OrderBook: Via exchange (on_order_book_deltas)           │
│ └─ TradeTicks: Via exchange (on_trade_tick)                 │
└───────────────┬────────────────────────────────────────────┘
                │
        ┌───────▼──────────────────────────────────────────┐
        │ OrderflowStrategy (Main Orchestrator)            │
        │ ├─ _engine: OrderflowFeatureEngine               │
        │ ├─ _signals: SignalRegistry                      │
        │ ├─ _structure_engine: MarketStructureEngine      │
        │ ├─ _risk: PreTradeRiskStack                      │
        │ ├─ _metrics: MetricsLogger ◄── KEY LINK          │
        │ └─ _dataset: DatasetBuffer                       │
        └──────────┬───────────────────────────────────────┘
                   │
        ┌──────────▼──────────────────────────────────────┐
        │ MetricsLogger (Data Sink)                       │
        │ └─ Writes to: JSONL file                        │
        │    - event: entry_rejected/signal/exit/etc      │
        │    - ts: timestamp (unix ms)                    │
        │    - data: event-specific fields                │
        └──────────┬───────────────────────────────────────┘
                   │
    ┌──────────────┴──────────────┐
    │                             │
    ▼                             ▼
┌────────────────────┐   ┌───────────────────┐
│ dashboard.py       │   │ dashboard_v2.py   │
│ (Operational)      │   │ (Signal-Focused)  │
├────────────────────┤   ├───────────────────┤
│ • Entries          │   │ • Eval rate/sec   │
│ • Exits            │   │ • Rejection reasons
│ • Signals          │   │ • Rejection log   │
│ • Errors           │   │ • Entries/exits   │
│ • Warnings         │   │ • Position state  │
│ • Position state   │   │ • PnL totals      │
│ • Risk state       │   │ • Risk status     │
│ • PnL totals       │   │                   │
└────────────────────┘   └───────────────────┘
        │                         │
        └─────────────┬───────────┘
                      │
                Terminal Display
                (Rich TUI)
```

---

## Data Type Flow

```
Input Type          Transformation           Output Type
──────────────────────────────────────────────────────────

TradeTick           trade_tick_to_           dict
(Nautilus)          side_dict()              {"ts", "price", "qty", "side"}
                                             │
                                             ▼
                                             
dict (tick)         OrderflowEngine          CandleFlow
                    .add_tick()              (15 fields: delta, CVD, imbal., etc.)
                                             │
                                             ▼
                                             
CandleFlow[]        .compute_               OrderflowFeature
                    snapshot()              Snapshot
                                            (ts_ms, flow, close_price, 
                                             cvd_ema, cvd_rising, ob_imbalance)
                                             │
                                             ▼
                                             
Snapshot +          evaluate_long()         EntrySignal | None
Structure +         evaluate_short()        (side, label, conditions, confidence)
Session                                      │
                                             ▼
                                             
EntrySignal         submit_order()          Order
                    + risk checks
                                             │
                                             ▼
                                             
Order + Fills       MetricsLogger           Event dict
                    .log_event()            {"ts", "event", "data"}
                                             │
                                             ▼
                                             
Event dict          json.dumps()            JSONL line
                                             │
                                             ▼
                                             
JSONL line          tail_new_lines()        Event dict
                    json.loads()
                                             │
                                             ▼
                                             
Event dict          apply_events()          BotState
                                             │
                                             ▼
                                             
BotState            render_*()              Rich Text/Table/Panel
                                             │
                                             ▼
                                             
                                             Terminal Display
```

---

## Summary Checklist

```
✅ Data Ingestion
   ✓ Parquet loaded
   ✓ Converted to TradeTick
   ✓ Fed to BacktestEngine
   
✅ Feature Computation
   ✓ OrderflowFeatureEngine processes ticks
   ✓ Candle flows cached
   ✓ Snapshots computed
   
✅ Signal Evaluation
   ✓ Registry loads modules
   ✓ Long/short signals evaluated
   ✓ Risk checks applied
   
✅ Execution & Logging
   ✓ Orders submitted
   ✓ Events logged to JSONL
   ✓ Format valid
   
✅ Dashboard Integration
   ✓ Both v1 and v2 find log file
   ✓ Parse JSONL correctly
   ✓ Render TUI without errors
   
⏳ End-to-End Verification
   → Run backtest
   → Monitor metrics file
   → Verify dashboard displays data live
```

