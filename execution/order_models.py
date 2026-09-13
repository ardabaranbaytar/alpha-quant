"""Pure immutable, validated data contracts for simulated order execution."""

from dataclasses import dataclass
from enum import Enum
from math import isfinite
from typing import Literal

import pandas as pd


class OrderStatus(str, Enum):
    NEW = "NEW"
    SUBMITTED = "SUBMITTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELED = "CANCELED"


@dataclass(frozen=True)
class Order:
    pair: str
    leg_symbol: str
    side: Literal["BUY", "SELL"]
    quantity: float
    order_type: Literal["MARKET"]
    requested_at: pd.Timestamp

    def __post_init__(self):
        if not self.pair or not self.leg_symbol or self.side not in ("BUY", "SELL") or self.order_type != "MARKET":
            raise ValueError("Order requires a pair, symbol, BUY/SELL side, and MARKET type")
        if not isfinite(self.quantity) or self.quantity <= 0 or pd.isna(self.requested_at):
            raise ValueError("Order quantity and timestamp must be finite and positive")


@dataclass(frozen=True)
class Fill:
    order_id: str
    filled_qty: float
    filled_price: float
    fee: float
    filled_at: pd.Timestamp

    def __post_init__(self):
        if (not self.order_id or not isfinite(self.filled_qty) or self.filled_qty <= 0
                or not isfinite(self.filled_price) or self.filled_price <= 0
                or not isfinite(self.fee) or self.fee < 0 or pd.isna(self.filled_at)):
            raise ValueError("Fill requires positive finite quantity/price and a nonnegative finite fee")


@dataclass(frozen=True)
class MarketSnapshot:
    last_price: float
    bar_volume: float
    bid_ask_spread_bps: float = 5.0

    def __post_init__(self):
        if (not isfinite(self.last_price) or self.last_price <= 0 or not isfinite(self.bar_volume)
                or self.bar_volume <= 0 or not isfinite(self.bid_ask_spread_bps) or self.bid_ask_spread_bps < 0):
            raise ValueError("Market snapshot requires positive finite price/volume and nonnegative finite spread")


@dataclass(frozen=True)
class FillResult:
    status: OrderStatus
    fill: Fill | None
    remaining_qty: float
    reason: str = ""

    def __post_init__(self):
        if not isfinite(self.remaining_qty) or self.remaining_qty < 0:
            raise ValueError("Remaining quantity must be finite and nonnegative")
        if self.status in (OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED) and self.fill is None:
            raise ValueError("Filled statuses require a Fill")
        if self.status in (OrderStatus.REJECTED, OrderStatus.CANCELED) and self.fill is not None:
            raise ValueError("Rejected/canceled statuses cannot contain a Fill")
