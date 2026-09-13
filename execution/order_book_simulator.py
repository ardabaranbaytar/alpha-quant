"""Pure deterministic market-order fill simulator; no database or network I/O."""

import hashlib

import pandas as pd

from execution.order_models import Fill, FillResult, MarketSnapshot, Order, OrderStatus
from research.run_backtest import ExecutionConfig


class OrderBookSimulator:
    MAX_PARTICIPATION = 0.10
    LIQUIDITY_PENALTY_BPS = 25.0
    MAX_GAP_BPS = 500.0

    def __init__(self, costs: ExecutionConfig):
        self.costs = costs

    @staticmethod
    def _order_id(order: Order) -> str:
        payload = "|".join((order.pair, order.leg_symbol, order.side, repr(order.quantity),
                            order.order_type, pd.Timestamp(order.requested_at).isoformat()))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]

    def simulate_fill(self, order: Order, market: MarketSnapshot) -> FillResult:
        if market.bid_ask_spread_bps > self.MAX_GAP_BPS:
            return FillResult(OrderStatus.REJECTED, None, order.quantity, "MAX_GAP_EXCEEDED")
        max_quantity = market.bar_volume * self.MAX_PARTICIPATION
        filled_qty = min(order.quantity, max_quantity)
        participation_rate = filled_qty / market.bar_volume
        effective_slippage_bps = self.costs.slippage_bps + participation_rate * self.LIQUIDITY_PENALTY_BPS
        direction = 1 if order.side == "BUY" else -1
        filled_price = float(market.last_price * (1 + direction * effective_slippage_bps / 10000))
        fee = float(filled_qty * filled_price * self.costs.commission_bps / 10000)
        fill = Fill(self._order_id(order), float(filled_qty), filled_price, fee, pd.Timestamp(order.requested_at))
        remaining = float(order.quantity - filled_qty)
        return FillResult(OrderStatus.PARTIALLY_FILLED if remaining else OrderStatus.FILLED, fill, remaining)
