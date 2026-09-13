"""Pair-order lifecycle coordination with hedge-preserving fills."""

from dataclasses import asdict, is_dataclass, replace
from enum import Enum
import hashlib
import json
import uuid

import pandas as pd

from execution.order_book_simulator import OrderBookSimulator
from execution.order_ledger import OrderLedger
from execution.order_models import FillResult, MarketSnapshot, Order, OrderStatus
from execution.telemetry import NullTelemetryHook, TelemetryHook


class OrderManager:
    def __init__(self, simulator: OrderBookSimulator, telemetry: TelemetryHook = NullTelemetryHook(),
                 order_ledger: OrderLedger | None = None):
        self.simulator = simulator
        self.telemetry = telemetry
        self.order_ledger = order_ledger or OrderLedger()

    @staticmethod
    def _canceled_leg(order: Order) -> FillResult:
        return FillResult(OrderStatus.CANCELED, None, order.quantity, "PAIR_LEG_REJECTED")

    @staticmethod
    def _json_safe(value):
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, pd.Timestamp):
            return value.isoformat()
        if is_dataclass(value):
            return {key: OrderManager._json_safe(item) for key, item in asdict(value).items()}
        if isinstance(value, dict):
            return {str(key): OrderManager._json_safe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [OrderManager._json_safe(item) for item in value]
        return value

    def _emit_safely(self, event: dict) -> None:
        try:
            self.telemetry.emit(self._json_safe(event))
        except Exception:
            pass

    def _persist_order_transitions(self, orders: list[Order] | tuple[Order, ...], statuses,
                                   reasons: str | list[str] | tuple[str, ...] = "") -> None:
        """Append lifecycle transitions in one audit transaction, best-effort.

        ``reasons`` is either one string applied to every leg (CREATED/SUBMITTED,
        where no per-leg outcome exists yet) or a per-leg sequence, so a rejected
        leg and its canceled sibling each keep their own distinct reason instead
        of the sibling's cancellation being overwritten with the rejector's cause.
        """
        if isinstance(reasons, str):
            reasons = [reasons] * len(orders)
        transitions = []
        for order, status, reason in zip(orders, statuses, reasons):
            normalized_status = status.value if isinstance(status, OrderStatus) else str(status)
            transitions.append({
                "order_id": self.simulator._order_id(order),
                "pair": order.pair,
                "timestamp": order.requested_at,
                "leg_symbol": order.leg_symbol,
                "side": order.side,
                "target_shares": float(order.quantity),
                # These are market orders, so no limit price exists.
                "limit_price": None,
                "status": normalized_status,
                "rejection_reason": reason or None,
            })
        try:
            self.order_ledger.record_transitions(transitions)
        except Exception:
            # Custom audit ledger implementations receive the same isolation
            # guarantee as the built-in database ledger.
            pass

    def record_pre_trade_rejection(self, pair: str, symbols: tuple[str, str], action: str,
                                   requested_at: pd.Timestamp, reason: str) -> None:
        """Record a rejected candidate even though no executable Order was created."""
        sides = ("BUY", "SELL") if action == "LONG_SPREAD" else ("SELL", "BUY")
        transitions = [
            {
                "order_id": uuid.uuid4().hex,
                "pair": pair,
                "timestamp": requested_at,
                "leg_symbol": symbol,
                "side": side,
                "target_shares": 0.0,
                "limit_price": None,
                "status": OrderStatus.REJECTED.value,
                "rejection_reason": reason,
            }
            for symbol, side in zip(symbols, sides)
        ]
        try:
            self.order_ledger.record_transitions(transitions)
        except Exception:
            pass

    def _terminal_event_id(self, pair: str, status: OrderStatus, legs: list[FillResult], reason: str) -> str:
        """Create a stable source id before asynchronous audit delivery.

        The Hermes hook may add its own occurrence timestamp while dequeuing.  The
        execution-side id is therefore derived only from the immutable terminal
        order result, so retries do not become distinct audit records.
        """
        material = self._json_safe({"pair": pair, "status": status, "legs": legs, "reason": reason})
        encoded = json.dumps(material, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def _terminal(self, pair: str, status: OrderStatus, legs: list[FillResult], reason: str = "",
                  orders: list[Order] | tuple[Order, ...] = ()) -> dict:
        result = {"pair": pair, "status": status, "legs": legs, "reason": reason}
        self._persist_order_transitions(orders, [leg.status for leg in legs], [leg.reason for leg in legs])
        # Terminal rejections/cancellations are audit events too, not only fills.
        self._emit_safely({"type": "pair_order_terminal",
                           "event_id": self._terminal_event_id(pair, status, legs, reason),
                           "pair": pair,
                           "status": status.value, "reason": reason, "legs": legs})
        return result

    def submit_pair_orders(self, pair: str, leg1_order: Order, leg2_order: Order,
                           market1: MarketSnapshot, market2: MarketSnapshot) -> dict:
        if leg1_order.pair != pair or leg2_order.pair != pair:
            raise ValueError("Both orders must belong to the submitted pair")
        orders = (leg1_order, leg2_order)
        self._persist_order_transitions(orders, ("CREATED", "CREATED"))
        self._persist_order_transitions(orders, (OrderStatus.SUBMITTED, OrderStatus.SUBMITTED))
        first = self.simulator.simulate_fill(leg1_order, market1)
        if first.status is OrderStatus.REJECTED:
            return self._terminal(pair, OrderStatus.REJECTED, [first, self._canceled_leg(leg2_order)], first.reason, orders)
        second = self.simulator.simulate_fill(leg2_order, market2)
        if second.status is OrderStatus.REJECTED:
            return self._terminal(pair, OrderStatus.REJECTED, [self._canceled_leg(leg1_order), second], second.reason, orders)

        # One common completion fraction preserves requested 1:N hedge ratios.
        fill_fraction = min(first.fill.filled_qty / leg1_order.quantity, second.fill.filled_qty / leg2_order.quantity)
        if fill_fraction < 1:
            constrained1 = replace(leg1_order, quantity=leg1_order.quantity * fill_fraction)
            constrained2 = replace(leg2_order, quantity=leg2_order.quantity * fill_fraction)
            simulated1 = self.simulator.simulate_fill(constrained1, market1)
            simulated2 = self.simulator.simulate_fill(constrained2, market2)
            first = FillResult(OrderStatus.PARTIALLY_FILLED, simulated1.fill,
                               leg1_order.quantity - simulated1.fill.filled_qty, "HEDGE_LIQUIDITY_CAP")
            second = FillResult(OrderStatus.PARTIALLY_FILLED, simulated2.fill,
                                leg2_order.quantity - simulated2.fill.filled_qty, "HEDGE_LIQUIDITY_CAP")

        status = OrderStatus.PARTIALLY_FILLED if fill_fraction < 1 else OrderStatus.FILLED
        return self._terminal(pair, status, [first, second], "HEDGE_LIQUIDITY_CAP" if fill_fraction < 1 else "", orders)

    def reconcile_open_orders(self) -> dict:
        return {"status": "NOT_IMPLEMENTED", "open_orders": []}
