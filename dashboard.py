"""
Orderflow Bot — Live Terminal Dashboard
========================================
Tails your MetricsLogger JSON log files and prints a live-updating
dashboard in a second terminal while the bot runs.

Usage:
    # Terminal 1 — run the bot normally
    python run_bot.py

    # Terminal 2 — run the dashboard
    python dashboard.py
    python dashboard.py --log-dir orderflow/logs/metrics   # custom path
    python dashboard.py --refresh 0.5                      # faster refresh

Install:
    pip install rich watchdog
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
from rich.columns import Columns
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text


# ─── Config ────────────────────────────────────────────────────────────────────

DEFAULT_LOG_DIR   = "orderflow/logs/metrics"
DEFAULT_REFRESH   = 1.0          # seconds between re-renders
MAX_ORDERS        = 20           # rows in order history table
MAX_ERRORS        = 30           # rows in error log
MAX_SIGNALS       = 10           # rows in recent signals table


# ─── State ─────────────────────────────────────────────────────────────────────

class BotState:
    """Parsed state accumulated from the metrics log stream."""

    def __init__(self) -> None:
        self.entries:  deque[dict] = deque(maxlen=MAX_ORDERS)
        self.exits:    deque[dict] = deque(maxlen=MAX_ORDERS)
        self.rejections: deque[dict] = deque(maxlen=MAX_SIGNALS)
        self.errors:   deque[dict] = deque(maxlen=MAX_ERRORS)
        self.warnings: deque[dict] = deque(maxlen=MAX_ERRORS)

        self.last_signal_ts: str   = "—"
        self.position_open: bool   = False
        self.entry_price: float | None = None
        self.entry_ts: str          = "—"
        self.current_exit_reason: str = "—"

        # Risk state
        self.risk_halted: bool = False
        self.halt_reason: str  = "—"
        self.consecutive_losses: int = 0
        self.daily_pnl_pct: float | None = None

        # Running totals
        self.total_entries: int = 0
        self.total_exits:   int = 0
        self.total_wins:    int = 0
        self.total_losses:  int = 0
        self.gross_pnl:     float = 0.0

        self._file_position: int = 0
        self._log_path: Path | None = None


# ─── Log reader ────────────────────────────────────────────────────────────────

def find_latest_log(log_dir: Path) -> Path | None:
    """Return the most recently modified *.jsonl file in log_dir."""
    if not log_dir.exists():
        return None
    files = sorted(log_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def tail_new_lines(path: Path, state: BotState) -> list[dict]:
    """Read any new lines added to path since the last read."""
    lines = []
    try:
        size = path.stat().st_size
        if size < state._file_position:
            # File was rotated/truncated
            state._file_position = 0
        with path.open("r") as f:
            f.seek(state._file_position)
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    lines.append(json.loads(raw))
                except json.JSONDecodeError:
                    pass
            state._file_position = f.tell()
    except OSError:
        pass
    return lines


def apply_events(events: list[dict], state: BotState) -> None:
    """Update BotState from a list of new log events."""
    for ev in events:
        ev_type = ev.get("event", "")
        ts_raw  = ev.get("ts", "")
        ts      = _fmt_ts(ts_raw)
        data    = ev.get("data", {})

        if ev_type == "entry_signal":
            state.total_entries += 1
            state.position_open = True
            # Support both nested (data.price) and flat (price) formats
            price = float(data.get("price", ev.get("price", 0)))
            state.entry_price   = price
            state.entry_ts      = ts
            state.last_signal_ts = ts
            side = str(data.get("side", ev.get("side", "BUY")))
            state.entries.append({
                "ts":       ts,
                "side":     side,
                "price":    data.get("price", ev.get("price", "—")),
                "qty":      data.get("qty", ev.get("qty", "—")),
                "notional": data.get("notional_usdt", ev.get("notional_usdt", "—")),
                "conditions": data.get("conditions", ev.get("conditions", {})),
            })

        elif ev_type == "exit":
            state.total_exits += 1
            state.position_open     = False
            state.current_exit_reason = data.get("reason", ev.get("reason", "—"))
            # Prefer position_closed.realized_pnl for PnL; use exit.pnl only if present (legacy)
            pnl = data.get("pnl", ev.get("pnl", None))
            if pnl is not None:
                pnl = float(pnl)
                state.gross_pnl += pnl
                if pnl >= 0:
                    state.total_wins += 1
                else:
                    state.total_losses += 1
            state.exits.append({
                "ts":     ts,
                "reason": data.get("reason", ev.get("reason", "—")),
                "pnl":    pnl,
            })

        elif ev_type == "position_closed":
            # Canonical realized PnL (Nautilus fill); also risk telemetry
            rp = data.get("realized_pnl")
            if rp is not None:
                rp = float(rp)
                state.gross_pnl += rp
                if rp >= 0:
                    state.total_wins += 1
                else:
                    state.total_losses += 1
            cl = data.get("consecutive_losses")
            if cl is not None:
                state.consecutive_losses = int(cl)
            dp = data.get("daily_pnl_pct")
            if dp is not None:
                state.daily_pnl_pct = float(dp)

        elif ev_type == "entry_rejected":
            state.last_signal_ts = ts
            state.rejections.append({
                "ts":     ts,
                "failed": data.get("failed", ev.get("failed", [])),
            })

        elif ev_type == "risk_halt":
            state.risk_halted  = True
            state.halt_reason  = data.get("reason", "—")
            state.errors.append({"ts": ts, "msg": f"RISK HALT: {state.halt_reason}"})

        elif ev_type in ("error", "ERROR"):
            state.errors.append({"ts": ts, "msg": str(data.get("msg", data))})

        elif ev_type in ("warning", "WARNING"):
            state.warnings.append({"ts": ts, "msg": str(data.get("msg", data))})


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


def _pnl_color(pnl: float | None) -> str:
    if pnl is None:
        return "dim"
    return "green" if pnl >= 0 else "red"


def render_header(state: BotState) -> Panel:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d  %H:%M:%S UTC")

    status_color = "red bold" if state.risk_halted else ("yellow bold" if state.position_open else "green bold")
    status_text  = "[HALTED]" if state.risk_halted else ("[IN POSITION]" if state.position_open else "[FLAT]")

    win_rate = (
        f"{state.total_wins / max(state.total_exits, 1) * 100:.0f}%"
        if state.total_exits > 0 else "—"
    )
    pnl_color = "green" if state.gross_pnl >= 0 else "red"

    grid = Table.grid(padding=(0, 3))
    grid.add_column(justify="left")
    grid.add_column(justify="left")
    grid.add_column(justify="left")
    grid.add_column(justify="left")
    grid.add_column(justify="right")

    grid.add_row(
        Text(f"Status  ", style="dim") + Text(status_text, style=status_color),
        Text(f"Entries  ", style="dim") + Text(str(state.total_entries), style="cyan"),
        Text(f"Exits  ", style="dim") + Text(str(state.total_exits), style="cyan"),
        Text(f"Win rate  ", style="dim") + Text(win_rate, style="cyan"),
        Text(now, style="dim"),
    )
    grid.add_row(
        Text(f"Halt  ", style="dim") + Text(state.halt_reason if state.risk_halted else "none", style="dim"),
        Text(f"Wins  ", style="dim") + Text(str(state.total_wins), style="green"),
        Text(f"Losses  ", style="dim") + Text(str(state.total_losses), style="red"),
        Text(f"Gross PnL  ", style="dim") + Text(f"{state.gross_pnl:+.4f}", style=pnl_color),
        Text(""),
    )

    return Panel(grid, title="[bold]Orderflow Bot Monitor[/bold]", border_style="bright_blue", padding=(0, 1))


def render_position(state: BotState) -> Panel:
    grid = Table.grid(padding=(0, 4))
    grid.add_column()
    grid.add_column()

    if state.position_open and state.entry_price:
        grid.add_row(Text("Entry price", style="dim"),  Text(f"{state.entry_price:,.2f}", style="yellow"))
        grid.add_row(Text("Entry time",  style="dim"),  Text(state.entry_ts, style="white"))
        grid.add_row(Text("Last exit reason", style="dim"), Text(state.current_exit_reason, style="dim"))
    else:
        grid.add_row(Text("No open position", style="dim italic"))
        grid.add_row(Text("Last exit", style="dim"), Text(state.current_exit_reason, style="dim"))

    color = "yellow" if state.position_open else "dim"
    return Panel(grid, title="[bold]Position[/bold]", border_style=color, padding=(0, 1))


def render_risk(state: BotState) -> Panel:
    grid = Table.grid(padding=(0, 4))
    grid.add_column()
    grid.add_column()

    grid.add_row(
        Text("Halted", style="dim"),
        Text("YES", style="red bold") if state.risk_halted else Text("no", style="green"),
    )
    grid.add_row(
        Text("Consec. losses", style="dim"),
        Text(str(state.consecutive_losses),
             style="red" if state.consecutive_losses >= 3 else "white"),
    )
    dp = state.daily_pnl_pct
    grid.add_row(
        Text("Daily PnL %", style="dim"),
        Text(
            f"{dp:+.2f}%" if dp is not None else "—",
            style="dim" if dp is None else ("green" if dp >= 0 else "red"),
        ),
    )
    grid.add_row(
        Text("Halt reason", style="dim"),
        Text(state.halt_reason, style="red dim"),
    )

    color = "red" if state.risk_halted else "dim"
    return Panel(grid, title="[bold]Risk[/bold]", border_style=color, padding=(0, 1))


def render_orders(state: BotState) -> Panel:
    t = Table(box=box.SIMPLE_HEAD, show_edge=False, padding=(0, 1), expand=True)
    t.add_column("Time",    style="dim",    width=9)
    t.add_column("Side",    width=6)
    t.add_column("Price",   justify="right", width=12)
    t.add_column("Qty",     justify="right", width=10)
    t.add_column("Notional", justify="right", width=12)
    t.add_column("PnL",     justify="right", width=10)
    t.add_column("Exit reason", style="dim")

    # Merge entries and exits into a unified timeline
    events = []
    for e in state.entries:
        events.append(("entry", e))
    for e in state.exits:
        events.append(("exit", e))
    events.sort(key=lambda x: x[1].get("ts", ""), reverse=True)

    for kind, ev in events[:MAX_ORDERS]:
        if kind == "entry":
            notional = ev.get("notional", "—")
            notional_str = f"${float(notional):,.0f}" if notional != "—" else "—"
            side = str(ev.get("side", "BUY")).upper()
            side_txt = (
                Text("BUY", style="green bold")
                if side == "BUY"
                else Text("SELL", style="red bold")
            )
            t.add_row(
                ev["ts"],
                side_txt,
                f"{float(ev['price']):,.2f}" if ev["price"] != "—" else "—",
                str(ev["qty"]),
                notional_str,
                "—",
                "—",
            )
        else:
            pnl = ev.get("pnl")
            pnl_str = f"{pnl:+.4f}" if pnl is not None else "—"
            t.add_row(
                ev["ts"],
                Text("SELL", style="red bold"),
                "—", "—", "—",
                Text(pnl_str, style=_pnl_color(pnl)),
                str(ev["reason"]),
            )

    if not events:
        t.add_row("—", "—", "—", "—", "—", "—", "no orders yet")

    return Panel(t, title="[bold]Order History[/bold]", border_style="bright_blue", padding=(0, 1))


def render_rejections(state: BotState) -> Panel:
    t = Table(box=box.SIMPLE_HEAD, show_edge=False, padding=(0, 1), expand=True)
    t.add_column("Time",   style="dim", width=9)
    t.add_column("Failed conditions", style="yellow")

    for r in reversed(list(state.rejections)):
        failed = r.get("failed", [])
        t.add_row(r["ts"], ", ".join(failed) if failed else "—")

    if not state.rejections:
        t.add_row("—", "no rejections yet")

    return Panel(t, title="[bold]Entry Rejections[/bold]", border_style="yellow", padding=(0, 1))


def render_errors(state: BotState) -> Panel:
    t = Table(box=box.SIMPLE_HEAD, show_edge=False, padding=(0, 1), expand=True)
    t.add_column("Time",    style="dim", width=9)
    t.add_column("Level",   width=8)
    t.add_column("Message")

    all_msgs = (
        [("ERROR",   e) for e in state.errors]
        + [("WARN",  w) for w in state.warnings]
    )
    all_msgs.sort(key=lambda x: x[1].get("ts", ""), reverse=True)

    for level, msg in all_msgs[:MAX_ERRORS]:
        color = "red" if level == "ERROR" else "yellow"
        t.add_row(
            msg.get("ts", "—"),
            Text(level, style=f"{color} bold"),
            Text(str(msg.get("msg", "—")), style=color),
        )

    if not all_msgs:
        t.add_row("—", "—", Text("no errors", style="dim"))

    return Panel(t, title="[bold]Errors & Warnings[/bold]", border_style="red", padding=(0, 1))


def render_conditions_legend() -> Panel:
    """Reference panel: what each condition means at a glance."""
    t = Table(box=box.SIMPLE_HEAD, show_edge=False, padding=(0, 1), expand=True)
    t.add_column("Condition",       style="cyan", width=22)
    t.add_column("Meaning",         style="dim")

    rows = [
        ("cvd_rising / cvd_still_rising", "CVD EMA up — net buying momentum"),
        ("imbalance / imb_holding",       "Bar buy/sell imbalance vs threshold"),
        ("no_sell_absorption",            "Absorption ≥ −min (no heavy large sells)"),
        ("stacked_imb",                   "Consecutive bars same-direction imbalance"),
        ("ob_imbalance / ob_bid_heavy",   "Order book bid vs ask imbalance"),
        ("large_dom",                     "Large-trade buy vs sell dominance"),
        ("no_bearish_div / bullish_div", "Delta divergence not opposing entry"),
    ]
    for name, desc in rows:
        t.add_row(name, desc)

    return Panel(t, title="[bold]Condition Reference[/bold]", border_style="dim", padding=(0, 1))


def build_layout(state: BotState) -> Layout:
    layout = Layout()

    layout.split_column(
        Layout(render_header(state),  name="header",  size=6),
        Layout(name="middle",                         size=8),
        Layout(name="bottom"),
    )

    layout["middle"].split_row(
        Layout(render_position(state), name="position", ratio=2),
        Layout(render_risk(state),     name="risk",     ratio=1),
        Layout(render_conditions_legend(), name="legend", ratio=3),
    )

    layout["bottom"].split_column(
        Layout(render_orders(state),     name="orders",     ratio=3),
        Layout(name="lower",                               ratio=2),
    )

    layout["lower"].split_row(
        Layout(render_rejections(state), name="rejections", ratio=1),
        Layout(render_errors(state),     name="errors",     ratio=1),
    )

    return layout


# ─── Main loop ─────────────────────────────────────────────────────────────────

def run(log_dir: Path, refresh: float) -> None:
    console = Console()
    state   = BotState()

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
    parser = argparse.ArgumentParser(description="Orderflow bot live dashboard")
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
        print("\nExpected log format (one JSON object per line):")
        print('  {"event": "entry_signal", "ts": 1700000000000, "data": {"price": 43200.0, ...}}')
        print('  {"event": "exit",         "ts": 1700000060000, "data": {"reason": "trailing_stop"}}')
        print('  {"event": "entry_rejected","ts": ..., "data": {"failed": ["cvd_rising","imbalance"]}}')
        print('  {"event": "error",         "ts": ..., "data": {"msg": "something went wrong"}}')
        sys.exit(1)

    try:
        run(log_dir, args.refresh)
    except KeyboardInterrupt:
        print("\nDashboard closed.")


if __name__ == "__main__":
    main()