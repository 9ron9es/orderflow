"""
Order sizing and entry order construction.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.enums import TimeInForce
from nautilus_trader.model.orders.base import Order

if TYPE_CHECKING:
    from nautilus_trader.common.factories import OrderFactory
    from nautilus_trader.model.instruments import Instrument


@dataclass(slots=True)
class BracketSpec:
    """Stop-loss and take-profit bracket configuration."""

    stoploss_pct: float                 # Stop-loss distance (% below entry)
    target_pct: float                   # Take-profit distance (% above entry)
    trailing_trigger_pct: float         # Profit % to activate trailing stop
    trailing_offset_pct: float          # Trailing stop distance (% below peak)


def compute_bracket_prices(
    entry_price: float,
    side: OrderSide,
    spec: BracketSpec,
) -> tuple[float, float]:
    """
    Compute stop and target prices from entry and bracket spec.

    Parameters
    ----------
    entry_price : float
        Entry fill price.
    side : OrderSide
        BUY (long) or SELL (short).
    spec : BracketSpec
        Bracket specification.

    Returns
    -------
    tuple[float, float]
        (stop_price, target_price)
    """
    # FIX 12: stoploss_pct and target_pct are decimal fractions (e.g. 0.018 = 1.8%).
    # The old code divided by 100 again → 0.00018 = 0.018% stop, firing on every
    # tick of noise. Use the fractions directly.
    if side == OrderSide.BUY:
        stop = entry_price * (1.0 - spec.stoploss_pct)
        target = entry_price * (1.0 + spec.target_pct)
    else:  # SELL (short)
        stop = entry_price * (1.0 + spec.stoploss_pct)
        target = entry_price * (1.0 - spec.target_pct)
    return stop, target


def build_exit_order(
    order_factory: OrderFactory,
    instrument: Instrument,
    *,
    price: float,
    qty: Decimal,
    side: OrderSide,
    use_market: bool = True,
) -> Order:
    """
    Build a market or limit exit order.

    Parameters
    ----------
    order_factory : OrderFactory
        Nautilus order factory.
    instrument : Instrument
        Trading instrument.
    price : float
        Limit price (ignored if use_market=True).
    qty : Decimal
        Order quantity.
    side : OrderSide
        BUY or SELL.
    use_market : bool
        If True, market order; else limit order.

    Returns
    -------
    Order
        Exit order ready to submit.
    """
    if qty <= 0:
        raise ValueError("qty must be positive")

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
        post_only=False,
        time_in_force=TimeInForce.GTC,
    )


def estimate_order_qty(
    instrument: Instrument,
    *,
    side: OrderSide,
    quote_balance: float,
    price: float,
    max_fraction: float,
    max_notional_usdt: float | None,
) -> Decimal:
    """Return base quantity from quote margin (linear USDT-M: same sizing for BUY and SELL)."""
    _ = side  # reserved for venue-specific margin rules
    available = quote_balance * max_fraction
    if max_notional_usdt is not None:
        available = min(available, float(max_notional_usdt))
    if price <= 0 or available <= 0:
        return instrument.make_qty(0).as_decimal()
    qty = available / price
    q = instrument.make_qty(qty)
    return q.as_decimal()


def build_entry_order(
    order_factory: OrderFactory,
    instrument: Instrument,
    *,
    side: OrderSide,
    price: float,
    qty: Decimal,
    use_market: bool,
    post_only: bool,
) -> Order:
    if qty <= 0:
        raise ValueError("qty must be positive")
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


def should_cancel_stale_limit(
    order_price: float,
    current_price: float,
    *,
    side: OrderSide,
    max_drift_bps: float = 8.0,
) -> bool:
    """
    True if the working limit entry is too far from the current market (bps drift).

    ``side`` is reserved for directional rules; drift is symmetric in bps for now.
    """
    _ = side
    if current_price <= 0:
        return False
    drift_bps = abs(order_price - current_price) / current_price * 10_000.0
    return drift_bps > max_drift_bps
