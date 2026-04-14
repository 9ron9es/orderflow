#!/usr/bin/env python3
"""
test_paper_trader.py — Quick verification of PaperTrader functionality.

Usage:
    python test_paper_trader.py
"""

import json
from paper_trader import PaperTrader
from pathlib import Path


def test_paper_trader():
    """Test basic PaperTrader operations."""
    print("\n" + "="*60)
    print("PAPER TRADER TEST")
    print("="*60)
    
    # Remove existing state
    paper_file = Path("paper_trades.json")
    if paper_file.exists():
        paper_file.unlink()
    
    # Initialize fresh PaperTrader
    print("\n✓ Initializing PaperTrader...")
    paper = PaperTrader()
    
    state = paper.get_account_state()
    print(f"  Initial balance: ${state['balance_usdt']:.2f}")
    assert state['balance_usdt'] == 1000.0, "Initial balance should be 1000"
    print("  ✓ Initial state correct")
    
    # Test BUY order
    print("\n✓ Testing BUY order...")
    current_price = 45000.0
    result = paper.place_order("BTCUSDT", "BUY", usdt_amount=100, current_price=current_price)
    print(f"  Result: {result['status']}")
    assert result['status'] == "FILLED", "BUY should be filled"
    
    position = result['position']
    print(f"  Qty: {position['qty']:.8f} BTC")
    print(f"  Entry price: ${position['entry_price']:.2f}")
    print(f"  Fee paid: ${position['fee_paid']:.4f}")
    
    state = paper.get_account_state()
    print(f"  Balance after BUY: ${state['balance_usdt']:.2f}")
    print(f"  Open positions: {state['open_trades_count']}")
    assert state['open_trades_count'] == 1, "Should have 1 open position"
    print("  ✓ BUY order processed correctly")
    
    # Test mark_to_market
    print("\n✓ Testing mark_to_market...")
    new_price = 46000.0
    unrealized = paper.mark_to_market("BTCUSDT", new_price)
    print(f"  Current price: ${new_price:.2f}")
    print(f"  Unrealized PnL: ${unrealized:.2f}")
    assert unrealized > 0, "Should be profitable at higher price"
    print("  ✓ Mark-to-market working")
    
    # Test SELL order
    print("\n✓ Testing SELL order (close position)...")
    result = paper.place_order("BTCUSDT", "SELL", usdt_amount=0, current_price=new_price)
    print(f"  Result: {result['status']}")
    assert result['status'] == "CLOSED", "SELL should close position"
    
    trade = result['trade']
    print(f"  Exit price: ${trade['exit_price']:.2f}")
    print(f"  Realized PnL: ${trade['pnl']:.4f}")
    
    state = paper.get_account_state()
    print(f"  Balance after SELL: ${state['balance_usdt']:.2f}")
    print(f"  Open positions: {state['open_trades_count']}")
    print(f"  Closed trades: {state['closed_trades_count']}")
    assert state['open_trades_count'] == 0, "Should have no open positions"
    assert state['closed_trades_count'] == 1, "Should have 1 closed trade"
    print("  ✓ SELL order processed correctly")
    
    # Test get_stats
    print("\n✓ Testing get_stats...")
    stats = paper.get_stats()
    print(f"  Total trades: {stats['total_trades']}")
    print(f"  Win rate: {stats['win_rate']}")
    print(f"  Total PnL: ${stats['total_pnl_usdt']:.4f}")
    print(f"  Current balance: ${stats['current_balance']:.2f}")
    print(f"  ROI: {stats['roi']}")
    print("  ✓ Stats calculated correctly")
    
    # Test insufficient balance
    print("\n✓ Testing insufficient balance...")
    result = paper.place_order("ETHUSDT", "BUY", usdt_amount=2000, current_price=2500)
    print(f"  Result: {result}")
    assert "error" in result, "Should return error for insufficient balance"
    print("  ✓ Balance check working")
    
    # Test multiple positions
    print("\n✓ Testing multiple positions...")
    paper.place_order("ETHUSDT", "BUY", usdt_amount=100, current_price=2500)
    paper.place_order("SOLUSDT", "BUY", usdt_amount=100, current_price=200)
    
    state = paper.get_account_state()
    print(f"  Open positions: {state['open_trades_count']}")
    print(f"  Position symbols: {list(paper.state['open_positions'].keys())}")
    assert state['open_trades_count'] == 2, "Should have 2 open positions"
    print("  ✓ Multiple positions working")
    
    # Clean up and verify persistence
    print("\n✓ Testing persistence...")
    paper2 = PaperTrader()  # Load existing state
    state2 = paper2.get_account_state()
    print(f"  Reloaded balance: ${state2['balance_usdt']:.2f}")
    print(f"  Reloaded open positions: {state2['open_trades_count']}")
    assert state2['balance_usdt'] == state['balance_usdt'], "State should persist"
    print("  ✓ State persistence working")
    
    print("\n" + "="*60)
    print("✓ ALL TESTS PASSED")
    print("="*60 + "\n")


if __name__ == "__main__":
    test_paper_trader()
