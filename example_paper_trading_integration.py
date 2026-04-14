#!/usr/bin/env python3
"""
example_paper_trading_integration.py — Real-world integration example.

This shows how to use PaperTrader with your orderflow signals.
It's a drop-in example you can adapt for your specific needs.

Usage:
    python example_paper_trading_integration.py [--reset]
"""

import argparse
from datetime import datetime
from pathlib import Path
from paper_trader import PaperTrader


class PaperTradingSimulator:
    """
    Wrapper around PaperTrader that handles a complete trading loop.
    
    This is what you'd use to integrate paper trading with your orderflow strategy.
    """
    
    def __init__(self):
        self.paper = PaperTrader()
        self.trade_log = []
    
    def execute_signal(self, symbol: str, signal: str, usdt_amount: float, current_price: float) -> dict:
        """
        Execute a trading signal from your orderflow strategy.
        
        Args:
            symbol: e.g., "BTCUSDT"
            signal: "BUY" or "SELL"
            usdt_amount: Amount of USDT for BUY orders (ignored for SELL)
            current_price: Current market price from Binance
        
        Returns:
            Result dict with status, position/trade info, or error
        """
        result = self.paper.place_order(
            symbol=symbol,
            side=signal,
            usdt_amount=usdt_amount,
            current_price=current_price
        )
        
        # Log the trade
        self.trade_log.append({
            "timestamp": datetime.utcnow().isoformat(),
            "symbol": symbol,
            "signal": signal,
            "price": current_price,
            "amount": usdt_amount,
            "result": result
        })
        
        return result
    
    def update_positions(self, price_snapshot: dict) -> None:
        """
        Update mark-to-market PnL for all open positions.
        
        Args:
            price_snapshot: Dict like {"BTCUSDT": 45000, "ETHUSDT": 2500, ...}
        """
        for symbol, price in price_snapshot.items():
            self.paper.mark_to_market(symbol, price)
    
    def print_summary(self) -> None:
        """Print trading summary to console."""
        state = self.paper.state
        stats = self.paper.get_stats()
        
        print("\n" + "="*70)
        print("PAPER TRADING SIMULATOR — SUMMARY")
        print("="*70)
        
        print(f"\nAccount Status:")
        print(f"  Initial balance:   ${state['account']['initial_balance']:.2f}")
        print(f"  Current balance:   ${state['account']['balance_usdt']:.2f}")
        print(f"  Equity change:     ${state['account']['balance_usdt'] - state['account']['initial_balance']:.2f}")
        
        if state['open_positions']:
            print(f"\nOpen Positions ({len(state['open_positions'])}):")
            total_open_pnl = 0
            for symbol, pos in state['open_positions'].items():
                pnl = pos['pnl']
                total_open_pnl += pnl
                print(f"  {symbol:12} | Entry: ${pos['entry_price']:>10.2f} | " +
                      f"Qty: {pos['qty']:>10.8f} | PnL: ${pnl:>10.2f}")
            print(f"  {'TOTAL':12} | {'':25} | {'':>10} | PnL: ${total_open_pnl:>10.2f}")
        else:
            print("\nOpen Positions: None")
        
        if state['closed_trades']:
            print(f"\nClosed Trades ({len(state['closed_trades'])}):")
            print(f"  Total trades:      {stats.get('total_trades', 0)}")
            print(f"  Win rate:          {stats.get('win_rate', 'N/A')}")
            print(f"  Total P&L:         ${stats.get('total_pnl_usdt', 0):.2f}")
            print(f"  ROI:               {stats.get('roi', 'N/A')}")
        else:
            print("\nClosed Trades: None")
        
        print("\n" + "="*70 + "\n")
    
    def reset(self) -> None:
        """Reset to initial state."""
        initial_state = {
            "account": {"balance_usdt": 1000.0, "initial_balance": 1000.0},
            "open_positions": {},
            "closed_trades": [],
            "pending_orders": []
        }
        self.paper._save(initial_state)
        self.trade_log = []
        print("✓ Paper trading simulator reset to 1000 USDT")


def simulate_orderflow_session():
    """
    Example: Simulate a complete orderflow trading session.
    
    This demonstrates how to:
    1. Initialize the simulator
    2. Execute multiple signals
    3. Update positions with new prices
    4. Check performance
    """
    sim = PaperTradingSimulator()
    
    print("\n" + "="*70)
    print("EXAMPLE: Simulating Orderflow Trading Session")
    print("="*70)
    
    # Scenario: Trading BTCUSDT with 3 signals
    trades = [
        # (symbol, signal, usdt_amount, current_price)
        ("BTCUSDT", "BUY", 100, 45000),      # Buy 100 USDT of BTC
        ("BTCUSDT", "SELL", 0, 46000),       # Sell when price goes up
        ("ETHUSDT", "BUY", 50, 2500),        # Buy 50 USDT of ETH
        ("SOLUSDT", "BUY", 50, 200),         # Buy 50 USDT of SOL
    ]
    
    print("\n1. Executing signals...")
    for i, (symbol, signal, amount, price) in enumerate(trades, 1):
        print(f"\n   Signal {i}: {signal:4} {symbol:10} @ ${price:>8.0f} ({amount:>6} USDT)")
        result = sim.execute_signal(symbol, signal, amount, price)
        
        if result.get("error"):
            print(f"      ✗ Error: {result['error']}")
        elif signal == "BUY":
            pos = result['position']
            print(f"      ✓ Entry @ ${pos['entry_price']:.2f} | Qty: {pos['qty']:.8f}")
        else:  # SELL
            trade = result['trade']
            print(f"      ✓ Exit @ ${trade['exit_price']:.2f} | PnL: ${trade['pnl']:.2f}")
    
    # Update remaining positions to new prices
    print("\n2. Updating positions to new market prices...")
    new_prices = {
        "ETHUSDT": 2600,  # ETH went up
        "SOLUSDT": 210,   # SOL went up
    }
    sim.update_positions(new_prices)
    print(f"   Updated: {new_prices}")
    
    # Show summary
    print("\n3. Trading summary")
    sim.print_summary()
    
    return sim


def integrate_with_orderflow_strategy():
    """
    Example: How to integrate PaperTrader with your OrderflowStrategy.
    
    This is pseudocode showing the pattern you'd use.
    """
    example_code = """
    # In your orderflow strategy or runner:
    
    from paper_trader import PaperTrader
    
    class OrderflowWithPaper:
        def __init__(self):
            self.paper = PaperTrader()
            self._paper_mode = True  # Set to False for live trading
        
        def process_signal(self, signal_data):
            # Signal logic (unchanged)
            symbol = signal_data['symbol']
            signal = signal_data['signal']  # "BUY" or "SELL"
            price = signal_data['price']
            
            if self._paper_mode:
                # Route to paper trader
                usdt_amount = signal_data.get('usdt_amount', 50)
                result = self.paper.place_order(symbol, signal, usdt_amount, price)
                print(f"Paper: {symbol} {signal} @ {price}: {result}")
            else:
                # Route to real Binance
                self.binance_client.order_market(symbol, signal, amount)
        
        def get_performance(self):
            return self.paper.get_stats()
    """
    
    print("\n" + "="*70)
    print("INTEGRATION PATTERN: OrderflowStrategy with Paper Trading")
    print("="*70)
    print(example_code)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Paper trading integration example")
    parser.add_argument("--reset", action="store_true", help="Reset to initial state")
    parser.add_argument("--integrate", action="store_true", help="Show integration pattern")
    args = parser.parse_args()
    
    if args.reset:
        sim = PaperTradingSimulator()
        sim.reset()
    elif args.integrate:
        integrate_with_orderflow_strategy()
    else:
        sim = simulate_orderflow_session()
