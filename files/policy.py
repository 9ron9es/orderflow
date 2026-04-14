"""
execution/policy.py — Bidirectional order sizing + construction.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from nautilus_trader.model.enums import OrderSide, TimeInForce

if TYPE_CHECKING:
    from nautilus_trader.common.factories import OrderFactory
    from nautilus_trader.model.instruments import Instrument
    from nautilus_trader.model.orders.base import Order


def estimate_order_qty(
    instrument,
    *,
    side: OrderSide,
    quote_balance: float,
    price: float,
    max_fraction: float,
    max_notional_usdt: float | None,
) -> Decimal:
    """Notional-based sizing for both long and short."""
    available = quote_balance * max_fraction
    if max_notional_usdt is not None:
        available = min(available, float(max_notional_usdt))
    if price <= 0 or available <= 0:
        return instrument.make_qty(0).as_decimal()
    return instrument.make_qty(available / price).as_decimal()


def build_entry_order(
    order_factory,
    instrument,
    *,
    side: OrderSide,
    price: float,
    qty: Decimal,
    use_market: bool,
    post_only: bool,
):
    if qty <= 0:
        raise ValueError(f"qty must be positive, got {qty}")
    if use_market:
        return order_factory.market(
            instrument_id=instrument.id,
            order_side=side,
            quantity=instrument.make_qty(qty),
        )
    return order_factory.limit(
        instrument_id=instrument.id,
        order_side=side,
        quantity=instrument.make_qty(qty),
        price=instrument.make_price(price),
        post_only=post_only,
        time_in_force=TimeInForce.GTC,
    )


@dataclass(frozen=True)
class BracketSpec:
    stoploss_pct: float
    target_pct: float
    trailing_trigger_pct: float = 0.012
    trailing_offset_pct: float = 0.008

    @property
    def reward_risk(self) -> float:
        return self.target_pct / self.stoploss_pct


def compute_bracket_prices(
    entry_price: float,
    side: OrderSide,
    spec: BracketSpec,
) -> tuple[float, float]:
    """Return (stop_price, target_price)."""
    if side == OrderSide.BUY:
        return (
            entry_price * (1.0 - spec.stoploss_pct),
            entry_price * (1.0 + spec.target_pct),
        )
    else:
        return (
            entry_price * (1.0 + spec.stoploss_pct),
            entry_price * (1.0 - spec.target_pct),
        )


def should_cancel_stale_limit(
    order_price: float,
    current_price: float,
    *,
    side: OrderSide,
    max_drift_bps: float = 8.0,
) -> bool:
    if current_price <= 0:
        return False
    drift_bps = abs(order_price - current_price) / current_price * 10_000.0
    return drift_bps > max_drift_bps
