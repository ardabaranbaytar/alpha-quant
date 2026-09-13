import unittest
import json

import pandas as pd

from execution.order_book_simulator import OrderBookSimulator
from execution.order_manager import OrderManager
from execution.order_models import MarketSnapshot, Order, OrderStatus
from research.run_backtest import ExecutionConfig


class RecordingTelemetry:
    def __init__(self):
        self.events = []

    def emit(self, event):
        self.events.append(event)


class FailingTelemetry:
    def emit(self, event):
        raise RuntimeError("telemetry unavailable")


class RecordingOrderLedger:
    def __init__(self):
        self.batches = []

    def record_transitions(self, transitions):
        self.batches.append(list(transitions))


class OrderManagerTests(unittest.TestCase):
    def setUp(self):
        self.costs = ExecutionConfig(commission_bps=10, slippage_bps=5, annual_borrow_rate=0.03)
        self.simulator = OrderBookSimulator(self.costs)
        self.timestamp = pd.Timestamp("2026-09-09T14:30:00Z")
        self.ledger = RecordingOrderLedger()

    def manager(self, telemetry=None):
        if telemetry is None:
            return OrderManager(self.simulator, order_ledger=self.ledger)
        return OrderManager(self.simulator, telemetry, order_ledger=self.ledger)

    def orders(self, quantity=100):
        return (
            Order("AAA / BBB", "AAA", "BUY", quantity, "MARKET", self.timestamp),
            Order("AAA / BBB", "BBB", "SELL", quantity, "MARKET", self.timestamp),
        )

    def test_both_legs_fill_and_report_costs(self):
        telemetry = RecordingTelemetry()
        manager = self.manager(telemetry)
        leg1, leg2 = self.orders()
        result = manager.submit_pair_orders("AAA / BBB", leg1, leg2,
                                            MarketSnapshot(100, 10_000), MarketSnapshot(50, 10_000))
        self.assertEqual(result["status"], OrderStatus.FILLED)
        self.assertEqual([leg.status for leg in result["legs"]], [OrderStatus.FILLED, OrderStatus.FILLED])
        self.assertGreater(result["legs"][0].fill.fee, 0)
        self.assertGreater(result["legs"][1].fill.fee, 0)
        self.assertEqual(len(telemetry.events), 1)

    def test_rejected_leg_cancels_the_other_leg(self):
        manager = self.manager()
        leg1, leg2 = self.orders()
        result = manager.submit_pair_orders("AAA / BBB", leg1, leg2,
                                            MarketSnapshot(100, 10_000), MarketSnapshot(50, 10_000, 501))
        self.assertEqual(result["status"], OrderStatus.REJECTED)
        self.assertEqual(result["legs"][0].status, OrderStatus.CANCELED)
        self.assertIsNone(result["legs"][0].fill)
        self.assertEqual(result["legs"][1].status, OrderStatus.REJECTED)

    def test_telemetry_failure_does_not_interrupt_filled_pair(self):
        manager = self.manager(FailingTelemetry())
        leg1, leg2 = self.orders()
        result = manager.submit_pair_orders("AAA / BBB", leg1, leg2,
                                            MarketSnapshot(100, 10_000), MarketSnapshot(50, 10_000))
        self.assertEqual(result["status"], OrderStatus.FILLED)

    def test_partial_fill_reports_remaining_quantities(self):
        manager = self.manager()
        leg1, leg2 = self.orders(quantity=150)
        result = manager.submit_pair_orders("AAA / BBB", leg1, leg2,
                                            MarketSnapshot(100, 1_000), MarketSnapshot(50, 1_000))
        self.assertEqual(result["status"], OrderStatus.PARTIALLY_FILLED)
        for leg in result["legs"]:
            self.assertEqual(leg.status, OrderStatus.PARTIALLY_FILLED)
            self.assertEqual(leg.fill.filled_qty, 100)
            self.assertEqual(leg.remaining_qty, 50)

    def test_asymmetric_liquidity_preserves_the_requested_hedge_ratio(self):
        manager = self.manager()
        leg1, _ = self.orders(quantity=100)
        leg2 = Order("AAA / BBB", "BBB", "SELL", 200, "MARKET", self.timestamp)
        result = manager.submit_pair_orders("AAA / BBB", leg1, leg2,
                                            MarketSnapshot(100, 1_000), MarketSnapshot(50, 500))
        first, second = result["legs"]
        self.assertEqual(result["status"], OrderStatus.PARTIALLY_FILLED)
        self.assertEqual((first.fill.filled_qty, second.fill.filled_qty), (25, 50))
        self.assertEqual(second.fill.filled_qty / first.fill.filled_qty, 2)

    def test_rejection_emits_json_safe_telemetry_with_reason(self):
        telemetry = RecordingTelemetry()
        manager = self.manager(telemetry)
        leg1, leg2 = self.orders()
        manager.submit_pair_orders("AAA / BBB", leg1, leg2,
                                   MarketSnapshot(100, 10_000), MarketSnapshot(50, 10_000, 501))
        self.assertEqual(telemetry.events[0]["status"], "REJECTED")
        self.assertEqual(telemetry.events[0]["reason"], "MAX_GAP_EXCEEDED")
        json.dumps(telemetry.events[0])

    def test_lifecycle_transitions_are_persisted_for_each_order_leg(self):
        ledger = RecordingOrderLedger()
        manager = OrderManager(self.simulator, order_ledger=ledger)
        leg1, leg2 = self.orders()
        manager.submit_pair_orders("AAA / BBB", leg1, leg2,
                                   MarketSnapshot(100, 10_000), MarketSnapshot(50, 10_000))

        self.assertEqual(
            [[row["status"] for row in batch] for batch in ledger.batches],
            [["CREATED", "CREATED"], ["SUBMITTED", "SUBMITTED"], ["FILLED", "FILLED"]],
        )
        self.assertTrue(all(row["target_shares"] == 100.0 for batch in ledger.batches for row in batch))

    def test_pre_trade_rejection_is_persisted_without_creating_executable_orders(self):
        ledger = RecordingOrderLedger()
        manager = OrderManager(self.simulator, order_ledger=ledger)
        manager.record_pre_trade_rejection(
            "AAA / BBB", ("AAA", "BBB"), "LONG_SPREAD", self.timestamp, "Risk limit hit"
        )

        self.assertEqual([row["status"] for row in ledger.batches[0]], ["REJECTED", "REJECTED"])
        self.assertEqual([row["rejection_reason"] for row in ledger.batches[0]], ["Risk limit hit", "Risk limit hit"])
