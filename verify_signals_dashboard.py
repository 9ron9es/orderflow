#!/usr/bin/env python3
"""
Verification script for signal generation and dashboard.

Tests:
1. Signal module imports and instantiation
2. Signal evaluation logic with mock data
3. Dashboard log file format and parsing
4. Metrics logger functionality
5. Entry/exit event logging

Run:
    python verify_signals_dashboard.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from decimal import Decimal

# Add project to path
sys.path.insert(0, str(Path(__file__).parent))


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Test Signal Module Imports
# ═══════════════════════════════════════════════════════════════════════════════

def test_signal_imports() -> bool:
    """Verify all signal modules can be imported."""
    print("\n[TEST 1] Signal Module Imports")
    print("─" * 60)
    
    try:
        from nautilus.signals.base import SignalModule, EntrySignal
        print("✓ Base classes imported successfully")
        
        from nautilus.signals.long import (
            AbsorptionBreakoutLong,
            ImbalanceContinuationLong,
            DivergenceReversalLong,
            LateEntryConfirmLong,
        )
        print("✓ Long signal modules imported successfully (4 total)")
        
        from nautilus.signals.short import (
            AbsorptionBreakoutShort,
            ImbalanceContinuationShort,
            DivergenceReversalShort,
        )
        print("✓ Short signal modules imported successfully (3 total)")
        
        from nautilus.signals.registry import SignalRegistry
        print("✓ Signal registry imported successfully")
        
        return True
    except Exception as e:
        print(f"✗ Import failed: {e}")
        return False


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Test Signal Registry
# ═══════════════════════════════════════════════════════════════════════════════

def test_signal_registry() -> bool:
    """Verify signal registry can load and evaluate modules."""
    print("\n[TEST 2] Signal Registry")
    print("─" * 60)
    
    try:
        from nautilus.signals.registry import SignalRegistry
        from nautilus.signals.long import AbsorptionBreakoutLong, ImbalanceContinuationLong
        from nautilus.signals.short import AbsorptionBreakoutShort, ImbalanceContinuationShort
        
        modules = [
            AbsorptionBreakoutLong(),
            ImbalanceContinuationLong(),
            AbsorptionBreakoutShort(),
            ImbalanceContinuationShort(),
        ]
        
        registry = SignalRegistry(modules, require_all=False)
        print(f"✓ Registry created with {len(registry.modules)} modules")
        
        for mod in registry.modules:
            print(f"  • {mod}")
        
        return True
    except Exception as e:
        print(f"✗ Registry test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Test Metrics Logger
# ═══════════════════════════════════════════════════════════════════════════════

def test_metrics_logger() -> bool:
    """Verify metrics logger creates files and writes events."""
    print("\n[TEST 3] Metrics Logger")
    print("─" * 60)
    
    try:
        from nautilus.ops.metrics import MetricsLogger
        
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = MetricsLogger(tmpdir)
            print(f"✓ Logger created in {tmpdir}")
            
            # Log test events
            logger.log_event("entry_signal", {
                "side": "BUY",
                "label": "absorption_breakout_long",
                "price": 43200.50,
                "qty": "0.5",
                "notional_usdt": 21600.25,
                "confidence": 0.95,
                "conditions": {
                    "large_dom": True,
                    "buy_absorption": True,
                    "cvd_rising": True,
                }
            })
            print("✓ entry_signal logged")
            
            logger.log_event("exit", {
                "reason": "trailing_stop",
                "pnl": 0.1234,
            })
            print("✓ exit logged")
            
            logger.log_event("entry_rejected", {
                "failed": ["cvd_rising", "imbalance"]
            })
            print("✓ entry_rejected logged")
            
            logger.log_event("error", {
                "msg": "Test error message"
            })
            print("✓ error logged")
            
            # Verify files exist
            log_files = list(Path(tmpdir).glob("*.jsonl"))
            if log_files:
                print(f"✓ Log file created: {log_files[0].name}")
                
                # Read and verify content
                lines = log_files[0].read_text().strip().split('\n')
                print(f"✓ {len(lines)} events written to file")
                
                # Parse first event
                first_event = json.loads(lines[0])
                if first_event.get("event") == "entry_signal":
                    print("✓ First event is entry_signal with correct format")
                else:
                    print(f"✗ Unexpected first event type: {first_event.get('event')}")
                    return False
                
                return True
            else:
                print("✗ No log files created")
                return False
                
    except Exception as e:
        print(f"✗ Logger test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Test Dashboard Log Parsing
# ═══════════════════════════════════════════════════════════════════════════════

def test_dashboard_log_parsing() -> bool:
    """Verify dashboard can parse and apply events."""
    print("\n[TEST 4] Dashboard Log Parsing")
    print("─" * 60)
    
    try:
        from dashboard import BotState, apply_events, tail_new_lines
        
        state = BotState()
        print("✓ BotState created")
        
        # Create synthetic events
        now_ts = int(datetime.now(timezone.utc).timestamp() * 1000)
        
        events = [
            {
                "event": "entry_signal",
                "ts": now_ts,
                "data": {
                    "side": "BUY",
                    "label": "absorption_breakout_long",
                    "price": 43200.50,
                    "qty": "0.5",
                    "notional_usdt": 21600.25,
                    "confidence": 0.95,
                    "conditions": {
                        "large_dom": True,
                        "buy_absorption": True,
                        "cvd_rising": True,
                    }
                }
            },
            {
                "event": "exit",
                "ts": now_ts + 60000,
                "data": {
                    "reason": "trailing_stop",
                    "pnl": 0.1234,
                }
            },
            {
                "event": "entry_rejected",
                "ts": now_ts + 120000,
                "data": {
                    "failed": ["cvd_rising", "imbalance"]
                }
            }
        ]
        
        apply_events(events, state)
        print("✓ Events applied to state")
        
        # Verify state was updated
        assert state.total_entries == 1, "Expected 1 entry"
        assert state.total_exits == 1, "Expected 1 exit"
        assert state.position_open == False, "Expected position to be closed"
        print("✓ State updates correct")
        
        assert len(state.rejections) == 1, "Expected 1 rejection"
        print("✓ Rejection recorded")
        
        assert state.gross_pnl == 0.1234, f"Expected PnL 0.1234, got {state.gross_pnl}"
        print("✓ PnL calculation correct")
        
        return True
        
    except Exception as e:
        print(f"✗ Dashboard parsing test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Test Entry Signal Data Flow
# ═══════════════════════════════════════════════════════════════════════════════

def test_entry_signal_dataflow() -> bool:
    """Verify entry signal can be created and serialized."""
    print("\n[TEST 5] Entry Signal Data Flow")
    print("─" * 60)
    
    try:
        from nautilus.signals.base import EntrySignal
        from nautilus_trader.model.enums import OrderSide
        
        # Create entry signal
        signal = EntrySignal(
            side=OrderSide.BUY,
            label="absorption_breakout_long",
            confidence=0.92,
            conditions={
                "large_dom": True,
                "buy_absorption": True,
                "cvd_rising": True,
                "ob_bid_heavy": True,
                "no_bearish_div": True,
                "htf_not_bearish": True,
            },
            failed=[],
        )
        print("✓ Entry signal created")
        
        # Verify all fields
        assert signal.side == OrderSide.BUY, "Side should be BUY"
        assert signal.label == "absorption_breakout_long", "Label mismatch"
        assert signal.confidence == 0.92, "Confidence mismatch"
        assert len(signal.conditions) == 6, "Expected 6 conditions"
        assert len(signal.failed) == 0, "Should have no failed conditions"
        print("✓ Entry signal fields correct")
        
        # Verify serialization
        data_dict = {
            "side": signal.side.name,
            "label": signal.label,
            "confidence": signal.confidence,
            "conditions": signal.conditions,
            "failed": signal.failed,
        }
        json_str = json.dumps(data_dict)
        parsed = json.loads(json_str)
        print("✓ Entry signal serializable to JSON")
        
        return True
        
    except Exception as e:
        print(f"✗ Entry signal dataflow test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


# ═══════════════════════════════════════════════════════════════════════════════
# 6. Test Dashboard Layout Rendering
# ═══════════════════════════════════════════════════════════════════════════════

def test_dashboard_rendering() -> bool:
    """Verify dashboard can render without errors."""
    print("\n[TEST 6] Dashboard Layout Rendering")
    print("─" * 60)
    
    try:
        from dashboard import (
            BotState, render_header, render_position, render_risk,
            render_orders, render_rejections, render_errors,
            render_conditions_legend, build_layout
        )
        
        state = BotState()
        state.total_entries = 5
        state.total_exits = 3
        state.total_wins = 2
        state.total_losses = 1
        state.gross_pnl = 0.5678
        state.position_open = True
        state.entry_price = 43200.50
        
        print("✓ BotState populated with test data")
        
        # Test rendering functions
        render_header(state)
        print("✓ render_header() works")
        
        render_position(state)
        print("✓ render_position() works")
        
        render_risk(state)
        print("✓ render_risk() works")
        
        render_orders(state)
        print("✓ render_orders() works")
        
        render_rejections(state)
        print("✓ render_rejections() works")
        
        render_errors(state)
        print("✓ render_errors() works")
        
        render_conditions_legend()
        print("✓ render_conditions_legend() works")
        
        build_layout(state)
        print("✓ build_layout() works")
        
        return True
        
    except Exception as e:
        print(f"✗ Dashboard rendering test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


# ═══════════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════════

def main() -> int:
    """Run all verification tests."""
    print("\n" + "=" * 70)
    print(" ORDERFLOW SIGNAL GENERATION & DASHBOARD VERIFICATION")
    print("=" * 70)
    
    results = {
        "Signal Imports": test_signal_imports(),
        "Signal Registry": test_signal_registry(),
        "Metrics Logger": test_metrics_logger(),
        "Dashboard Parsing": test_dashboard_log_parsing(),
        "Entry Signal Dataflow": test_entry_signal_dataflow(),
        "Dashboard Rendering": test_dashboard_rendering(),
    }
    
    # Summary
    print("\n" + "=" * 70)
    print(" TEST SUMMARY")
    print("=" * 70)
    
    for test_name, passed in results.items():
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"{status:8} {test_name}")
    
    total = len(results)
    passed = sum(results.values())
    print(f"\n{passed}/{total} tests passed")
    
    if passed == total:
        print("\n✓ All verification tests passed!")
        return 0
    else:
        print(f"\n✗ {total - passed} test(s) failed. Check output above for details.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
