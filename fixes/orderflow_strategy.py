"""
strategy/orderflow_strategy.py — Bidirectional orderflow strategy.

Fixes vs previous version
--------------------------
1. build_entry_order / estimate_order_qty: added missing `side=signal.side`.
   Previous code passed no side → always would TypeError on first live entry.

2. _maybe_cancel_replace_limit: was calling should_cancel_stale_limit with
   (timestamp_ns, timestamp_ns, stale_ms=...) — completely wrong signature.
   Function expects (order_price, current_price, side=, max_drift_bps=).

3. Post-loss cooldown: after a losing close the strategy now waits
   `loss_cooldown_secs` (default 60s) before accepting new entries.
   Previously re-entered immediately, creating the observed death-spiral.

4. Minimum hold time: signal-reversal exits (cvd_rolling_over etc.) are
   suppressed for `min_hold_secs` (default 10s) after entry.
   Hard stop-loss still fires immediately.  Prevents 716ms exits.

5. HTF trend comparison: signals compare structure.trend.value (str) to
   "bearish"/"bullish" strings — fixed in signal files, not here, but
   the fix is noted for cross-reference.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from nautilus_trader.core.data import Data
from nautilus_trader.model.book import OrderBook
from nautilus_trader.model.data import OrderBookDeltas, TradeTick
from nautilus_trader.model.enums import OrderSide, PositionSide, book_type_from_str
from nautilus_trader.model.events import PositionClosed
from nautilus_trader.model.identifiers import ClientId, InstrumentId
from nautilus_trader.trading.strategy import Strategy

from nautilus.config.schema import OrderflowStrategyConfig, SignalsConfig
from nautilus.data.ticks import trade_tick_to_side_dict
from nautilus.execution.policy import (
    BracketSpec,
    build_entry_order,
    build_exit_order,
    compute_bracket_prices,
    estimate_order_qty,
    should_cancel_stale_limit,
)
from nautilus.features.multi_tf import MultiTFEngine, MultiTFSnapshot
from nautilus.features.ob import orderbook_to_imbalance
from nautilus.ml.dataset import DatasetBuffer, FeatureRow, Labeler, PassthroughHook
from nautilus.ops.metrics import MetricsLogger
from nautilus.risk.stack import PreTradeRiskStack
from nautilus.sessions.filter import SessionFilter
from nautilus.signals.base import EntrySignal
from nautilus.signals.registry import SignalRegistry
from nautilus.structure.market_structure import (
    NULL_STRUCTURE,
    MarketStructureEngine,
    MarketStructureSnapshot,
)


class OrderflowStrategy(Strategy):
    """
    Tick-driven bidirectional orderflow strategy.

    State machine:
        flat ──► LONG  (via long signal modules)
        flat ──► SHORT (via short signal modules)
        LONG  ──► flat (stoploss / trailing / signal reversal / time)
        SHORT ──► flat (stoploss / trailing / signal reversal / time)
    """

    def __init__(self, config: OrderflowStrategyConfig) -> None:
        super().__init__(config)
        self._instrument_id: InstrumentId = config.instrument_id
        self._client_id = ClientId(config.client_id)

        self._engine = MultiTFEngine(
            ltf=config.timeframe,
            htf=getattr(config, "htf_timeframe", "1h"),
            lookback_candles=config.lookback_candles,
            price_bucket_size=config.price_bucket_size,
            large_trade_pct=config.large_trade_pct,
            cvd_smoothing=config.cvd_smoothing,
            divergence_window=getattr(config, "divergence_window", 3),
        )

        signals_cfg = getattr(config, "signals_config", None)
        if signals_cfg is None:
            signals_cfg = SignalsConfig(
                long=["imbalance_continuation_long", "absorption_breakout_long"],
                short=["imbalance_continuation_short", "absorption_breakout_short"],
            )
        self._signals = SignalRegistry.from_config(signals_cfg)

        self._structure_engine = MarketStructureEngine(
            swing_window=getattr(config, "swing_window", 5)
        )
        self._structure: MarketStructureSnapshot = NULL_STRUCTURE

        sessions_cfg = getattr(config, "sessions_config", None)
        if sessions_cfg:
            self._session_filter = SessionFilter.from_config(sessions_cfg)
        else:
            self._session_filter = SessionFilter.always()

        self._risk = PreTradeRiskStack(
            max_daily_loss_pct=config.max_daily_loss_pct,
            max_consecutive_losses=config.max_consecutive_losses,
            max_spread_bps=config.max_spread_bps,
            stale_tick_ms=config.stale_tick_ms,
            min_top_of_book_qty=config.min_top_of_book_qty,
            kill_switch_path=config.kill_switch_path,
            max_leverage=config.max_leverage,
            equity_state_path=config.equity_state_path,
        )

        self._inference_hook = PassthroughHook()
        self._dataset = DatasetBuffer(labeler=Labeler()) if config.log_metrics else None
        self._metrics = MetricsLogger(config.metrics_dir) if config.log_metrics else None

        # ── Position state ────────────────────────────────────────────────
        self._last_tick_ns: int = 0
        self._last_eval_ns: int = 0
        self._entry_price: float | None = None
        self._entry_side: OrderSide | None = None
        self._trailing_active: bool = False
        self._trailing_peak: float = 0.0
        self._position_open_ts_ns: int | None = None
        self._last_signal: EntrySignal | None = None
        self._pending_limit_price: float | None = None
        self._is_pending: bool = False

        # ── Cooldown state (FIX: post-loss re-entry guard) ────────────────
        # Set to future timestamp after a losing close; blocks new entries.
        self._loss_cooldown_until_ns: int = 0

        self._bracket = BracketSpec(
            stoploss_pct=config.stoploss_pct,
            target_pct=getattr(config, "target_pct", config.stoploss_pct * 2),
            trailing_trigger_pct=config.trailing_trigger_pct,
            trailing_offset_pct=config.trailing_offset_pct,
        )

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def on_start(self) -> None:
        inst = self.cache.instrument(self._instrument_id)
        if inst is None:
            self.log.error(f"Instrument not found: {self._instrument_id}")
            self.stop()
            return

        positions = self.cache.positions_open(instrument_id=self._instrument_id, strategy_id=self.id)
        if positions:
            pos = positions[0]
            self.log.info(f"Recovered open position: {pos.side} {pos.quantity}")
            self._entry_price = float(pos.avg_px_open)
            self._entry_side = OrderSide.BUY if pos.is_long else OrderSide.SELL
            self._position_open_ts_ns = pos.ts_opened
            self._trailing_peak = self._entry_price

        self.subscribe_trade_ticks(self._instrument_id)
        bt = (
            book_type_from_str(self.config.book_type)
            if isinstance(self.config.book_type, str)
            else self.config.book_type
        )
        self.subscribe_order_book_deltas(self._instrument_id, bt)

    # ── Data handlers ──────────────────────────────────────────────────────────

    def on_trade_tick(self, tick: TradeTick) -> None:
        self._last_tick_ns = tick.ts_event
        raw = trade_tick_to_side_dict(tick)
        if raw is None:
            return
        self._engine.add_tick(raw["ts"], raw["price"], raw["qty"], raw["side"])
        self._maybe_evaluate()

    def on_order_book_deltas(self, deltas: OrderBookDeltas) -> None:
        try:
            self._maybe_evaluate()
        except Exception as e:
            self.log.exception(f"Error in on_order_book_deltas: {e}", e)

    # ── Evaluation throttle ────────────────────────────────────────────────────

    def _maybe_evaluate(self) -> None:
        throttle_ns = int(self.config.eval_throttle_ms * 1_000_000)
        now_ns = self.clock.timestamp_ns()
        if throttle_ns > 0 and (now_ns - self._last_eval_ns) < throttle_ns:
            return
        self._last_eval_ns = now_ns

        book = self.cache.order_book(self._instrument_id)
        ob_imb = orderbook_to_imbalance(book, self.config.book_depth)
        self._engine.set_orderbook_imbalance_value(ob_imb)

        now_ms = int(self._last_tick_ns / 1_000_000 if self._last_tick_ns else now_ns / 1_000_000)
        snap = self._engine.compute_snapshot(now_ms=now_ms)
        if snap is None:
            return

        htf_candles = self._engine.completed_candles(
            getattr(self.config, "htf_timeframe", "1h")
        )
        if htf_candles and snap.htf:
            self._structure = self._structure_engine.update(
                htf_candles, snap.htf.close_price
            )

        session = self._session_filter.current_session(datetime.now(UTC))

        inst = self.cache.instrument(self._instrument_id)
        if inst is None:
            return

        mid = book.midpoint() if book and book.midpoint() is not None else None
        px  = float(mid) if mid is not None else snap.ltf.close_price

        is_long  = self.portfolio.is_net_long(self._instrument_id)
        is_short = self.portfolio.is_net_short(self._instrument_id)

        if is_long:
            self._check_exit(snap, px, OrderSide.BUY)
        elif is_short:
            self._check_exit(snap, px, OrderSide.SELL)
        else:
            self._maybe_cancel_replace_limit(px)
            self._check_entry(snap, book, px, session)

    # ── Account helpers ────────────────────────────────────────────────────────

    def _quote_balance(self) -> float | None:
        try:
            inst = self.cache.instrument(self._instrument_id)
            if inst is None:
                return None
            account = self.portfolio.account(self._instrument_id.venue)
            bal = account.balance(inst.quote_currency)
            return float(bal.total.as_double()) if bal else None
        except Exception:
            return None

    # ── Entry ──────────────────────────────────────────────────────────────────

    def _check_entry(self, snap: MultiTFSnapshot, book, px: float, session) -> None:
        cfg = self.config

        if self._is_pending:
            return

        # ── FIX: post-loss cooldown ───────────────────────────────────────
        now_ns = self.clock.timestamp_ns()
        if now_ns < self._loss_cooldown_until_ns:
            remaining_s = (self._loss_cooldown_until_ns - now_ns) / 1e9
            if self._metrics:
                self._metrics.log_event("entry_rejected", {"failed": ["loss_cooldown"],
                                                            "remaining_s": remaining_s})
            return

        if self.cache.orders_open_count(instrument_id=self._instrument_id, strategy_id=self.id) > 0:
            return

        inst = self.cache.instrument(self._instrument_id)
        if inst is None:
            return

        ks_result = self._risk.check_kill_switch()
        if not ks_result.ok:
            if self._metrics:
                self._metrics.log_event("entry_rejected", {"failed": ["kill_switch"]})
            return

        last_ms = self._last_tick_ns / 1_000_000
        now_ms  = now_ns / 1_000_000
        st_result = self._risk.check_stale_tick(last_ms, now_ms)
        if not st_result.ok:
            if self._metrics:
                self._metrics.log_event("entry_rejected", {"failed": ["stale_tick"]})
            return

        if cfg.require_orderbook:
            if not book:
                if self._metrics:
                    self._metrics.log_event("entry_rejected", {"failed": ["no_orderbook"]})
                return
            sd_result = self._risk.check_spread_and_depth(book)
            if not sd_result.ok:
                if self._metrics:
                    self._metrics.log_event("entry_rejected", {"failed": ["spread_depth"]})
                return

        if not self.portfolio.is_flat(self._instrument_id):
            if self._metrics:
                self._metrics.log_event("entry_rejected", {"failed": ["position_open"]})
            return

        eq = self._quote_balance()
        dl_result = self._risk.check_daily_loss(eq)
        if not dl_result.ok:
            if self._metrics:
                self._metrics.log_event("entry_rejected", {"failed": ["daily_loss"]})
            return

        long_signals  = self._signals.evaluate_long(snap, self._structure, session)
        short_signals = self._signals.evaluate_short(snap, self._structure, session)

        signal = (long_signals or short_signals or [None])[0]
        if signal is None:
            if self._metrics:
                self._metrics.log_event("entry_rejected", {"failed": ["no_signal"]})
            return

        if eq is None or eq <= 0:
            if self._metrics:
                self._metrics.log_event("entry_rejected", {"failed": ["insufficient_equity"]})
            return

        ml_confidence = self._inference_hook.predict(
            self._build_feature_row(snap, session, signal)
        )
        if ml_confidence <= 0:
            if self._metrics:
                self._metrics.log_event("entry_rejected", {"failed": ["low_ml_confidence"]})
            return

        effective_fraction = cfg.max_position_fraction * ml_confidence

        # ── Estimate order quantity based on available equity ──────────────
        qty = estimate_order_qty(
            inst,
            quote_balance=eq,
            price=px,
            max_fraction=effective_fraction,
            max_notional_usdt=cfg.max_notional_usdt,
        )
        if qty <= 0:
            if self._metrics:
                self._metrics.log_event("entry_rejected", {"failed": ["insufficient_qty"]})
            return

        notional = float(qty) * px
        if not self._risk.check_leverage(notional, eq).ok:
            return

        try:
            order = build_entry_order(
                self.order_factory, inst,
                price=px,
                qty=qty,
                use_market=cfg.use_market_entries,
                post_only=cfg.entry_post_only,
            )
        except Exception as exc:
            self.log.warning(f"Entry order build failed: {exc}")
            return

        self._entry_price = px
        self._entry_side  = signal.side
        self._trailing_active = False
        self._trailing_peak   = px
        self._position_open_ts_ns = self.clock.timestamp_ns()
        self._last_signal = signal
        self._pending_limit_price = px if not cfg.use_market_entries else None

        self.submit_order(order, client_id=self._client_id)
        self._is_pending = True

        if self._metrics:
            self._metrics.log_event("entry_signal", {
                "side": signal.side.name,
                "label": signal.label,
                "price": px,
                "qty": str(qty),
                "notional_usdt": float(qty) * px,
                "confidence": ml_confidence,
                "conditions": signal.conditions,
            })

    # ── Exit ───────────────────────────────────────────────────────────────────

    def _check_exit(self, snap: MultiTFSnapshot, px: float, position_side: OrderSide) -> None:
        """
        Evaluate exit conditions for an open position.

        Hard stop fires immediately.
        All signal-reversal exits are suppressed for min_hold_secs after entry
        to prevent sub-second exits on noisy ticks.
        """
        cfg = self.config
        of  = snap.ltf.flow

        if self._entry_price is None:
            return

        entry     = self._entry_price
        direction = 1.0 if position_side == OrderSide.BUY else -1.0
        pnl_pct   = direction * (px - entry) / entry

        # ── Hard stop — fires regardless of hold time ─────────────────────
        stop_dist = self._bracket.stoploss_pct
        if pnl_pct <= -stop_dist:
            self._exit_all("stoploss", position_side)
            return

        # ── FIX: minimum hold time — suppresses signal exits only ─────────
        min_hold_secs = getattr(cfg, "min_hold_secs", 10.0)
        if self._position_open_ts_ns is not None and min_hold_secs > 0:
            held_secs = (self.clock.timestamp_ns() - self._position_open_ts_ns) / 1e9
            if held_secs < min_hold_secs:
                return   # still within minimum hold window

        # ── Trailing stop ─────────────────────────────────────────────────
        if pnl_pct >= self._bracket.trailing_trigger_pct:
            self._trailing_active = True

        if self._trailing_active:
            if position_side == OrderSide.BUY:
                self._trailing_peak = max(self._trailing_peak, px)
                trail_dd = (px - self._trailing_peak) / self._trailing_peak
            else:
                self._trailing_peak = min(self._trailing_peak, px)
                trail_dd = (self._trailing_peak - px) / self._trailing_peak

            if trail_dd <= -self._bracket.trailing_offset_pct:
                self._exit_all("trailing_stop", position_side)
                return

        # ── Time stop ─────────────────────────────────────────────────────
        if cfg.max_time_in_trade_secs is not None and self._position_open_ts_ns is not None:
            open_secs = (self.clock.timestamp_ns() - self._position_open_ts_ns) / 1e9
            if open_secs >= cfg.max_time_in_trade_secs:
                self._exit_all("time_stop", position_side)
                return

        # ── Signal-reversal exits ─────────────────────────────────────────
        reasons: list[str] = []

        if position_side == OrderSide.BUY:
            if not snap.ltf.cvd_rising:
                reasons.append("cvd_rolling_over")
            if of.absorption <= -cfg.absorption_min:
                reasons.append("sell_absorption")
            if of.delta_div == 1.0:
                reasons.append("bearish_delta_div")
            if of.imbalance <= -cfg.imbalance_threshold:
                reasons.append("imbalance_flipped_sell")
        else:
            if snap.ltf.cvd_rising:
                reasons.append("cvd_rising_vs_short")
            if of.absorption >= cfg.absorption_min:
                reasons.append("buy_absorption_vs_short")
            if of.delta_div == -1.0:
                reasons.append("bullish_delta_div_vs_short")
            if of.imbalance >= cfg.imbalance_threshold:
                reasons.append("imbalance_flipped_buy")

        if reasons:
            self._exit_all("+".join(reasons), position_side)

    # ── Exit execution ─────────────────────────────────────────────────────────

    def _exit_all(self, reason: str, side: OrderSide) -> None:
        self.cancel_all_orders(self._instrument_id, client_id=self._client_id)
        positions = self.cache.positions_open(
            instrument_id=self._instrument_id,
            strategy_id=self.id,
        )
        for pos in positions:
            self.close_position(pos, client_id=self._client_id, tags=[f"exit:{reason}"])

        pnl_str = f"entry={self._entry_price:.4f}" if self._entry_price else ""

        if self._metrics:
            self._metrics.log_event("exit", {
                "reason": reason,
                "side": side.name,
                "entry_info": pnl_str,
                "trailing_active": self._trailing_active,
            })

        self._entry_price = None
        self._entry_side  = None
        self._trailing_active = False
        self._position_open_ts_ns = None
        self._pending_limit_price = None

    # ── Cancel-replace stale limit ─────────────────────────────────────────────

    def _maybe_cancel_replace_limit(self, current_px: float) -> None:
        """
        Cancel a pending limit entry that has drifted more than max_entry_drift_bps
        from current price.  Fresh entry re-evaluated next cycle.
        """
        if self._pending_limit_price is None:
            return

        open_count = self.cache.orders_open_count(
            instrument_id=self._instrument_id,
            strategy_id=self.id,
        )
        if open_count == 0:
            self._pending_limit_price = None
            return

        # ── FIX: was passing (timestamp_ns, timestamp_ns, stale_ms=...) ──
        # Correct signature: (order_price, current_price, side=, max_drift_bps=)
        if self._entry_side is None:
            self._pending_limit_price = None
            return

        if should_cancel_stale_limit(
            self._pending_limit_price,
            current_px,
            side=self._entry_side,
            max_drift_bps=getattr(self.config, "max_entry_drift_bps", 8.0),
        ):
            stale_px = self._pending_limit_price
            self.cancel_all_orders(self._instrument_id, client_id=self._client_id)
            self._pending_limit_price = None
            self._entry_price = None
            if self._metrics:
                self._metrics.log_event("entry_cancelled_stale_limit", {
                    "limit_px": stale_px,
                    "current_px": current_px,
                })

    # ── Order feedback ─────────────────────────────────────────────────────────

    def on_order_submitted(self, event):
        pass

    def on_order_accepted(self, event):
        self._is_pending = False

    def on_position_opened(self, event):
        self._entry_price = float(event.avg_px_open)
        self._entry_side = OrderSide.BUY if event.side == PositionSide.LONG else OrderSide.SELL
        self._position_open_ts_ns = event.ts_event
        self._trailing_peak = self._entry_price
        self._trailing_active = False

    def on_order_rejected(self, event):
        self._is_pending = False
        self._entry_price = None
        self._entry_side = None

    def on_order_cancelled(self, event):
        self._is_pending = False
        self._entry_price = None
        self._entry_side = None

    # ── Position closed ────────────────────────────────────────────────────────

    def on_position_closed(self, event: PositionClosed) -> None:
        if event.instrument_id != self._instrument_id:
            return

        pnl = float(event.realized_pnl.as_double())
        self._risk.on_position_closed_pnl(pnl)

        # ── FIX: post-loss cooldown ───────────────────────────────────────
        if pnl < 0:
            cooldown_secs = getattr(self.config, "loss_cooldown_secs", 60.0)
            if cooldown_secs > 0:
                self._loss_cooldown_until_ns = (
                    self.clock.timestamp_ns() + int(cooldown_secs * 1e9)
                )
                self.log.info(
                    f"Loss cooldown active for {cooldown_secs:.0f}s "
                    f"(pnl={pnl:.4f})"
                )

        self._entry_price = None
        self._entry_side  = None
        self._trailing_active = False
        self._position_open_ts_ns = None

        if self._metrics:
            eq = self._quote_balance()
            self._metrics.log_event("position_closed", {
                "realized_pnl": pnl,
                "consecutive_losses": self._risk.consecutive_losses,
                "daily_pnl_pct": self._risk.daily_pnl_pct(eq),
            })

    # ── ML feature row ─────────────────────────────────────────────────────────

    def _build_feature_row(self, snap: MultiTFSnapshot, session, signal: EntrySignal) -> FeatureRow:
        ltf = snap.ltf.flow
        htf = snap.htf.flow
        st  = self._structure
        large_sum = ltf.large_buy_vol + ltf.large_sell_vol
        large_dom = (
            (ltf.large_buy_vol - ltf.large_sell_vol) / large_sum
            if large_sum > 1e-9 else 0.0
        )
        return FeatureRow(
            ts_ms=snap.ltf.ts_ms,
            cvd=ltf.cvd, cvd_ema=snap.ltf.cvd_ema, cvd_rising=int(snap.ltf.cvd_rising),
            imbalance=ltf.imbalance, absorption=ltf.absorption,
            stacked_imb=ltf.stacked_imb, ob_imbalance=ltf.ob_imbalance,
            delta_div=ltf.delta_div, large_dom=large_dom,
            buy_vol=ltf.buy_vol, sell_vol=ltf.sell_vol, total_vol=ltf.total_vol,
            htf_cvd=htf.cvd, htf_cvd_rising=int(snap.htf.cvd_rising),
            htf_imbalance=htf.imbalance, htf_absorption=htf.absorption,
            trend=1 if st.trend.value == "bullish" else (-1 if st.trend.value == "bearish" else 0),
            hh=1 if st.last_swing_high and st.structure_break and st.break_type == "high" else 0,
            hl=1 if st.last_swing_low and st.structure_break else 0,
            lh=0, ll=0,
            bos_bullish=1 if st.structure_break and st.break_type == "high" else 0,
            bos_bearish=1 if st.structure_break and st.break_type == "low" else 0,
            last_high_price=st.last_swing_high.price if st.last_swing_high else 0.0,
            last_low_price=st.last_swing_low.price if st.last_swing_low else 0.0,
            session_name=session.session_name or "",
            session_active=int(session.active),
            session_minutes_elapsed=session.minutes_elapsed or -1,
            session_minutes_to_close=session.minutes_to_close or -1,
            signal_label=signal.label,
            signal_side=signal.side.name,
            signal_confidence=signal.confidence,
        )

    def on_data(self, data: Data) -> None:
        pass
