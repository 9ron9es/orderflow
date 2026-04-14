"""
Orderflow Bot — Live Terminal Dashboard (Pipeline View)
==================================================
Shows real-time pipeline data: price, CVD, orderbook imbalance, signal evaluation, and what's passing/failing.

Usage:
    python dashboard_pipeline.py
    python dashboard_pipeline.py --log-dir orderflow/logs/metrics
    python dashboard_pipeline.py --refresh 0.5
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


# ─── Config ────────────────────────────────────────────────────────────────────

DEFAULT_LOG_DIR   = "orderflow/logs/metrics"
DEFAULT_REFRESH   = 0.5          # seconds between re-renders
MAX_REJECTIONS    = 20           # rows in rejection log
MAX_SIGNALS       = 15           # rows in signal log


# ─── State ─────────────────────────────────────────────────────────────────────

class PipelineState:
    """Real-time pipeline state from metrics log stream."""

    def __init__(self) -> None:
        self.rejections: deque[dict] = deque(maxlen=MAX_REJECTIONS)
        self.signals: deque[dict] = deque(maxlen=MAX_SIGNALS)
        
        # Real-time market data
        self.current_price: float | None = None
        self.cvd_value: float | None = None
        self.cvd_trend: str | None = None
        self.ob_imbalance: float | None = None
        self.imbalance_ratio: float | None = None
        self.absorption_level: float | None = None
        
        # Signal evaluation results
        self.last_signal_check: str = "—"
        self.long_signals: list[str] = []
        self.short_signals: list[str] = []
        self.failed_conditions: list[str] = []
        
        # Position state
        self.position_open: bool = False
        self.entry_price: float | None = None
        self.entry_ts: str = "—"
        self.pnl: float = 0.0
        
        self._file_position: int = 0
        self._log_path: Path | None = None


# ─── Log reader ────────────────────────────────────────────────────────────────

def find_latest_log(log_dir: Path) -> Path | None:
    """Return the most recently modified *.jsonl file in log_dir."""
    if not log_dir.exists():
        return None
    files = sorted(log_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    # Try to find any file with "pipeline" in name first, then fallback to regular
    pipeline_files = [f for f in files if "pipeline" in f.name]
    if pipeline_files:
        return pipeline_files[0]
    return files[0] if files else None


def tail_new_lines(path: Path, state: PipelineState) -> list[dict]:
    """Read any new lines added to path since the last read."""
    lines = []
    try:
        with path.open("r", encoding="utf-8") as f:
            f.seek(state._file_position)
            for line in f:
                line = line.strip()
                if line:
                    lines.append(json.loads(line))
            state._file_position = f.tell()
    except (OSError, json.JSONDecodeError):
        pass
    return lines


def apply_events(events: list[dict], state: PipelineState) -> None:
    """Update pipeline state from new log events."""
    for ev in events:
        event_type = ev.get("event", "")
        data = ev.get("data", {})
        ts = ev.get("ts", "")
        
        if event_type == "market_update":
            # Real-time market data
            state.current_price = data.get("price")
            state.cvd_value = data.get("cvd")
            state.cvd_trend = data.get("cvd_trend")
            state.ob_imbalance = data.get("ob_imbalance")
            state.imbalance_ratio = data.get("imbalance_ratio")
            state.absorption_level = data.get("absorption")
            
        elif event_type == "signal_evaluation":
            # Signal evaluation results
            state.last_signal_check = _fmt_ts(ts)
            state.long_signals = data.get("long_signals", [])
            state.short_signals = data.get("short_signals", [])
            state.failed_conditions = data.get("failed_conditions", [])
            
        elif event_type == "entry_signal":
            state.position_open = True
            state.entry_price = data.get("price")
            state.entry_ts = _fmt_ts(ts)
            
        elif event_type == "position_closed":
            state.position_open = False
            state.pnl += data.get("realized_pnl", 0.0)
            
        elif event_type == "entry_rejected":
            state.failed_conditions = data.get("failed", [])


# ─── Rendering ─────────────────────────────────────────────────────────────────

def _fmt_ts(raw: Any) -> str:
    """Convert a unix timestamp (s or ms) or ISO string to HH:MM:SS."""
    if not raw:
        return "—"
    try:
        if isinstance(raw, (int, float)):
            ts_s = raw / 1000.0 if raw > 1e10 else float(raw)
            return datetime.fromtimestamp(ts_s, tz=timezone.utc).strftime("%H:%M:%S")
        return str(raw)[11:19]   # ISO string slice
    except Exception:
        return str(raw)[:8]


def render_header(state: PipelineState) -> Panel:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d  %H:%M:%S UTC")
    
    status_color = "yellow bold" if state.position_open else "green bold"
    status_text = "[IN POSITION]" if state.position_open else "[SCANNING]"
    
    pnl_color = "green" if state.pnl >= 0 else "red"

    grid = Table.grid(padding=(0, 3))
    grid.add_column(justify="left")
    grid.add_column(justify="left")
    grid.add_column(justify="left")
    grid.add_column(justify="right")

    grid.add_row(
        Text(f"Status  ", style="dim") + Text(status_text, style=status_color),
        Text(f"Price  ", style="dim") + Text(f"${state.current_price:,.1f}" if state.current_price else "—", style="cyan"),
        Text(f"PnL  ", style="dim") + Text(f"{state.pnl:+.4f}", style=pnl_color),
        Text(now, style="dim"),
    )

    return Panel(grid, title="[bold]Orderflow Pipeline Monitor[/bold]", border_style="bright_blue", padding=(0, 1))


def render_market_data(state: PipelineState) -> Panel:
    grid = Table.grid(padding=(0, 4))
    grid.add_column()
    grid.add_column()

    # CVD Section
    grid.add_row(Text("CVD Value", style="dim"), Text(f"{state.cvd_value:+.4f}" if state.cvd_value is not None else "—", style="cyan"))
    grid.add_row(Text("CVD Trend", style="dim"), Text(state.cvd_trend or "—", style="cyan"))
    
    # Orderbook Section  
    grid.add_row(Text("OB Imbalance", style="dim"), Text(f"{state.ob_imbalance:+.3f}" if state.ob_imbalance is not None else "—", style="yellow"))
    grid.add_row(Text("Imbalance Ratio", style="dim"), Text(f"{state.imbalance_ratio:+.3f}" if state.imbalance_ratio is not None else "—", style="yellow"))
    
    # Absorption Section
    grid.add_row(Text("Absorption Level", style="dim"), Text(f"{state.absorption_level:+.3f}" if state.absorption_level is not None else "—", style="magenta"))

    return Panel(grid, title="[bold]Market Data[/bold]", border_style="green", padding=(0, 1))


def render_signal_evaluation(state: PipelineState) -> Panel:
    grid = Table.grid(padding=(0, 4))
    grid.add_column()
    grid.add_column()

    grid.add_row(Text("Last Check", style="dim"), Text(state.last_signal_check, style="white"))
    
    # Long Signals
    long_text = ", ".join(state.long_signals) if state.long_signals else "None"
    grid.add_row(Text("Long Signals", style="dim"), Text(long_text, style="green"))
    
    # Short Signals
    short_text = ", ".join(state.short_signals) if state.short_signals else "None"
    grid.add_row(Text("Short Signals", style="dim"), Text(short_text, style="red"))
    
    # Failed Conditions
    failed_text = ", ".join(state.failed_conditions) if state.failed_conditions else "All Clear"
    failed_color = "red" if state.failed_conditions else "green"
    grid.add_row(Text("Failed Conditions", style="dim"), Text(failed_text, style=failed_color))

    return Panel(grid, title="[bold]Signal Evaluation[/bold]", border_style="yellow", padding=(0, 1))


def render_position(state: PipelineState) -> Panel:
    grid = Table.grid(padding=(0, 4))
    grid.add_column()
    grid.add_column()

    if state.position_open and state.entry_price:
        grid.add_row(Text("Entry Price", style="dim"), Text(f"${state.entry_price:,.2f}", style="yellow"))
        grid.add_row(Text("Entry Time", style="dim"), Text(state.entry_ts, style="white"))
        pnl = state.current_price - state.entry_price if state.current_price else 0.0
        pnl_color = "green" if pnl >= 0 else "red"
        grid.add_row(Text("Unreal PnL", style="dim"), Text(f"{pnl:+.4f}", style=pnl_color))
    else:
        grid.add_row(Text("No Position", style="dim italic"))
        grid.add_row(Text("Last PnL", style="dim"), Text(f"{state.pnl:+.4f}", style="green" if state.pnl >= 0 else "red"))

    return Panel(grid, title="[bold]Position[/bold]", border_style="cyan", padding=(0, 1))


def render_rejection_log(state: PipelineState) -> Panel:
    t = Table(box=box.SIMPLE_HEAD, show_edge=False, padding=(0, 1), expand=True)
    t.add_column("Time", style="dim", width=9)
    t.add_column("Failed Conditions", style="red")

    # Show recent rejections
    for i in range(min(5, len(state.rejections))):
        r = state.rejections[-(i+1)]
        failed = r.get("failed", [])
        t.add_row(r.get("ts", "—"), ", ".join(failed) if failed else "—")

    if not state.rejections:
        t.add_row("—", "No rejections yet")

    return Panel(t, title="[bold]Recent Rejections[/bold]", border_style="red", padding=(0, 1))


def build_layout(state: PipelineState) -> Layout:
    layout = Layout()

    layout.split_column(
        Layout(render_header(state), name="header", size=6),
        Layout(name="middle", size=10),
        Layout(name="bottom", size=8),
    )

    layout["middle"].split_row(
        Layout(render_market_data(state), name="market", ratio=1),
        Layout(render_signal_evaluation(state), name="signals", ratio=1),
        Layout(render_position(state), name="position", ratio=1),
    )

    layout["bottom"].split_row(
        Layout(render_rejection_log(state), name="rejections", ratio=1),
    )

    return layout


# ─── Main loop ─────────────────────────────────────────────────────────────────

def run(log_dir: Path, refresh: float) -> None:
    console = Console()
    state = PipelineState()

    console.print(f"\n[dim]Watching[/dim] [cyan]{log_dir}[/cyan]  [dim](Ctrl-C to quit)[/dim]\n")
    time.sleep(0.3)

    with Live(console=console, refresh_per_second=1 / refresh, screen=True) as live:
        while True:
            # Find or re-find the log file (handles rotation / new sessions)
            if state._log_path is None or not state._log_path.exists():
                state._log_path = find_latest_log(log_dir)

            if state._log_path:
                events = tail_new_lines(state._log_path, state)
                apply_events(events, state)

            live.update(build_layout(state))
            time.sleep(refresh)


# ─── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Orderflow bot pipeline dashboard")
    parser.add_argument(
        "--log-dir", default=DEFAULT_LOG_DIR,
        help=f"Directory containing MetricsLogger *.jsonl files (default: {DEFAULT_LOG_DIR})",
    )
    parser.add_argument(
        "--refresh", type=float, default=DEFAULT_REFRESH,
        help=f"Refresh interval in seconds (default: {DEFAULT_REFRESH})",
    )
    args = parser.parse_args()

    log_dir = Path(args.log_dir)
    if not log_dir.exists():
        print(f"Log directory not found: {log_dir}")
        print("Create it or pass --log-dir pointing to your metrics output folder.")
        sys.exit(1)

    try:
        run(log_dir, args.refresh)
    except KeyboardInterrupt:
        print("\nDashboard closed.")


if __name__ == "__main__":
    main()
