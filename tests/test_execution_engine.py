import unittest
from unittest.mock import MagicMock, patch
import sys
import tempfile
from pathlib import Path

import pandas as pd


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar(self):
        return self.value


class _ScalarsResult:
    def __init__(self, values):
        self.values = values

    def scalars(self):
        return self

    def all(self):
        return self.values


class _MappingsResult:
    def __init__(self, values):
        self.values = values

    def mappings(self):
        return self

    def all(self):
        return self.values


class ExecutionEngineTests(unittest.TestCase):
    def tearDown(self):
        # Preserve the research package's import-isolation contract for later tests.
        for module in ("execution.execution_engine", "config.database", "config.settings", "dotenv"):
            sys.modules.pop(module, None)

    def test_empty_price_cycle_returns_without_attribute_errors(self):
        from execution.execution_engine import ExecutionEngine
        engine = ExecutionEngine()
        with patch("execution.execution_engine.signals_hub._build_price_matrix", return_value=pd.DataFrame()), \
             patch.object(engine, "_get_open_positions", return_value={}):
            self.assertIsNone(engine.manage_orders_and_positions())

    def test_current_percentage_risk_limits_approve_an_empty_ledger(self):
        from execution.execution_engine import ExecutionEngine
        conn = MagicMock()
        conn.execute.side_effect = [_ScalarResult(0.0), _ScalarsResult([]), _ScalarResult(0), _MappingsResult([]), _ScalarResult(None)]
        approved, reason = ExecutionEngine()._is_risk_approved(conn, "AAA / BBB", "AAA", "BBB")
        self.assertTrue(approved)
        self.assertEqual(reason, "Risk Approved")

    def test_opening_a_position_submits_through_order_manager(self):
        from execution.execution_engine import ExecutionEngine
        from execution.order_models import Fill, FillResult, OrderStatus

        class StubOrderManager:
            def __init__(self):
                self.calls = []

            def submit_pair_orders(self, *args):
                self.calls.append(args)
                timestamp = pd.Timestamp("2026-09-09T14:30:00Z")
                return {"status": OrderStatus.FILLED, "legs": [
                    FillResult(OrderStatus.FILLED, Fill("a", 10, 100.05, 0.1, timestamp), 0),
                    FillResult(OrderStatus.FILLED, Fill("b", 20, 50.0, 0.1, timestamp), 0),
                ]}

        manager = StubOrderManager()
        engine = ExecutionEngine(order_manager=manager)
        opportunity = {"pair": "AAA / BBB", "action": "LONG_SPREAD", "z_score": -2.1,
                       "price_a": 100.0, "price_b": 50.0}
        begin = MagicMock()
        begin.return_value.__enter__.return_value = MagicMock()
        connect = MagicMock()
        connect.return_value.__enter__.return_value = MagicMock()
        prices = pd.DataFrame({"AAA": [100.0], "BBB": [50.0]})
        with patch("execution.execution_engine.signals_hub._build_price_matrix", return_value=prices), \
             patch.object(engine, "_get_open_positions", return_value={}), \
             patch.object(engine, "_scan_opportunities", return_value=[opportunity]), \
             patch.object(engine, "_pre_trade_risk_gate", return_value=(True, "Approved")), \
             patch("execution.execution_engine.db.engine.connect", connect), \
             patch("execution.execution_engine.db.engine.begin", begin):
            engine.manage_orders_and_positions()
        self.assertEqual(len(manager.calls), 1)
        self.assertEqual(manager.calls[0][0], "AAA / BBB")
        begin.assert_called_once()

    def test_closing_a_position_submits_offsetting_legs_through_order_manager(self):
        from execution.execution_engine import ExecutionEngine
        from execution.order_models import Fill, FillResult, MarketSnapshot, OrderStatus

        timestamp = pd.Timestamp("2026-09-09T14:30:00Z")

        class StubOrderManager:
            def __init__(self):
                self.calls = []

            def submit_pair_orders(self, *args):
                self.calls.append(args)
                return {"status": OrderStatus.FILLED, "legs": [
                    FillResult(OrderStatus.FILLED, Fill("close-a", 50, 101, 0.5, timestamp), 0),
                    FillResult(OrderStatus.FILLED, Fill("close-b", 100, 49, 0.5, timestamp), 0),
                ]}

        manager = StubOrderManager()
        engine = ExecutionEngine(order_manager=manager)
        record = {
            "id": [7, 8], "pair": "AAA / BBB", "action": "LONG_SPREAD",
            "price_a_entry": 100.0, "price_b_entry": 50.0, "entry_time": timestamp,
            "legs": [
                {"id": 7, "symbol": "AAA", "side": "BUY", "quantity": 50.0, "entry_price": 100.0},
                {"id": 8, "symbol": "BBB", "side": "SELL", "quantity": 100.0, "entry_price": 50.0},
            ],
        }
        begin = MagicMock()
        begin.return_value.__enter__.return_value = MagicMock()
        with patch.object(engine, "_market_snapshot", return_value=MarketSnapshot(100, 10_000)), \
             patch.object(engine, "_net_pnl", return_value=12.34), \
             patch("execution.execution_engine.db.engine.begin", begin):
            self.assertTrue(engine._close_position(record, [101.0, 49.0], "mean reversion"))
        self.assertEqual(len(manager.calls), 1)
        _, close_a, close_b, *_ = manager.calls[0]
        self.assertEqual((close_a.side, close_b.side), ("SELL", "BUY"))
        begin.assert_called_once()

    @staticmethod
    def _open_record():
        timestamp = pd.Timestamp("2026-09-09T14:30:00Z")
        return {
            "id": [7, 8], "pair": "NFLX / TSLA", "action": "LONG_SPREAD",
            "price_a_entry": 100.0, "price_b_entry": 50.0, "entry_time": timestamp,
            "legs": [
                {"id": 7, "symbol": "NFLX", "side": "BUY", "quantity": 50.0,
                 "entry_price": 100.0, "current_price": 101.0},
                {"id": 8, "symbol": "TSLA", "side": "SELL", "quantity": 100.0,
                 "entry_price": 50.0, "current_price": 49.0},
            ],
        }

    def test_routine_check_does_not_close_position_without_stability_alert(self):
        from execution.execution_engine import ExecutionEngine

        strategy = MagicMock()
        strategy.fit.return_value = None
        engine = ExecutionEngine(strategy=strategy)
        prices = pd.DataFrame({"NFLX": [101.0], "TSLA": [49.0]})
        record = self._open_record()
        with patch("execution.execution_engine.signals_hub._build_price_matrix", return_value=prices), \
             patch.object(engine, "_get_open_positions", return_value={record["pair"]: record}), \
             patch.object(engine, "_scan_opportunities", return_value=[]), \
             patch.object(engine, "_active_hermes_stability_alert", return_value=None), \
             patch.object(engine, "_net_pnl", return_value=0.0), \
             patch.object(engine, "_close_position") as close_position:
            engine.manage_orders_and_positions()

        close_position.assert_not_called()

    def test_missing_hermes_ledger_is_not_an_active_alert(self):
        from execution.execution_engine import ExecutionEngine

        with tempfile.TemporaryDirectory() as directory:
            engine = ExecutionEngine(hermes_ledger_path=str(Path(directory) / "missing-hermes.db"))
            self.assertIsNone(engine._active_hermes_stability_alert("AAA / BBB"))
            self.assertEqual(engine._hermes_allows_pair("AAA / BBB"), (True, "Hermes clear"))

    def test_active_stability_alert_closes_open_legs_and_writes_de_risk_ledger_event(self):
        from execution.execution_engine import ExecutionEngine
        from hermes.audit_ledger import AuditLedger
        from hermes.models import AuditEventKind

        with tempfile.TemporaryDirectory() as directory:
            ledger_path = str(Path(directory) / "hermes.db")
            engine = ExecutionEngine(hermes_ledger_path=ledger_path)
            prices = pd.DataFrame({"NFLX": [101.0], "TSLA": [49.0]})
            record = self._open_record()
            alert = {"event_id": "alert-123", "payload": {"pair": record["pair"], "active": True,
                     "report": {"coint_pvalue": 0.12, "ou_half_life": 31.5}}}
            with patch("execution.execution_engine.signals_hub._build_price_matrix", return_value=prices), \
                 patch.object(engine, "_get_open_positions", return_value={record["pair"]: record}), \
                 patch.object(engine, "_scan_opportunities", return_value=[]), \
                 patch.object(engine, "_active_hermes_stability_alert", return_value=alert), \
                 patch.object(engine, "_close_position", return_value=True) as close_position:
                engine.manage_orders_and_positions()

            close_position.assert_called_once_with(record, [101.0, 49.0], "STABILITY_KILL_SWITCH")
            with AuditLedger(ledger_path) as ledger:
                events = ledger.iter_since("1970-01-01T00:00:00Z")
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0].kind, AuditEventKind.DE_RISK_TRIGGERED)
            self.assertEqual(events[0].payload["pair"], "NFLX / TSLA")
            self.assertEqual(events[0].payload["closed_symbols"], ["NFLX", "TSLA"])
            self.assertEqual(events[0].payload["coint_pvalue"], 0.12)
            self.assertEqual(events[0].payload["ou_half_life"], 31.5)

    def test_invalid_or_short_price_series_is_skipped_before_kalman_analysis(self):
        from execution.execution_engine import ExecutionEngine

        engine = ExecutionEngine()
        valid = pd.Series(range(100, 125), index=pd.bdate_range("2025-01-02", periods=25), dtype=float)
        non_positive = valid.copy()
        non_positive.iloc[-1] = 0.0
        for invalid in (pd.Series([100.0] * 19, index=pd.bdate_range("2025-01-02", periods=19)), non_positive):
            matrix = pd.DataFrame({"AAA": valid, "BBB": invalid})
            with patch.object(engine.strategy, "analyze") as analyze:
                self.assertEqual(engine._scan_opportunities(matrix), [])
            analyze.assert_not_called()
