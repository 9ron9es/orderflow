"""
Orderflow Bot — Live Terminal Dashboard v2 (Focused View)
============================================================
Redesigned dashboard focused on signal evaluation loop in real-time.

Shows:
  • Signal evaluation frequency and cycle time
  • Latest rejection reasons with timestamps
  • Market data updates (price, volume)
  • Position state and PnL
  • Trades strip (venue fills, entry signals, exits, cancels) — reads any of:
      fill | order_fill | venue_fill | trade | entry_signal | exit | entry_cancelled_stale_limit

Usage:
    python dashboard_v2.py
    python dashboard_v2.py --log-dir orderflow/logs/metrics
    python dashboard_v2.py --refresh 0.5
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
DEFAULT_REFRESH   = 0.5          # seconds between re-renders (faster)
MAX_REJECTION_LOG = 50           # rows in rejection log
MAX_TRADES        = 40           # rows in unified trades strip
# Metrics event names treated as venue / execution prints (extensible).
FILL_EVENT_NAMES  = frozenset({"fill", "order_fill", "venue_fill"})
TRADE_EVENT_NAMES = frozenset({"trade", "venue_trade"})


# ─── State ─────────────────────────────────────────────────────────────────────

class BotState:
    """Parsed state accumulated from the metrics log stream."""

    def __init__(self) -> None:
        # Signal evaluation loop tracking
        self.last_eval_ts: str   = "—"
        self.eval_count: int     = 0
        self.eval_rate_per_sec: float = 0.0
        self.last_eval_time: float = 0.0
        self.eval_times: deque[float] = deque(maxlen=10)  # Track last 10 evals for rate calc
        
        # Rejections with detailed info
        self.rejections: deque[dict] = deque(maxlen=MAX_REJECTION_LOG)
        self.rejection_reasons: dict[str, int] = {}  # Count by reason
        
        # Orders / unified trade ledger (fills + signals + exits)
        self.entries:  deque[dict] = deque(maxlen=MAX_TRADES)
        self.exits:    deque[dict] = deque(maxlen=MAX_TRADES)
        self.trades:   deque[dict] = deque(maxlen=MAX_TRADES)

        # Position state
        self.position_open: bool   = False
        self.entry_price: float | None = None
        self.entry_ts: str          = "—"

        # Market data state
        self.current_price: float | None = None
        self.cvd: float | None = None
        self.cvd_trend: str | None = None
        self.ob_imbalance: float | None = None
        self.imbalance_ratio: float | None = None
        self.absorption: float | None = None
        self.last_market_update: str = "—"
        
        # Market update tracking for evaluation loop
        self.market_updates: deque[dict] = deque(maxlen=MAX_REJECTION_LOG)
        self.market_update_count: int = 0
        
        # Position state tracking
        self.position_checks: deque[dict] = deque(maxlen=MAX_REJECTION_LOG)

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

        self._file_position: int = 0  # Start at beginning to read existing data
        self._log_path: Path | None = None
        self._initialized: bool = False  # Track if we've read initial data


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


def _ts_ms(raw: Any) -> int:
    """Normalize metrics ``ts`` to unix milliseconds for ordering trade rows."""
    if raw is None:
        return 0
    try:
        if isinstance(raw, (int, float)):
            v = float(raw)
            return int(v if v > 1e11 else v * 1000)
    except (TypeError, ValueError):
        pass
    return 0


def apply_events(events: list[dict], state: BotState) -> None:
    """Update BotState from a list of new log events."""
    current_time = time.time()
    
    for ev in events:
        ev_type = ev.get("event", "")
        ts_raw  = ev.get("ts", "")
        ts      = _fmt_ts(ts_raw)
        data    = ev.get("data", {})

        if ev_type == "entry_rejected":
            # Track signal evaluation
            state.eval_count += 1
            state.last_eval_ts = ts
            state.eval_times.append(current_time)
            
            # Calculate evaluation rate per second
            if len(state.eval_times) >= 2:
                time_span = state.eval_times[-1] - state.eval_times[0]
                if time_span > 0:
                    state.eval_rate_per_sec = len(state.eval_times) / time_span
            
            # Track rejection reasons
            failed = data.get("failed", ev.get("failed", []))
            if isinstance(failed, list):
                for reason in failed:
                    state.rejection_reasons[reason] = state.rejection_reasons.get(reason, 0) + 1
            
            state.rejections.append({
                "ts": ts,
                "failed": failed if isinstance(failed, list) else [str(failed)],
            })

        elif ev_type == "entry_signal":
            state.total_entries += 1
            state.position_open = True
            price = float(data.get("price", ev.get("price", 0)))
            state.entry_price = price
            state.entry_ts = ts
            side = str(data.get("side", ev.get("side", "BUY")))
            state.entries.append({
                "ts": ts,
                "side": side,
                "price": data.get("price", ev.get("price", "—")),
                "qty": data.get("qty", ev.get("qty", "—")),
                "notional": data.get("notional_usdt", ev.get("notional_usdt", "—")),
            })
            state.trades.append({
                "ts": ts,
                "ts_ms": _ts_ms(ts_raw),
                "kind": "ENTRY",
                "side": side,
                "price": data.get("price", ev.get("price")),
                "qty": data.get("qty", ev.get("qty")),
                "pnl": None,
                "fee": None,
                "detail": str(data.get("label", "")),
            })

        elif ev_type == "exit":
            state.total_exits += 1
            state.position_open = False
            pnl = data.get("pnl", ev.get("pnl", None))
            if pnl is not None:
                pnl = float(pnl)
                state.gross_pnl += pnl
                if pnl >= 0:
                    state.total_wins += 1
                else:
                    state.total_losses += 1
            state.exits.append({
                "ts": ts,
                "reason": data.get("reason", ev.get("reason", "—")),
                "pnl": pnl,
            })
            state.trades.append({
                "ts": ts,
                "ts_ms": _ts_ms(ts_raw),
                "kind": "EXIT",
                "side": None,
                "price": None,
                "qty": None,
                "pnl": pnl,
                "fee": None,
                "detail": str(data.get("reason", ev.get("reason", "—"))),
            })

        elif ev_type in FILL_EVENT_NAMES or ev_type in TRADE_EVENT_NAMES:
            pnl_v = data.get("pnl")
            if pnl_v is not None:
                try:
                    pnl_v = float(pnl_v)
                except (TypeError, ValueError):
                    pnl_v = None
            fee_v = data.get("fee")
            if fee_v is not None:
                try:
                    fee_v = float(fee_v)
                except (TypeError, ValueError):
                    fee_v = None
            kind = "FILL" if ev_type in FILL_EVENT_NAMES else "TRADE"
            tid = data.get("trade_id") or data.get("id") or ""
            state.trades.append({
                "ts": ts,
                "ts_ms": _ts_ms(ts_raw),
                "kind": kind,
                "side": str(data.get("side", "—")),
                "price": data.get("price"),
                "qty": data.get("qty"),
                "pnl": pnl_v,
                "fee": fee_v,
                "detail": str(tid) if tid else ev_type,
            })

        elif ev_type == "entry_cancelled_stale_limit":
            lp = data.get("limit_px")
            cp = data.get("current_px")
            state.trades.append({
                "ts": ts,
                "ts_ms": _ts_ms(ts_raw),
                "kind": "CANCEL",
                "side": None,
                "price": lp,
                "qty": None,
                "pnl": None,
                "fee": None,
                "detail": f"stale limit {lp} @ {cp}",
            })

        elif ev_type == "position_closed":
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

        elif ev_type == "risk_halt":
            state.risk_halted = True
            state.halt_reason = data.get("reason", "—")

        elif ev_type == "position_check":
            # Track position state for debugging
            state.position_checks.append({
                "ts": ts,
                "is_long": data.get("is_long", False),
                "is_short": data.get("is_short", False),
                "is_flat": data.get("is_flat", True),
                "current_price": data.get("current_price"),
                "pending_entry": data.get("pending_entry", False),
            })

        elif ev_type == "market_update":
            # Update market data state
            state.current_price = data.get("price")
            state.cvd = data.get("cvd")
            state.cvd_trend = data.get("cvd_trend")
            state.ob_imbalance = data.get("ob_imbalance")
            state.imbalance_ratio = data.get("imbalance_ratio")
            state.absorption = data.get("absorption")
            state.last_market_update = ts
            
            # Track market updates for evaluation loop display
            state.market_update_count += 1
            state.market_updates.append({
                "ts": ts,
                "price": data.get("price"),
                "cvd": data.get("cvd"),
                "cvd_trend": data.get("cvd_trend"),
                "ob_imbalance": data.get("ob_imbalance"),
            })


# ─── Rendering ─────────────────────────────────────────────────────────────────

def _fmt_ts(raw: Any) -> str:
    """Convert a unix timestamp (s or ms) or ISO string to HH:MM:SS."""
    if not raw:
        return "—"
    try:
        if isinstance(raw, (int, float)):
            ts_s = raw / 1000.0 if raw > 1e10 else float(raw)
            return datetime.fromtimestamp(ts_s, tz=timezone.utc).strftime("%H:%M:%S")
        return str(raw)[11:19]
    except Exception:
        return str(raw)[:8]


def _pnl_color(pnl: float | None) -> str:
    if pnl is None:
        return "dim"
    return "green" if pnl >= 0 else "red"


def render_header(state: BotState) -> Panel:
    """Main status header."""
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
        Text(f"Eval rate  ", style="dim") + Text(f"{state.eval_rate_per_sec:.1f}/s", style="bright_cyan"),
        Text(f"Wins  ", style="dim") + Text(str(state.total_wins), style="green"),
        Text(f"Losses  ", style="dim") + Text(str(state.total_losses), style="red"),
        Text(f"Gross PnL  ", style="dim") + Text(f"{state.gross_pnl:+.4f}", style=pnl_color),
        Text(""),
    )

    return Panel(grid, title="[bold]>> Orderflow Signal Loop[/bold]", border_style="bright_cyan", padding=(0, 1))


def render_evaluation_loop(state: BotState) -> Panel:
    """Show the signal evaluation loop - displays rejections, position checks, or market updates."""
    t = Table(box=box.SIMPLE_HEAD, show_edge=False, padding=(0, 1), expand=True)
    t.add_column("Time",       style="dim",   width=9)
    t.add_column("Status",     width=8)
    t.add_column("Activity",   style="cyan")
    t.add_column("Count",      justify="right", width=6)

    # Show rejections if available, otherwise show position checks, then market updates
    if state.rejections:
        rejections_list = list(state.rejections)
        for rej in reversed(rejections_list[-30:]):  # Show last 30
            failed = rej.get("failed", [])
            reason_str = ", ".join(str(r) for r in failed) if failed else "—"
            
            reason_style = "yellow"
            status = "REJECT"
            
            t.add_row(
                rej["ts"],
                Text(status, style="red dim"),
                Text(reason_str, style=reason_style),
                Text("", style="dim"),
            )
    elif state.position_checks:
        # Show position checks to see why entries aren't happening
        checks_list = list(state.position_checks)
        for check in reversed(checks_list[-30:]):  # Show last 30
            is_long = check.get("is_long", False)
            is_short = check.get("is_short", False)
            is_flat = check.get("is_flat", True)
            pending = check.get("pending_entry", False)
            price = check.get("current_price", "—")
            
            if is_long:
                status = "LONG"
                activity = f"Position: LONG | Price: {price}"
                status_style = "green dim"
            elif is_short:
                status = "SHORT"
                activity = f"Position: SHORT | Price: {price}"
                status_style = "red dim"
            else:
                status = "FLAT"
                activity = f"Position: FLAT | Price: {price}"
                status_style = "cyan dim"
                if pending:
                    activity += " | PENDING ENTRY"
            
            t.add_row(
                check["ts"],
                Text(status, style=status_style),
                Text(activity, style="cyan"),
                Text("", style="dim"),
            )
    elif state.market_updates:
        # Show market updates as last resort
        updates_list = list(state.market_updates)
        for update in reversed(updates_list[-30:]):  # Show last 30
            price = update.get("price", "—")
            cvd = update.get("cvd", "—")
            trend = update.get("cvd_trend", "—")
            
            # Format market data string
            market_str = f"Price: {price}"
            if cvd is not None:
                cvd_str = f"{cvd:.3f}"
                market_str += f" | CVD: {cvd_str}"
            if trend:
                trend_icon = "UP" if trend == "rising" else "DOWN" if trend == "falling" else "~"
                market_str += f" | {trend_icon}:{trend.upper()}"
            
            status = "MARKET"
            
            t.add_row(
                update["ts"],
                Text(status, style="cyan dim"),
                Text(market_str, style="cyan"),
                Text(str(state.market_update_count), style="dim"),
            )
    
    if not state.rejections and not state.position_checks and not state.market_updates:
        t.add_row("—", "—", Text("initializing...", style="dim"), "—")

    return Panel(t, title="[bold]>> Trading Activity[/bold]", border_style="bright_cyan", padding=(0, 1))


def render_rejection_stats(state: BotState) -> Panel:
    """Show rejection reason statistics."""
    t = Table(box=box.SIMPLE_HEAD, show_edge=False, padding=(0, 1), expand=True)
    t.add_column("Reason",  style="cyan", width=20)
    t.add_column("Count",   justify="right", width=8)
    t.add_column("% Total", justify="right", width=8)

    total = sum(state.rejection_reasons.values())
    
    # Sort by count descending
    sorted_reasons = sorted(state.rejection_reasons.items(), key=lambda x: x[1], reverse=True)
    
    for reason, count in sorted_reasons[:10]:
        pct = (count / total * 100) if total > 0 else 0
        t.add_row(
            Text(str(reason), style="cyan"),
            Text(str(count)),
            Text(f"{pct:.1f}%", style="dim"),
        )

    if not state.rejection_reasons:
        t.add_row("—", "—", "—")

    return Panel(t, title="[bold]| Stats Breakdown[/bold]", border_style="bright_cyan", padding=(0, 1))


def render_position(state: BotState) -> Panel:
    """Current position info."""
    grid = Table.grid(padding=(0, 2))
    grid.add_column()
    grid.add_column()

    if state.position_open and state.entry_price:
        grid.add_row(Text("Entry price", style="dim"),  Text(f"{state.entry_price:,.2f}", style="yellow"))
        grid.add_row(Text("Entry time",  style="dim"),  Text(state.entry_ts, style="white"))
    else:
        grid.add_row(Text("Status", style="dim"), Text("NO OPEN POSITION", style="dim"))

    color = "yellow" if state.position_open else "dim"
    return Panel(grid, title="[bold]* Position[/bold]", border_style=color, padding=(0, 1))


def render_market_data(state: BotState) -> Panel:
    """Current market data from market_update events."""
    grid = Table.grid(padding=(0, 2))
    grid.add_column()
    grid.add_column()

    if state.current_price is not None:
        grid.add_row(Text("Price", style="dim"), Text(f"{state.current_price:,.2f}", style="cyan bold"))
        
        if state.cvd is not None:
            cvd_color = "green" if state.cvd > 0 else "red" if state.cvd < 0 else "dim"
            grid.add_row(Text("CVD", style="dim"), Text(f"{state.cvd:.6f}", style=cvd_color))
            
        if state.cvd_trend:
            trend_color = "green" if state.cvd_trend == "rising" else "red" if state.cvd_trend == "falling" else "dim"
            trend_icon = "UP" if state.cvd_trend == "rising" else "DOWN" if state.cvd_trend == "falling" else "~"
            grid.add_row(Text("CVD Trend", style="dim"), Text(f"{trend_icon} {state.cvd_trend.upper()}", style=trend_color))
            
        if state.ob_imbalance is not None:
            imb_color = "green" if state.ob_imbalance > 0 else "red" if state.ob_imbalance < 0 else "dim"
            grid.add_row(Text("OB Imbalance", style="dim"), Text(f"{state.ob_imbalance:.4f}", style=imb_color))
            
        if state.imbalance_ratio is not None:
            grid.add_row(Text("Imbalance Ratio", style="dim"), Text(f"{state.imbalance_ratio:.4f}", style="cyan"))
            
        if state.absorption is not None:
            grid.add_row(Text("Absorption", style="dim"), Text(f"{state.absorption:.4f}", style="yellow"))
            
        grid.add_row(Text("Last Update", style="dim"), Text(state.last_market_update, style="dim"))
    else:
        grid.add_row(Text("Status", style="dim"), Text("NO MARKET DATA", style="dim"))

    color = "cyan" if state.current_price is not None else "dim"
    return Panel(grid, title="[bold]| Market Data[/bold]", border_style=color, padding=(0, 1))


def _fmt_trade_price(v: Any) -> str:
    if v is None or v == "—":
        return "—"
    try:
        return f"{float(v):,.2f}"
    except (TypeError, ValueError):
        return str(v)


def render_trades(state: BotState) -> Panel:
    """Unified execution + intent feed from metrics JSONL (newest first)."""
    t = Table(box=box.SIMPLE_HEAD, show_edge=False, padding=(0, 1), expand=True)
    t.add_column("Time", style="dim", width=9)
    t.add_column("Type", width=7)
    t.add_column("Side", width=6)
    t.add_column("Price", justify="right", width=12)
    t.add_column("Qty", justify="right", width=10)
    t.add_column("PnL / Fee", justify="right", width=11)
    t.add_column("Detail", style="dim")

    rows = sorted(state.trades, key=lambda r: r.get("ts_ms", 0), reverse=True)
    kind_style = {
        "FILL": "green",
        "TRADE": "green",
        "ENTRY": "yellow",
        "EXIT": "cyan",
        "CANCEL": "magenta",
    }

    for r in rows[:MAX_TRADES]:
        k = str(r.get("kind", "—"))
        side_raw = r.get("side")
        if side_raw is None:
            side_raw = "—"
        side_u = str(side_raw).upper()
        if side_u in ("BUY", "BULL", "LONG"):
            side_txt = Text(side_u[:4], style="green bold")
        elif side_u in ("SELL", "BEAR", "SHORT"):
            side_txt = Text(side_u[:4], style="red bold")
        else:
            side_txt = Text(str(side_raw)[:6], style="dim")

        pnl = r.get("pnl")
        fee = r.get("fee")
        if pnl is not None:
            pf = Text(f"{float(pnl):+.4f}", style=_pnl_color(float(pnl)))
        elif fee is not None:
            pf = Text(f"{float(fee):.4f}", style="dim")
        else:
            pf = Text("—", style="dim")

        t.add_row(
            r.get("ts", "—"),
            Text(k, style=kind_style.get(k, "white")),
            side_txt,
            _fmt_trade_price(r.get("price")),
            str(r.get("qty") or "—"),
            pf,
            str(r.get("detail") or "")[:48],
        )

    if not rows:
        t.add_row(
            "—",
            "—",
            "—",
            "—",
            "—",
            Text("—", style="dim"),
            Text(
                "No trade rows yet. Venue fills → fill; intent → entry_signal / exit.",
                style="dim",
            ),
        )

    return Panel(
        t,
        title="[bold]^ Trades[/bold] (fills . entries . exits . cancels)",
        border_style="bright_blue",
        padding=(0, 1),
    )


def build_layout(state: BotState) -> Layout:
    layout = Layout()

    layout.split_column(
        Layout(render_header(state), name="header", size=5),
        Layout(name="main", ratio=3),
        Layout(render_trades(state), name="trades", ratio=2),
    )

    layout["main"].split_row(
        Layout(render_evaluation_loop(state), name="loop", ratio=2),
        Layout(name="right", ratio=1),
    )

    layout["right"].split_column(
        Layout(render_market_data(state), name="market", ratio=1),
        Layout(render_rejection_stats(state), name="stats", ratio=1),
        Layout(render_position(state), name="position", ratio=1),
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
            if state._log_path is None or not state._log_path.exists():
                state._log_path = find_latest_log(log_dir)

            if state._log_path:
                # If this is the first time we found the log file, read all existing data
                if not state._initialized:
                    # Read entire file to get existing market data
                    try:
                        with state._log_path.open("r") as f:
                            for raw in f:
                                raw = raw.strip()
                                if not raw:
                                    continue
                                try:
                                    event = json.loads(raw)
                                    apply_events([event], state)
                                except json.JSONDecodeError:
                                    pass
                        state._file_position = state._log_path.stat().st_size
                        state._initialized = True
                    except OSError:
                        pass
                else:
                    # Only read new lines after initial load
                    events = tail_new_lines(state._log_path, state)
                    apply_events(events, state)

            live.update(build_layout(state))
            time.sleep(refresh)


# ─── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Orderflow signal evaluation dashboard v2")
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
        sys.exit(1)

    try:
        run(log_dir, args.refresh)
    except KeyboardInterrupt:
        print("\nDashboard closed.")


if __name__ == "__main__":
    main()
