import unittest
from unittest.mock import patch

import pandas as pd
from sqlalchemy import create_engine, text

from execution.order_ledger import OrderLedger


class OrderLedgerTests(unittest.TestCase):
    def test_creates_orders_table_and_appends_lifecycle_rows(self):
        engine = create_engine("sqlite://")
        transition = {
            "order_id": "order-1",
            "pair": "AAA / BBB",
            "timestamp": pd.Timestamp("2026-09-09T14:30:00Z"),
            "leg_symbol": "AAA",
            "side": "BUY",
            "target_shares": 10.0,
            "limit_price": None,
            "status": "CREATED",
            "rejection_reason": None,
        }
        with patch("execution.order_ledger.db.engine", engine):
            OrderLedger().record_transitions([transition])
            with engine.connect() as connection:
                stored = connection.execute(text("""
                    SELECT order_id, pair, leg_symbol, side, target_shares, status
                    FROM orders
                """)).mappings().one()
        engine.dispose()

        self.assertEqual(dict(stored), {
            "order_id": "order-1",
            "pair": "AAA / BBB",
            "leg_symbol": "AAA",
            "side": "BUY",
            "target_shares": 10.0,
            "status": "CREATED",
        })

    def test_database_failure_is_isolated_from_execution(self):
        ledger = OrderLedger()
        with patch("execution.order_ledger.db.engine.begin", side_effect=RuntimeError("database unavailable")):
            ledger.record_transitions([{
                "order_id": "order-1",
                "pair": "AAA / BBB",
                "timestamp": pd.Timestamp("2026-09-09T14:30:00Z"),
                "leg_symbol": "AAA",
                "side": "BUY",
                "target_shares": 10.0,
                "limit_price": None,
                "status": "CREATED",
                "rejection_reason": None,
            }])
