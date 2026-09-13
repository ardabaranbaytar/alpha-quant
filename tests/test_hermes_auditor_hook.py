import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from hermes.audit_ledger import AuditLedger
from hermes.auditor_hook import HermesAuditorHook


def wait_for(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class HermesAuditorHookTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temporary_directory.name) / "audit.db")

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_normal_emit_writes_the_ledger_and_is_idempotent(self):
        hook = HermesAuditorHook(self.path, udp_port=65500)
        event = {"type": "pair_order_terminal", "pair": "AAA / BBB", "status": "REJECTED",
                 "reason": "MAX_GAP", "occurred_at": "2026-09-09T14:30:00Z"}
        try:
            hook.emit(event)
            hook.emit(event)
            def has_one_event():
                if not Path(self.path).exists():
                    return False
                with AuditLedger(self.path) as ledger:
                    return len(ledger.iter_since("2026-01-01T00:00:00Z")) == 1
            self.assertTrue(wait_for(has_one_event))
        finally:
            hook.stop()
        with AuditLedger(self.path) as ledger:
            stored = ledger.iter_since("2026-01-01T00:00:00Z")
            self.assertEqual(len(stored), 1)
            self.assertEqual(stored[0].kind.value, "PAIR_ORDER_TERMINAL")

    def test_full_queue_increments_drop_count_without_blocking(self):
        hook = HermesAuditorHook(self.path, queue_maxsize=1, udp_port=65500)
        try:
            import queue
            with patch.object(hook._queue, "put_nowait", side_effect=queue.Full):
                start = time.perf_counter()
                hook.emit({"type": "pair_order_terminal"})
            self.assertLess(time.perf_counter() - start, 0.02)
            self.assertEqual(hook.dropped_count, 1)
        finally:
            hook.stop()

    def test_stop_is_clean(self):
        hook = HermesAuditorHook(self.path, udp_port=65500)
        hook.stop()
        hook.stop()
        self.assertFalse(hook._thread.is_alive())
