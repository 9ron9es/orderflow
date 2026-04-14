"""
strategy/orderflow_strategy.py — PRODUCTION strategy.

Complete implementation:
  - Bidirectional long + short (all 7 signal modules)
  - Online ML gate: blocks low-confidence entries, learns from outcomes
  - Session filter: London + NY only by default
  - Market structure: HTF swing + trend alignment
  - Multi-TF: 5m entry, 1h structure
  - Bracket exits: stoploss + trailing (both sides correct)
  - Cancel-replace stale limit entries
  - Crash-safe: ML state + equity state persisted to disk
  - JSON-L metrics for every event
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal

from nautilus_trader.core.data import Data
from nautilus_trader.model.book import OrderBook
from nautilus_trader.model.data import OrderBookDeltas, TradeTick
from nautilus_trader.model.enums import OrderSide, book_type_from_str
from nautilus_trader.model.events import PositionClosed
from nautilus_trader.model.identifiers import ClientId, InstrumentId
from nautilus_trader.trading.strategy import Strategy

from orderflow.nautilus.config.schema import OrderflowStrategyConfig
from orderflow.nautilus.data.ticks import trade_tick_to_side_dict
from orderflow.nautilus.execution.policy import (
    BracketSpec,
    build_entry_order,
    compute_bracket_prices,
    estimate_order_qty,
    should_cancel_stale_limit,
)
from orderflow.nautilus.features.multi_tf import MultiTFEngine
from orderflow.nautilus.features.ob import orderbook_to_imbalance
from orderflow.nautilus.ml.online_gate import OnlineMLGate, TradeRecord, build_feature_vector
from orderflow.nautilus.ops.metrics import MetricsLogger
from orderflow.nautilus.risk.stack import PreTradeRiskStack
from orderflow.nautilus.sessions.filter import SessionFilter
from orderflow.nautilus.signals.base import EntrySignal
from orderflow.nautilus.signals.registry import SignalRegistry, SignalsConfig
from orderflow.nautilus.structure.market_structure import (
    NULL_STRUCTURE,
    MarketStructureEngine,
    MarketStructureSnapshot,
)

log = logging.getLogger(__name__)


class OrderflowStrategy(Strategy):
    """
    Production tick-driven orderflow strategy.
    Flat → Long or Short → Flat state machine, ML-gated.
    """

    def __init__(self, config: OrderflowStrategyConfig) -> None:
        super().__init__(config)
        cfg = config
        self._instrument_id: InstrumentId = cfg.instrument_id
        self._client_id = ClientId(cfg.client_id)

        # ── Multi-TF feature engine ───────────────────────────────────────
        self._engine = MultiTFEngine(
            ltf=cfg.timeframe,
            htf=getattr(cfg, "htf_timeframe", "1h"),
            lookback_candles=cfg.lookback_candles,
            price_bucket_size=cfg.price_bucket_size,
            large_trade_pct=cfg.large_trade_pct,
            cvd_smoothing=cfg.cvd_smoothing,
            divergence_window=getattr(cfg, "divergence_window", 3),
        )

        # ── Signals ───────────────────────────────────────────────────────
        sc = getattr(cfg, "signals_config", None) or SignalsConfig()
        self._signals = SignalRegistry.from_config(sc)

        # ── Market structure ──────────────────────────────────────────────
        self._structure_engine = MarketStructureEngine(
            swing_window=getattr(cfg, "swing_window", 5)
        )
        self._structure: MarketStructureSnapshot = NULL_STRUCTURE

        # ── Session filter ────────────────────────────────────────────────
        sessions_raw = getattr(cfg, "sessions_config", None)
        self._session_filter = (
            SessionFilter.from_config(sessions_raw)
            if sessions_raw
            else SessionFilter.default()
        )

        # ── Risk stack ────────────────────────────────────────────────────
        self._risk = PreTradeRiskStack(
            max_daily_loss_pct=cfg.max_daily_loss_pct,
            max_consecutive_losses=cfg.max_consecutive_losses,
            max_spread_bps=cfg.max_spread_bps,
            stale_tick_ms=cfg.stale_tick_ms,
            min_top_of_book_qty=cfg.min_top_of_book_qty,
            kill_switch_path=cfg.kill_switch_path,
            max_leverage=cfg.max_leverage,
            equity_state_path=cfg.equity_state_path,
        )

        # ── Online ML gate ────────────────────────────────────────────────
        ml_state_path = getattr(cfg, "ml_state_path", "orderflow/.ml_state.pkl")
        self._ml = OnlineMLGate.load(ml_state_path)
        self._ml.cfg.state_path = ml_state_path

        # ── Bracket spec ──────────────────────────────────────────────────
        self._bracket = BracketSpec(
            stoploss_pct=cfg.stoploss_pct,
            target_pct=getattr(cfg, "target_pct", cfg.stoploss_pct * 2.0),
            trailing_trigger_pct=cfg.trailing_trigger_pct,
            trailing_offset_pct=cfg.trailing_offset_pct,
        )

        # ── Telemetry ─────────────────────────────────────────────────────
        self._metrics = MetricsLogger(cfg.metrics_dir) if cfg.log_metrics else None

        # ── Position state ────────────────────────────────────────────────
        self._last_tick_ns: int = 0
        self._last_eval_ns: int = 0

        self._entry_price: float | None = None
        self._entry_side: OrderSide | None = None
        self._entry_ts_ns: int | None = None          # For ML resolve
        self._entry_ts_ms: int | None = None

        self._trailing_active: bool = False
        self._trailing_peak: float = 0.0

        self._pending_limit_price: float | None = None
        self._last_features: list[float] | None = None   # Stored for ML learn()

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def on_start(self) -> None:
        inst = self.cache.instrument(self._instrument_id)
        if inst is None:
            self.log.error(f"Instrument not found: {self._instrument_id}")
            self.stop()
            return
        self.subscribe_trade_ticks(self._instrument_id)
        bt = (
            book_type_from_str(self.config.book_type)
            if isinstance(self.config.book_type, str)
            else self.config.book_type
        )
        self.subscribe_order_book_deltas(self._instrument_id, bt)
        log.info("OrderflowStrategy started. ML status: %s", self._ml.status())

    def on_stop(self) -> None:
        self._ml.save()
        log.info("OrderflowStrategy stopped. ML status: %s", self._ml.status())

    # ── Data handlers ──────────────────────────────────────────────────────────

    def on_trade_tick(self, tick: TradeTick) -> None:
        self._last_tick_ns = tick.ts_event
        raw = trade_tick_to_side_dict(tick)
        if raw is None:
            return
        self._engine.add_tick(raw["ts"], raw["price"], raw["qty"], raw["side"])
        self._maybe_evaluate()

    def on_order_book_deltas(self, deltas: OrderBookDeltas) -> None:
        self._maybe_evaluate()

    # ── Throttled evaluation loop ──────────────────────────────────────────────

    def _maybe_evaluate(self) -> None:
        throttle_ns = int(self.config.eval_throttle_ms * 1_000_000)
        now_ns = self.clock.timestamp_ns()
        if throttle_ns > 0 and (now_ns - self._last_eval_ns) < throttle_ns:
            return
        self._last_eval_ns = now_ns

        # ── OB imbalance ──────────────────────────────────────────────────
        book = self.cache.order_book(self._instrument_id)
        ob_imb = orderbook_to_imbalance(book, self.config.book_depth)
        self._engine.set_orderbook_imbalance_value(ob_imb)

        # ── Feature snapshot ──────────────────────────────────────────────
        now_ms = int(self._last_tick_ns / 1_000_000 if self._last_tick_ns else now_ns / 1_000_000)
        snap = self._engine.compute_snapshot(now_ms=now_ms)
        if snap is None:
            return

        # ── Update market structure (HTF bars) ────────────────────────────
        htf_tf = getattr(self.config, "htf_timeframe", "1h")
        htf_candles = self._engine.completed_candles(htf_tf)
        if htf_candles:
            self._structure = self._structure_engine.update(
                htf_candles, snap.htf.close_price
            )

        # ── Session ───────────────────────────────────────────────────────
        session = self._session_filter.is_active(datetime.now(UTC))

        # ── Instrument ───────────────────────────────────────────────────
        inst = self.cache.instrument(self._instrument_id)
        if inst is None:
            return

        # ── Mid price ─────────────────────────────────────────────────────
        mid = book.midpoint() if book and book.midpoint() is not None else None
        px  = float(mid) if mid is not None else snap.ltf.close_price

        # ── State machine ─────────────────────────────────────────────────
        is_long  = self.portfolio.is_net_long(self._instrument_id)
        is_short = self.portfolio.is_net_short(self._instrument_id)

        if is_long:
            self._check_exit(snap, px, OrderSide.BUY)
        elif is_short:
            self._check_exit(snap, px, OrderSide.SELL)
        else:
            self._maybe_cancel_replace(px)
            self._check_entry(snap, book, px, session)

    # ── Entry logic ────────────────────────────────────────────────────────────

    def _check_entry(self, snap, book, px: float, session) -> None:
        cfg = self.config

        # Skip if limit order is already open
        if self.cache.orders_open_count(instrument_id=self._instrument_id, strategy_id=self.id) > 0:
            return

        inst = self.cache.instrument(self._instrument_id)
        if inst is None:
            return

        # ── Risk gates (cheapest first) ───────────────────────────────────
        if not self._risk.check_kill_switch().ok:
            return
        last_ms = self._last_tick_ns / 1_000_000
        now_ms  = self.clock.timestamp_ns() / 1_000_000
        if not self._risk.check_stale_tick(last_ms, now_ms).ok:
            return
        if cfg.require_orderbook:
            if not book or not self._risk.check_spread_and_depth(book).ok:
                return
        if not self.portfolio.is_flat(self._instrument_id):
            return
        eq = self._quote_balance()
        if not self._risk.check_daily_loss(eq).ok:
            return

        # ── Signal evaluation ─────────────────────────────────────────────
        long_sigs  = self._signals.evaluate_long(snap, self._structure, session)
        short_sigs = self._signals.evaluate_short(snap, self._structure, session)

        signal: EntrySignal | None = None
        if long_sigs:
            signal = long_sigs[0]
        elif short_sigs:
            signal = short_sigs[0]

        if signal is None:
            return

        # ── Build ML feature vector ───────────────────────────────────────
        features = build_feature_vector(snap, self._structure, session, signal.side.name)

        # ── ML gate ───────────────────────────────────────────────────────
        confidence = self._ml.predict(features)
        if not self._ml.should_pass(confidence):
            self._log("entry_ml_blocked", {
                "label": signal.label,
                "confidence": round(confidence, 4),
                "threshold": self._ml.cfg.confidence_threshold,
            })
            return

        # ── Size ──────────────────────────────────────────────────────────
        if eq is None or eq <= 0:
            return

        # Scale position by ML confidence when gate is active
        size_scale = confidence if self._ml.is_active else 1.0
        effective_fraction = cfg.max_position_fraction * size_scale

        qty = estimate_order_qty(
            inst,
            side=signal.side,
            quote_balance=eq,
            price=px,
            max_fraction=effective_fraction,
            max_notional_usdt=cfg.max_notional_usdt,
        )
        if qty <= 0:
            return

        # ── Leverage gate ─────────────────────────────────────────────────
        notional = float(qty) * px
        if not self._risk.check_leverage(notional, eq).ok:
            return

        # ── Submit order ──────────────────────────────────────────────────
        try:
            order = build_entry_order(
                self.order_factory, inst,
                side=signal.side,
                price=px,
                qty=qty,
                use_market=cfg.use_market_entries,
                post_only=cfg.entry_post_only,
            )
        except Exception as exc:
            log.warning("Entry order build failed: %s", exc)
            return

        # ── Store position state ──────────────────────────────────────────
        self._entry_price  = px
        self._entry_side   = signal.side
        self._entry_ts_ns  = self.clock.timestamp_ns()
        self._entry_ts_ms  = int(self._entry_ts_ns / 1_000_000)
        self._trailing_active = False
        self._trailing_peak   = px
        self._pending_limit_price = px if not cfg.use_market_entries else None
        self._last_features = features

        # Register with ML as pending trade
        record = TradeRecord(
            ts_ms=self._entry_ts_ms,
            features=features,
            signal_label=signal.label,
            signal_side=signal.side.name,
            confidence_at_entry=confidence,
        )
        self._ml._pending.append(record)

        self.submit_order(order, client_id=self._client_id)

        self._log("entry_submitted", {
            "side": signal.side.name,
            "label": signal.label,
            "price": px,
            "qty": str(qty),
            "ml_confidence": round(confidence, 4),
            "ml_active": self._ml.is_active,
            "ml_n_trades": self._ml.n_trades,
            "conditions": {k: v for k, v in signal.conditions.items()},
        })

    # ── Exit logic ─────────────────────────────────────────────────────────────

    def _check_exit(self, snap, px: float, position_side: OrderSide) -> None:
        """Direction-aware exit logic for long and short positions."""
        cfg = self.config

        if self._entry_price is None:
            return  # Guard: entry price not set (partial fill not yet tracked)

        entry = self._entry_price
        # Direction: +1 for long (profit when price rises), -1 for short
        direction = 1.0 if position_side == OrderSide.BUY else -1.0
        pnl_pct   = direction * (px - entry) / entry

        # ── Hard stop ─────────────────────────────────────────────────────
        if pnl_pct <= -self._bracket.stoploss_pct:
            self._exit_all("stoploss", position_side)
            return

        # ── Trailing stop ─────────────────────────────────────────────────
        if pnl_pct >= self._bracket.trailing_trigger_pct:
            self._trailing_active = True

        if self._trailing_active:
            if position_side == OrderSide.BUY:
                self._trailing_peak = max(self._trailing_peak, px)
                drawdown = (px - self._trailing_peak) / self._trailing_peak
            else:
                self._trailing_peak = min(self._trailing_peak, px)
                drawdown = (self._trailing_peak - px) / self._trailing_peak

            if drawdown <= -self._bracket.trailing_offset_pct:
                self._exit_all("trailing_stop", position_side)
                return

        # ── Time stop ─────────────────────────────────────────────────────
        max_t = cfg.max_time_in_trade_secs
        if max_t is not None and self._entry_ts_ns is not None:
            elapsed = (self.clock.timestamp_ns() - self._entry_ts_ns) / 1e9
            if elapsed >= max_t:
                self._exit_all("time_stop", position_side)
                return

        # ── Signal reversal exits ─────────────────────────────────────────
        of = snap.ltf.flow
        reasons: list[str] = []

        if position_side == OrderSide.BUY:
            if not snap.ltf.cvd_rising:
                reasons.append("cvd_rollover")
            if of.absorption <= -cfg.absorption_min:
                reasons.append("sell_absorption_active")
            if of.delta_div == 1.0:
                reasons.append("bearish_delta_div")
            if of.imbalance <= -cfg.imbalance_threshold:
                reasons.append("imbalance_flipped_sell")
        else:  # SHORT
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

        self._log("exit", {"reason": reason, "side": side.name, "entry_price": self._entry_price})

        self._entry_price  = None
        self._entry_side   = None
        self._trailing_active = False
        self._pending_limit_price = None

    # ── Position closed → ML learn ─────────────────────────────────────────────

    def on_position_closed(self, event: PositionClosed) -> None:
        if event.instrument_id != self._instrument_id:
            return
        pnl = float(event.realized_pnl.as_double())
        self._risk.on_position_closed_pnl(pnl)

        # ── ML: resolve the pending trade ────────────────────────────────
        if self._entry_ts_ms is not None:
            self._ml.resolve_trade(self._entry_ts_ms, pnl)
            # Periodically persist ML state
            if self._ml.n_trades % 10 == 0:
                self._ml.save()

        # ── Log ML status ─────────────────────────────────────────────────
        self._log("position_closed", {
            "realized_pnl": pnl,
            "outcome": 1 if pnl > 0 else 0,
            "ml_status": self._ml.status(),
        })

        # ── Clear state ───────────────────────────────────────────────────
        self._entry_price = None
        self._entry_side  = None
        self._entry_ts_ns = None
        self._entry_ts_ms = None
        self._trailing_active = False
        self._last_features = None

    # ── Cancel-replace stale limit ─────────────────────────────────────────────

    def _maybe_cancel_replace(self, current_px: float) -> None:
        if self._pending_limit_price is None:
            return
        if self.cache.orders_open_count(instrument_id=self._instrument_id, strategy_id=self.id) == 0:
            self._pending_limit_price = None
            self._entry_price = None
            return

        max_drift = getattr(self.config, "max_entry_drift_bps", 10.0)
        if should_cancel_stale_limit(
            self._pending_limit_price,
            current_px,
            side=self._entry_side or OrderSide.BUY,
            max_drift_bps=max_drift,
        ):
            self.cancel_all_orders(self._instrument_id, client_id=self._client_id)
            self._log("entry_cancel_stale", {
                "limit": self._pending_limit_price,
                "current": current_px,
            })
            self._pending_limit_price = None
            self._entry_price = None
            self._entry_ts_ms = None

    # ── Helpers ────────────────────────────────────────────────────────────────

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

    def _log(self, event: str, data: dict) -> None:
        if self._metrics:
            self._metrics.log_event(event, data)

    def on_data(self, data: Data) -> None:
        pass
