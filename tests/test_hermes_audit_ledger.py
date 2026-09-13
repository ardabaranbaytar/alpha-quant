import tempfile
import unittest
from pathlib import Path

from hermes.audit_ledger import AuditLedger
from hermes.models import AuditEvent, AuditEventKind


def event(event_id, occurred_at, payload=None):
    return AuditEvent(event_id, AuditEventKind.STABILITY_ALERT, occurred_at, payload or {"score": 0.3})


class HermesAuditLedgerTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temporary_directory.name) / "hermes.db")
        self.ledger = AuditLedger(self.path)

    def tearDown(self):
        self.ledger.close()
        self.temporary_directory.cleanup()

    def test_append_and_get_round_trip_every_field(self):
        original = event("event-1", "2026-09-09T14:30:00Z", {"pair": "AAA / BBB", "score": 0.3})
        self.assertTrue(self.ledger.append(original))
        self.assertEqual(self.ledger.get("event-1"), original)

    def test_append_is_idempotent(self):
        original = event("event-1", "2026-09-09T14:30:00Z")
        self.assertTrue(self.ledger.append(original))
        self.assertFalse(self.ledger.append(original))
        self.assertEqual(len(self.ledger.iter_since("2026-01-01T00:00:00Z")), 1)

    def test_iter_since_filters_and_orders_by_epoch(self):
        self.ledger.append(event("late", "2026-09-09T14:32:00Z"))
        self.ledger.append(event("early", "2026-09-09T14:30:00Z"))
        self.ledger.append(event("middle", "2026-09-09T14:31:00Z"))
        events = self.ledger.iter_since("2026-09-09T14:31:00Z")
        self.assertEqual([item.event_id for item in events], ["middle", "late"])

    def test_context_manager_and_wal_pragmas(self):
        self.ledger.close()
        with AuditLedger(self.path) as ledger:
            self.assertEqual(ledger._connection.execute("PRAGMA journal_mode;").fetchone()[0].lower(), "wal")
            self.assertEqual(ledger._connection.execute("PRAGMA busy_timeout;").fetchone()[0], 5000)
            self.assertEqual(ledger._connection.execute("PRAGMA synchronous;").fetchone()[0], 1)
            indexes = {row[1] for row in ledger._connection.execute("PRAGMA index_list(audit_events);").fetchall()}
            self.assertIn("idx_audit_events_epoch_ms", indexes)
        self.assertIsNone(ledger._connection)
