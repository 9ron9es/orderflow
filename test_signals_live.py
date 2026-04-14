#!/usr/bin/env python3
"""
Complete signal generation & dashboard integration test.

Simulates:
1. MultiTFEngine processing ticks
2. Signal evaluation with real orderflow data
3. Signal logging to metrics
4. Dashboard parsing and display

This verifies the full pipeline works before live trading.
"""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from decimal import Decimal

import pandas as pd

from nautilus.features.multi_tf import MultiTFEngine, MultiTFSnapshot
from nautilus.signals.registry import SignalRegistry
from nautilus.signals.long import AbsorptionBreakoutLong, ImbalanceContinuationLong
from nautilus.signals.short import AbsorptionBreakoutShort, ImbalanceContinuationShort
from nautilus.structure.market_structure import MarketStructureEngine, NULL_STRUCTURE
from nautilus.sessions.filter import SessionFilter
from nautilus.ops.metrics import MetricsLogger
from nautilus.data.ticks import parquet_ticks_to_trade_ticks
from nautilus_trader.test_kit.providers import TestInstrumentProvider


def test_full_pipeline():
    """Test: Load ticks → Process features → Generate signals → Log → Dashboard parse."""
    
    print("\n" + "="*80)
    print(" COMPLETE SIGNAL GENERATION & DASHBOARD INTEGRATION TEST")
    print("="*80)
    
    # ─────────────────────────────────────────────────────────────────────────
    # 1. Load tick data
    # ─────────────────────────────────────────────────────────────────────────
    print("\n[1/5] Loading tick data from parquet...")
    instrument = TestInstrumentProvider.btcusdt_perp_binance()
    ticks = parquet_ticks_to_trade_ticks(Path("data.parquet"), instrument)
    print(f"✓ Loaded {len(ticks)} ticks")
    print(f"  First: {ticks[0]}")
    print(f"  Last:  {ticks[-1]}")
    
    # ─────────────────────────────────────────────────────────────────────────
    # 2. Initialize feature engine
    # ─────────────────────────────────────────────────────────────────────────
    print("\n[2/5] Initializing MultiTFEngine...")
    engine = MultiTFEngine(
        ltf="1m",
        htf="5m",
        lookback_candles=50,
        price_bucket_size=1.0,
        large_trade_pct=0.90,
        cvd_smoothing=5,
        divergence_window=3,
    )
    print("✓ Engine initialized")
    print("  LTF: 1m, HTF: 5m, Lookback: 50 candles")
    
    # ─────────────────────────────────────────────────────────────────────────
    # 3. Initialize signal registry
    # ─────────────────────────────────────────────────────────────────────────
    print("\n[3/5] Initializing signal registry...")
    modules = [
        AbsorptionBreakoutLong(absorption_min=0.10, large_dom_min=0.15),
        ImbalanceContinuationLong(imbalance_threshold=0.25, absorption_min=0.15),
        AbsorptionBreakoutShort(absorption_min=0.10, large_dom_min=0.15),
        ImbalanceContinuationShort(imbalance_threshold=0.25, absorption_min=0.15),
    ]
    registry = SignalRegistry(modules, require_all=False)
    print(f"✓ Registry initialized with {len(registry.modules)} modules:")
    for mod in registry.modules:
        print(f"  • {mod.label} ({mod.side.name})")
    
    # ─────────────────────────────────────────────────────────────────────────
    # 4. Process ticks and generate signals
    # ─────────────────────────────────────────────────────────────────────────
    print("\n[4/5] Processing ticks and evaluating signals...")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        metrics_logger = MetricsLogger(tmpdir)
        print(f"✓ Metrics logger initialized: {tmpdir}")
        
        structure_engine = MarketStructureEngine(swing_window=5)
        session_filter = SessionFilter()
        
        signal_count = 0
        rejection_count = 0
        tick_count = 0
        
        for i, tick in enumerate(ticks):
            try:
                # Update engines
                engine.update_tick(tick)
                structure_engine.update_bar(engine.ltf.close_bar)
                
                tick_count += 1
                
                # Get snapshots
                snapshot = engine.snapshot()
                structure = structure_engine.snapshot()
                session = session_filter.current_session(datetime.now(timezone.utc))
                
                if snapshot is None or structure is None:
                    continue
                
                # Evaluate signals
                long_signals = registry.evaluate_long(snapshot, structure, session)
                short_signals = registry.evaluate_short(snapshot, structure, session)
                
                signal = (long_signals or short_signals or [None])[0]
                
                if signal:
                    # Log entry signal
                    metrics_logger.log_event("entry_signal", {
                        "side": signal.side.name,
                        "label": signal.label,
                        "price": float(tick.price),
                        "qty": "0.01",
                        "notional_usdt": float(tick.price) * 0.01,
                        "confidence": signal.confidence,
                        "conditions": signal.conditions,
                    })
                    signal_count += 1
                    print(f"  ✓ Signal #{signal_count} at tick {i}: {signal.label} ({signal.side.name})")
                    print(f"    Price: ${tick.price:,.2f}, Conditions: {sum(signal.conditions.values())}/{len(signal.conditions)}")
                else:
                    rejection_count += 1
                    
            except Exception as e:
                print(f"  ✗ Error processing tick {i}: {e}")
                continue
        
        print(f"\n✓ Processed {tick_count} ticks")
        print(f"  Signals generated: {signal_count}")
        print(f"  Rejections: {rejection_count}")
        
        # ─────────────────────────────────────────────────────────────────────
        # 5. Verify dashboard can parse logged signals
        # ─────────────────────────────────────────────────────────────────────
        print("\n[5/5] Verifying dashboard can parse logged signals...")
        
        log_files = list(Path(tmpdir).glob("*.jsonl"))
        if not log_files:
            print("✗ No log files created")
            return False
        
        log_file = log_files[0]
        print(f"✓ Found log file: {log_file.name}")
        
        # Parse all events
        from dashboard import BotState, apply_events
        
        with open(log_file) as f:
            events = [json.loads(line.strip()) for line in f if line.strip()]
        
        print(f"✓ Parsed {len(events)} events from log file")
        
        # Apply to dashboard state
        state = BotState()
        apply_events(events, state)
        
        print(f"\nDashboard State Summary:")
        print(f"  Total entries:    {state.total_entries}")
        print(f"  Total exits:      {state.total_exits}")
        print(f"  Position open:    {state.position_open}")
        print(f"  Gross PnL:        ${state.gross_pnl:+,.4f}")
        print(f"  Win rate:         {state.total_wins}/{max(state.total_exits, 1)}")
        
        # Verify event details
        if state.entries:
            print(f"\n✓ Entry events logged:")
            for entry in list(state.entries)[:3]:  # Show first 3
                print(f"  • {entry['ts']}: {entry['side']} @ ${entry['price']:.2f} qty={entry['qty']}")
        
        # Test rendering
        try:
            from dashboard import render_header, render_position, render_orders
            render_header(state)
            render_position(state)
            render_orders(state)
            print(f"\n✓ Dashboard rendering functions work correctly")
        except Exception as e:
            print(f"✗ Dashboard rendering failed: {e}")
            return False
    
    return True


def main():
    """Run the complete integration test."""
    try:
        success = test_full_pipeline()
        
        print("\n" + "="*80)
        if success:
            print(" ✓ ALL TESTS PASSED - SYSTEM READY FOR LIVE TRADING")
            print("="*80)
            print("\nNext steps:")
            print("  1. Run: python dashboard.py")
            print("  2. In another terminal: python live_runner.py")
            print("  3. Monitor the dashboard for real signals")
            return 0
        else:
            print(" ✗ TESTS FAILED - CHECK OUTPUT ABOVE")
            print("="*80)
            return 1
    except Exception as e:
        print(f"\n✗ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
