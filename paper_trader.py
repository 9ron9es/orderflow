import json
import time
from pathlib import Path
from datetime import datetime

PAPER_FILE = Path("paper_trades.json")


class PaperTrader:
    def __init__(self):
        self.state = self._load()

    def _load(self):
        if PAPER_FILE.exists():
            with open(PAPER_FILE) as f:
                return json.load(f)
        # Fresh state
        state = {
            "account": {"balance_usdt": 1000.0, "initial_balance": 1000.0},
            "open_positions": {},
            "closed_trades": [],
            "pending_orders": []
        }
        self._save(state)
        return state

    def _save(self, state=None):
        with open(PAPER_FILE, "w") as f:
            json.dump(state or self.state, f, indent=2)

    # ─── CORE: Place a simulated market order ───────────────────────────────
    def place_order(self, symbol: str, side: str, usdt_amount: float, current_price: float):
        """
        side: 'BUY' or 'SELL'
        current_price: live price from Binance (read-only, safe to use)
        """
        balance = self.state["account"]["balance_usdt"]

        pos = self.state["open_positions"].get(symbol)
        if side == "BUY":
            if pos and pos.get("side") == "SHORT":
                return self._close_short(symbol, current_price)
            return self._open_long(symbol, usdt_amount, current_price, balance)
        if side == "SELL":
            if pos and pos.get("side") == "LONG":
                return self._close_long(symbol, current_price)
            return self._open_short(symbol, usdt_amount, current_price, balance)
        return {"error": f"Unsupported side {side}"}

    def _open_long(self, symbol: str, usdt_amount: float, current_price: float, balance: float):
        if usdt_amount <= 0:
            return {"error": "Invalid order size"}
        if usdt_amount > balance:
            return {"error": "Insufficient balance", "balance": balance}
        qty = usdt_amount / current_price
        fee = usdt_amount * 0.001
        position = {
            "symbol": symbol,
            "side": "LONG",
            "entry_price": current_price,
            "qty": qty,
            "usdt_invested": usdt_amount,
            "fee_paid": fee,
            "opened_at": datetime.utcnow().isoformat(),
            "pnl": 0.0,
        }
        self.state["account"]["balance_usdt"] -= (usdt_amount + fee)
        self.state["open_positions"][symbol] = position
        self._save()
        return {"status": "FILLED", "position": position}

    def _close_long(self, symbol: str, current_price: float):
        pos = self.state["open_positions"].get(symbol)
        if not pos:
            return {"error": f"No open position for {symbol}"}
        entry = pos["entry_price"]
        qty = pos["qty"]
        pnl = (current_price - entry) * qty
        fee = (current_price * qty) * 0.001
        net_pnl = pnl - fee - pos["fee_paid"]
        closed = {**pos, "exit_price": current_price, "pnl": round(net_pnl, 4), "closed_at": datetime.utcnow().isoformat()}
        self.state["account"]["balance_usdt"] += (current_price * qty) - fee
        self.state["closed_trades"].append(closed)
        del self.state["open_positions"][symbol]
        self._save()
        return {"status": "CLOSED", "trade": closed}

    def _open_short(self, symbol: str, usdt_amount: float, current_price: float, balance: float):
        if usdt_amount <= 0:
            return {"error": "Invalid order size"}
        margin = usdt_amount
        if margin > balance:
            return {"error": "Insufficient balance", "balance": balance}
        qty = usdt_amount / current_price
        fee = usdt_amount * 0.001
        position = {
            "symbol": symbol,
            "side": "SHORT",
            "entry_price": current_price,
            "qty": qty,
            "usdt_invested": usdt_amount,
            "fee_paid": fee,
            "opened_at": datetime.utcnow().isoformat(),
            "pnl": 0.0,
        }
        self.state["account"]["balance_usdt"] -= (margin + fee)
        self.state["open_positions"][symbol] = position
        self._save()
        return {"status": "FILLED", "position": position}

    def _close_short(self, symbol: str, current_price: float):
        pos = self.state["open_positions"].get(symbol)
        if not pos:
            return {"error": f"No open position for {symbol}"}
        entry = pos["entry_price"]
        qty = pos["qty"]
        gross_pnl = (entry - current_price) * qty
        fee = (current_price * qty) * 0.001
        net_pnl = gross_pnl - fee - pos["fee_paid"]
        closed = {**pos, "exit_price": current_price, "pnl": round(net_pnl, 4), "closed_at": datetime.utcnow().isoformat()}
        # Release initial margin and apply PnL.
        self.state["account"]["balance_usdt"] += pos["usdt_invested"] + net_pnl
        self.state["closed_trades"].append(closed)
        del self.state["open_positions"][symbol]
        self._save()
        return {"status": "CLOSED", "trade": closed}

    # ─── Check unrealized PnL on open positions ──────────────────────────────
    def mark_to_market(self, symbol: str, current_price: float):
        pos = self.state["open_positions"].get(symbol)
        if not pos:
            return None
        unrealized = (current_price - pos["entry_price"]) * pos["qty"]
        pos["pnl"] = round(unrealized, 4)
        self._save()
        return unrealized

    # ─── Stats ───────────────────────────────────────────────────────────────
    def get_stats(self):
        trades = self.state["closed_trades"]
        if not trades:
            return {"message": "No closed trades yet"}

        total_pnl = sum(t["pnl"] for t in trades)
        wins = [t for t in trades if t["pnl"] > 0]
        return {
            "total_trades": len(trades),
            "win_rate": f"{len(wins)/len(trades)*100:.1f}%",
            "total_pnl_usdt": round(total_pnl, 4),
            "current_balance": round(self.state["account"]["balance_usdt"], 4),
            "roi": f"{((self.state['account']['balance_usdt'] - self.state['account']['initial_balance']) / self.state['account']['initial_balance']) * 100:.2f}%"
        }

    # ─── Get current account state ────────────────────────────────────────────
    def get_account_state(self):
        return {
            "balance_usdt": self.state["account"]["balance_usdt"],
            "open_positions": self.state["open_positions"],
            "open_trades_count": len(self.state["open_positions"]),
            "closed_trades_count": len(self.state["closed_trades"])
        }
