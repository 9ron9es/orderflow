"""
Pre-trade and account-level risk checks (kill switch, stale data, spread, daily loss).

Fixes applied vs original:
  - max_leverage is now accepted and enforced in check_leverage()
  - day_start_equity is persisted to disk (JSON) so a restart mid-session
    does not silently reset the drawdown reference to the post-loss equity
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC
from datetime import date
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nautilus_trader.model.book import OrderBook
    from nautilus_trader.portfolio.base import PortfolioFacade


@dataclass(slots=True)
class RiskCheckResult:
    ok: bool
    reason: str = ""


class PreTradeRiskStack:
    """
    Institutional-style pre-trade gates applied in order before opening risk.

    Gate order (same as _check_entry call sequence):
      1. kill_switch
      2. stale_tick
      3. spread_and_depth   (when require_orderbook=True)
      4. daily_loss
      5. leverage           (when max_leverage is set)

    Notes
    -----
    - day_start_equity is written to ``equity_state_path`` on first read each
      UTC day and reloaded on startup, so a mid-session restart does not reset
      the drawdown reference.
    - Kill switch: presence of ``kill_switch_path`` file blocks new entries.
    - Halted flag is sticky within a session; restart clears it (intentional —
      operator must resolve the halt condition before restarting).
    """

    def __init__(
        self,
        *,
        max_daily_loss_pct: float,
        max_consecutive_losses: int,
        max_spread_bps: float,
        stale_tick_ms: float,
        min_top_of_book_qty: float,
        kill_switch_path: str | None,
        max_leverage: float | None = None,           # FIX: now accepted and enforced
        equity_state_path: str | None = None,        # FIX: persistence for crash recovery
    ) -> None:
        self._max_daily_loss_pct = max_daily_loss_pct
        self._max_consecutive_losses = max_consecutive_losses
        self._max_spread_bps = max_spread_bps
        self._stale_tick_ms = stale_tick_ms
        self._min_top_of_book_qty = min_top_of_book_qty
        self._kill_switch_path = Path(kill_switch_path) if kill_switch_path else None
        self._max_leverage = max_leverage

        # Equity state persistence
        self._equity_state_path = Path(equity_state_path) if equity_state_path else None

        # Runtime state
        self._day: date | None = None
        self._day_start_equity: float | None = None
        self._consecutive_losses: int = 0
        self._halted: bool = False
        self._halt_reason: str = ""

        # Load persisted equity state on startup
        self._load_equity_state()

    # ── Public properties ──────────────────────────────────────────────────────

    @property
    def halted(self) -> bool:
        return self._halted

    @property
    def halt_reason(self) -> str:
        return self._halt_reason

    @property
    def consecutive_losses(self) -> int:
        return self._consecutive_losses

    def daily_pnl_pct(self, equity: float | None) -> float | None:
        """
        Return session PnL vs start-of-day equity (%), or None if unknown.

        Positive = profit since UTC day start; negative = drawdown.
        """
        if equity is None or self._day_start_equity is None or self._day_start_equity <= 0:
            return None
        return (equity - self._day_start_equity) / self._day_start_equity * 100.0

    # ── Halt control ───────────────────────────────────────────────────────────

    def halt(self, reason: str) -> None:
        self._halted = True
        self._halt_reason = reason

    # ── Trade outcome feedback ─────────────────────────────────────────────────

    def on_position_closed_pnl(self, realized_pnl: float) -> None:
        if realized_pnl < 0:
            self._consecutive_losses += 1
        else:
            self._consecutive_losses = 0
        if self._consecutive_losses >= self._max_consecutive_losses:
            self.halt(f"max_consecutive_losses>={self._max_consecutive_losses}")

    # ── Pre-trade checks ───────────────────────────────────────────────────────

    def check_kill_switch(self) -> RiskCheckResult:
        """Block new entries if kill switch file exists or session is halted."""
        if self._kill_switch_path and self._kill_switch_path.exists():
            return RiskCheckResult(False, "kill_switch_file_present")
        if self._halted:
            return RiskCheckResult(False, f"halted:{self._halt_reason}")
        return RiskCheckResult(True)

    def check_stale_tick(self, last_tick_ts_ms: float, now_ts_ms: float) -> RiskCheckResult:
        """Block entries when the last received tick is too old."""
        age_ms = now_ts_ms - last_tick_ts_ms
        if age_ms > self._stale_tick_ms:
            return RiskCheckResult(False, f"stale_tick:{age_ms:.0f}ms>{self._stale_tick_ms}ms")
        return RiskCheckResult(True)

    def check_spread_and_depth(self, book: "OrderBook | None") -> RiskCheckResult:
        """Gate on spread width and minimum top-of-book liquidity."""
        if book is None or not book.best_bid_price() or not book.best_ask_price():
            return RiskCheckResult(False, "no_top_of_book")

        bid = float(book.best_bid_price())
        ask = float(book.best_ask_price())
        mid = (bid + ask) / 2.0
        if mid <= 0:
            return RiskCheckResult(False, "invalid_mid")

        spread_bps = (ask - bid) / mid * 10_000.0
        if spread_bps > self._max_spread_bps:
            return RiskCheckResult(False, f"spread_bps={spread_bps:.1f}>{self._max_spread_bps}")

        if self._min_top_of_book_qty > 0:
            bs = book.best_bid_size()
            as_ = book.best_ask_size()
            if bs is None or as_ is None:
                return RiskCheckResult(False, "no_sizes")
            if float(bs) < self._min_top_of_book_qty or float(as_) < self._min_top_of_book_qty:
                return RiskCheckResult(False, "insufficient_top_depth")

        return RiskCheckResult(True)

    def check_daily_loss(self, equity: float | None) -> RiskCheckResult:
        """
        Compare current account equity to start-of-UTC-day snapshot.

        The start-of-day equity is written to disk on first observation so a
        mid-session restart does not silently reset the drawdown reference.

        Parameters
        ----------
        equity : float | None
            Current account equity in quote currency. If None, check is skipped.
        """
        if equity is None:
            return RiskCheckResult(True)

        now = datetime.now(UTC)
        self._maybe_roll_day(now)

        if self._day_start_equity is None:
            # First equity observation of the day — persist it
            self._day_start_equity = equity
            self._persist_equity_state(now.date())
            return RiskCheckResult(True)

        dd_pct = (
            (self._day_start_equity - equity)
            / max(self._day_start_equity, 1e-12)
            * 100.0
        )
        if dd_pct >= self._max_daily_loss_pct:
            self.halt(f"daily_loss_pct={dd_pct:.2f}>={self._max_daily_loss_pct}")
            return RiskCheckResult(False, "daily_loss_limit")

        return RiskCheckResult(True)

    def check_leverage(self, notional: float, equity: float | None) -> RiskCheckResult:
        """
        FIX: max_leverage was defined in config but never enforced anywhere.

        Blocks entry when (notional / equity) would exceed max_leverage.

        Parameters
        ----------
        notional : float
            Full notional value of the proposed position (price × qty).
        equity : float | None
            Current account equity. If None or max_leverage is not set, skipped.
        """
        if self._max_leverage is None or equity is None or equity <= 0:
            return RiskCheckResult(True)

        effective_leverage = notional / equity
        if effective_leverage > self._max_leverage:
            return RiskCheckResult(
                False,
                f"leverage={effective_leverage:.2f}x>{self._max_leverage}x",
            )
        return RiskCheckResult(True)

    def check_flat(
        self,
        portfolio: "PortfolioFacade",
        instrument_id,
    ) -> RiskCheckResult:
        try:
            if not portfolio.is_flat(instrument_id):
                return RiskCheckResult(False, "already_positioned")
        except Exception:
            return RiskCheckResult(True)
        return RiskCheckResult(True)

    # ── Equity state persistence ───────────────────────────────────────────────

    def _equity_state_file(self) -> Path | None:
        if self._equity_state_path is None:
            return None
        return self._equity_state_path

    def _persist_equity_state(self, day: date) -> None:
        """Write today's start equity to disk."""
        path = self._equity_state_file()
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            state = {
                "day": day.isoformat(),
                "day_start_equity": self._day_start_equity,
            }
            path.write_text(json.dumps(state))
        except OSError:
            pass  # non-fatal — degraded to in-memory only

    def _load_equity_state(self) -> None:
        """
        Reload persisted equity state on startup.

        If the saved day matches today UTC, restores day_start_equity so a
        restart mid-session does not reset the drawdown reference.
        """
        path = self._equity_state_file()
        if path is None or not path.exists():
            return
        try:
            state = json.loads(path.read_text())
            saved_day = date.fromisoformat(state["day"])
            today = datetime.now(UTC).date()
            if saved_day == today:
                self._day = saved_day
                self._day_start_equity = float(state["day_start_equity"])
        except (OSError, KeyError, ValueError, json.JSONDecodeError):
            pass  # corrupt or missing — start fresh

    def _maybe_roll_day(self, now: datetime) -> None:
        """Roll over day tracking at UTC midnight."""
        today = now.astimezone(UTC).date()
        if self._day != today:
            self._day = today
            self._day_start_equity = None   # will be set on next equity observation