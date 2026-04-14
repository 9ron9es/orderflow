"""
Orderflow Bot — Custom Dashboard
Simplified view with:
  - State, Trades, PnL summary
  - Data Events table (ticks, orderbook, etc)
  - Trades table (entry, TP/SL, exit, state)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rich import box
from rich.align import Align
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text


DEFAULT_LOG_DIR = "orderflow/logs/metrics"
DEFAULT_REFRESH = 0.5
MAX_EVENTS = 20
MAX_TRADES = 15


class BotState:
    """Parsed state from metrics log."""

    def __init__(self) -> None:
        # Summary
        self.state: str = "INITIALIZING"
        self.trades_count: int = 0
        self.pnl: float = 0.0
        self.pnl_pct: float = 0.0
        
        # Data events (ticks, orderbook)
        self.data_events: deque[dict] = deque(maxlen=MAX_EVENTS)
        
        # Trades
        self.trades: deque[dict] = deque(maxlen=MAX_TRADES)
        
        # Current position
        self.position_open: bool = False
        self.position_type: str = "—"  # LONG/SHORT
        self.entry_price: float | None = None
        self.entry_time: str = "—"
        self.tp_level: float | None = None
        self.sl_level: float | None = None
        
        self._file_position: int = 0
        self._log_path: Path | None = None


def find_latest_log(log_dir: Path) -> Path | None:
    """Find most recent metrics log file."""
    if not log_dir.exists():
        return None
    files = list(log_dir.glob("orderflow_metrics_*.jsonl"))
    if not files:
        return None
    return max(files, key=lambda p: p.stat().st_mtime)


def load_events(state: BotState, log_path: Path) -> None:
    """Load and parse events from metrics log."""
    try:
        with open(log_path, "rb") as f:
            f.seek(state._file_position)
            for line in f:
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                    ts = event.get("ts", 0)
                    ts_str = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%H:%M:%S.%f")[:-3]
                    
                    event_type = event.get("event", "unknown")
                    data = event.get("data", {})
                    
                    if event_type == "entry_signal":
                        # NEW TRADE
                        trade = {
                            "time": ts_str,
                            "type": data.get("signal_type", "?"),
                            "entry": data.get("entry_price", 0),
                            "tp": data.get("tp_level"),
                            "sl": data.get("sl_level"),
                            "state": "OPEN",
                        }
                        state.trades.append(trade)
                        state.trades_count += 1
                        state.position_open = True
                        state.position_type = data.get("signal_type", "?")
                        state.entry_price = data.get("entry_price")
                        state.entry_time = ts_str
                        state.tp_level = data.get("tp_level")
                        state.sl_level = data.get("sl_level")
                        state.state = "POSITION OPEN"
                    
                    elif event_type == "exit_signal":
                        # TRADE CLOSED
                        if state.trades:
                            state.trades[-1]["state"] = "CLOSED"
                        state.pnl += data.get("pnl", 0)
                        state.pnl_pct = data.get("pnl_pct", 0)
                        state.position_open = False
                        state.state = "READY"
                    
                    elif event_type == "entry_rejected":
                        # Data event - rejection
                        reason = data.get("failed", ["unknown"])[0]
                        state.data_events.append({
                            "time": ts_str,
                            "event": f"REJECT: {reason}",
                        })
                        state.state = "READY"
                    
                    elif event_type == "tick":
                        # Tick data event
                        state.data_events.append({
                            "time": ts_str,
                            "event": f"TICK @ {data.get('price', 0):.2f}",
                        })
                    
                    elif event_type == "orderbook_update":
                        # Orderbook update
                        state.data_events.append({
                            "time": ts_str,
                            "event": "ORDERBOOK",
                        })
                    
                    else:
                        # Generic event
                        state.data_events.append({
                            "time": ts_str,
                            "event": event_type,
                        })
                
                except json.JSONDecodeError:
                    continue
            
            state._file_position = f.tell()
    
    except (FileNotFoundError, IOError):
        pass


def render_summary(state: BotState) -> Panel:
    """State, Trades, PnL summary."""
    summary_table = Table(show_header=False, box=box.ROUNDED)
    summary_table.add_column("Key", style="cyan")
    summary_table.add_column("Value", style="yellow")
    
    summary_table.add_row("State", state.state)
    summary_table.add_row("Trades", str(state.trades_count))
    pnl_color = "green" if state.pnl >= 0 else "red"
    summary_table.add_row("PnL", f"[{pnl_color}]{state.pnl:.2f} ({state.pnl_pct:.1f}%)[/{pnl_color}]")
    
    if state.position_open:
        summary_table.add_row("Position", f"{state.position_type} @ {state.entry_price:.2f}")
        if state.tp_level:
            summary_table.add_row("TP", f"{state.tp_level:.2f}")
        if state.sl_level:
            summary_table.add_row("SL", f"{state.sl_level:.2f}")
    
    return Panel(summary_table, title="[bold cyan]Summary[/bold cyan]", border_style="cyan")


def render_data_events(state: BotState) -> Panel:
    """Data events table - ticks, orderbook, rejections."""
    events_table = Table(show_header=True, box=box.ROUNDED)
    events_table.add_column("Time", style="dim")
    events_table.add_column("Event", style="white")
    
    for event in state.data_events:
        events_table.add_row(event["time"], event["event"])
    
    return Panel(events_table, title="[bold green]📊 Data Events[/bold green]", border_style="green")


def render_trades(state: BotState) -> Panel:
    """Trades table - entry, TP/SL, exit, state."""
    trades_table = Table(show_header=True, box=box.ROUNDED)
    trades_table.add_column("Time", style="dim")
    trades_table.add_column("Type", style="cyan")
    trades_table.add_column("Entry", style="yellow")
    trades_table.add_column("TP", style="green")
    trades_table.add_column("SL", style="red")
    trades_table.add_column("State", style="white")
    
    for trade in state.trades:
        tp_str = f"{trade['tp']:.2f}" if trade['tp'] else "—"
        sl_str = f"{trade['sl']:.2f}" if trade['sl'] else "—"
        state_color = "green" if trade['state'] == "OPEN" else "dim"
        trades_table.add_row(
            trade["time"],
            trade["type"],
            f"{trade['entry']:.2f}",
            tp_str,
            sl_str,
            f"[{state_color}]{trade['state']}[/{state_color}]",
        )
    
    return Panel(trades_table, title="[bold magenta]📈 Trades[/bold magenta]", border_style="magenta")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log-dir", default=DEFAULT_LOG_DIR)
    parser.add_argument("--refresh", type=float, default=DEFAULT_REFRESH)
    args = parser.parse_args()
    
    log_dir = Path(args.log_dir)
    console = Console()
    state = BotState()
    
    with Live(console=console, refresh_per_second=1 / args.refresh, screen=True) as live:
        try:
            while True:
                log_path = find_latest_log(log_dir)
                if log_path:
                    state._log_path = log_path
                    load_events(state, log_path)
                
                # Build layout
                layout = Layout()
                layout.split_column(
                    Layout(render_summary(state), name="summary"),
                    Layout(render_data_events(state), name="events"),
                    Layout(render_trades(state), name="trades"),
                )
                
                live.update(layout)
                time.sleep(args.refresh)
        
        except KeyboardInterrupt:
            console.print("[yellow]Dashboard stopped[/yellow]")


if __name__ == "__main__":
    main()
