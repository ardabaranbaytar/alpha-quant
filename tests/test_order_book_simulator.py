import unittest

import pandas as pd

from execution.order_book_simulator import OrderBookSimulator
from execution.order_models import Fill, MarketSnapshot, Order, OrderStatus
from research.run_backtest import ExecutionConfig


class OrderBookSimulatorTests(unittest.TestCase):
    def setUp(self):
        self.costs = ExecutionConfig(commission_bps=10, slippage_bps=5, annual_borrow_rate=0.03)
        self.simulator = OrderBookSimulator(self.costs)
        self.timestamp = pd.Timestamp("2026-09-09T14:30:00Z")

    def order(self, side="BUY", quantity=100):
        return Order("AAA / BBB", "AAA", side, quantity, "MARKET", self.timestamp)

    def test_full_fill_with_sufficient_liquidity_prices_and_charges_commission(self):
        result = self.simulator.simulate_fill(self.order(), MarketSnapshot(100, 10_000))
        fill = result.fill
        expected_slippage = 5 + (100 / 10_000) * 25
        self.assertEqual(result.status, OrderStatus.FILLED)
        self.assertEqual(fill.filled_qty, 100)
        self.assertAlmostEqual(fill.filled_price, 100 * (1 + expected_slippage / 10_000))
        self.assertAlmostEqual(fill.fee, fill.filled_qty * fill.filled_price * 10 / 10_000)

    def test_volume_cap_returns_partially_filled_status(self):
        result = self.simulator.simulate_fill(self.order(quantity=150), MarketSnapshot(100, 1_000))
        self.assertEqual(result.status, OrderStatus.PARTIALLY_FILLED)
        self.assertEqual(result.fill.filled_qty, 100)
        self.assertEqual(result.remaining_qty, 50)

    def test_excessive_gap_is_rejected(self):
        result = self.simulator.simulate_fill(self.order(), MarketSnapshot(100, 10_000, 501))
        self.assertEqual(result.status, OrderStatus.REJECTED)
        self.assertEqual(result.reason, "MAX_GAP_EXCEEDED")
        self.assertIsNone(result.fill)

    def test_buy_and_sell_slippage_are_adverse(self):
        market = MarketSnapshot(100, 10_000)
        buy = self.simulator.simulate_fill(self.order("BUY"), market)
        sell = self.simulator.simulate_fill(self.order("SELL"), market)
        self.assertGreater(buy.fill.filled_price, market.last_price)
        self.assertLess(sell.fill.filled_price, market.last_price)

    def test_nonpositive_quantity_is_rejected_with_value_error(self):
        for quantity in (0, -1):
            with self.assertRaises(ValueError):
                self.simulator.simulate_fill(self.order(quantity=quantity), MarketSnapshot(100, 10_000))

    def test_models_reject_invalid_prices_and_nan_values_at_construction(self):
        with self.assertRaises(ValueError):
            MarketSnapshot(0, 1_000)
        with self.assertRaises(ValueError):
            MarketSnapshot(100, float("nan"))
        with self.assertRaises(ValueError):
            Fill("order", 1, 0, 0, self.timestamp)

    def test_same_inputs_are_deterministically_repeatable(self):
        order, market = self.order(), MarketSnapshot(100, 10_000)
        self.assertEqual(self.simulator.simulate_fill(order, market), self.simulator.simulate_fill(order, market))
