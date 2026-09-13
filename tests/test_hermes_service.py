import socket
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd

from hermes.audit_ledger import AuditLedger
from hermes.models import AuditEvent, AuditEventKind, StabilityReport
from hermes.service import HermesService


def wait_for(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class HermesServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temporary_directory.name) / "service.db")

    def tearDown(self):
        self.temporary_directory.cleanup()

    def terminal_event(self):
        return AuditEvent("source-1", AuditEventKind.PAIR_ORDER_TERMINAL, "2026-09-09T14:30:00Z", {"pair": "AAA / BBB"})

    def test_udp_trigger_creates_stability_alert_and_stop_is_idempotent(self):
        with AuditLedger(self.path) as ledger:
            ledger.append(self.terminal_event())
        unstable = StabilityReport("AAA / BBB", 200, 1, 1, 0.2, 0.2, 30, 0.6, False, ("HURST",))
        service = HermesService(udp_port=0, poll_interval_s=60, ledger_path=self.path,
                                price_reader=lambda pair: (pd.Series([1.0]), pd.Series([1.0])))
        try:
            with patch("hermes.service.analyze_pair_stability", return_value=unstable):
                service.start()
                sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sender.sendto(b"wake", ("127.0.0.1", service.udp_port))
                sender.close()
                def has_alert():
                    with AuditLedger(self.path) as ledger:
                        return len(ledger.iter_since("1970-01-01T00:00:00Z")) == 2
                self.assertTrue(wait_for(has_alert))
        finally:
            service.stop()
            service.stop()
        with AuditLedger(self.path) as ledger:
            alerts = [item for item in ledger.iter_since("1970-01-01T00:00:00Z") if item.kind is AuditEventKind.STABILITY_ALERT]
            self.assertEqual(len(alerts), 1)

    def test_reader_exception_does_not_stop_the_service(self):
        with AuditLedger(self.path) as ledger:
            ledger.append(self.terminal_event())
        service = HermesService(udp_port=0, poll_interval_s=60, ledger_path=self.path,
                                price_reader=lambda pair: (_ for _ in ()).throw(RuntimeError("bad reader")))
        try:
            service.start()
            sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sender.sendto(b"wake", ("127.0.0.1", service.udp_port))
            sender.close()
            self.assertTrue(wait_for(lambda: service._worker_thread.is_alive()))
        finally:
            service.stop()

    def test_unstable_event_adds_diagnosis_when_ollama_responds(self):
        with AuditLedger(self.path) as ledger:
            ledger.append(self.terminal_event())
            unstable = StabilityReport("AAA / BBB", 200, 1, 1, 0.2, 0.2, 30, 0.6, False, ("HURST",))
            client = MagicMock()
            client.diagnose.return_value = "Inspect the pair."
            service = HermesService(ledger_path=self.path, price_reader=lambda pair: (pd.Series([1.0]), pd.Series([1.0])),
                                    ollama_client=client)
            with patch("hermes.service.analyze_pair_stability", return_value=unstable):
                service._scan(ledger)
            kinds = [event.kind for event in ledger.iter_since("1970-01-01T00:00:00Z")]
        self.assertIn(AuditEventKind.STABILITY_ALERT, kinds)
        self.assertIn(AuditEventKind.DIAGNOSIS_CREATED, kinds)
        client.diagnose.assert_called_once()

    def test_unstable_event_without_ollama_remains_nonblocking(self):
        with AuditLedger(self.path) as ledger:
            ledger.append(self.terminal_event())
            unstable = StabilityReport("AAA / BBB", 200, 1, 1, 0.2, 0.2, 30, 0.6, False, ("HURST",))
            service = HermesService(ledger_path=self.path, price_reader=lambda pair: (pd.Series([1.0]), pd.Series([1.0])))
            with patch("hermes.service.analyze_pair_stability", return_value=unstable):
                service._scan(ledger)
            kinds = [event.kind for event in ledger.iter_since("1970-01-01T00:00:00Z")]
        self.assertIn(AuditEventKind.STABILITY_ALERT, kinds)
        self.assertNotIn(AuditEventKind.DIAGNOSIS_CREATED, kinds)

    def test_hermes_package_does_not_import_execution_modules(self):
        for path in Path("hermes").glob("*.py"):
            source = path.read_text(encoding="utf-8")
            self.assertNotIn("from execution.", source, path.name)
            self.assertNotIn("import execution.", source, path.name)

    def test_compound_checkpoint_processes_each_event_in_the_same_millisecond(self):
        timestamp = "2026-09-09T14:30:00.123Z"
        with AuditLedger(self.path) as ledger:
            ledger.append(AuditEvent("event-a", AuditEventKind.PAIR_ORDER_TERMINAL, timestamp, {"pair": "AAA / BBB"}))
            ledger.append(AuditEvent("event-b", AuditEventKind.PAIR_ORDER_TERMINAL, timestamp, {"pair": "AAA / BBB"}))
            stable = StabilityReport("AAA / BBB", 200, 1, 1, 0.01, 0.01, 5, 0.2, True, ())
            reader = MagicMock(return_value=(pd.Series([1.0]), pd.Series([1.0])))
            service = HermesService(ledger_path=self.path, price_reader=reader)
            with patch("hermes.service.analyze_pair_stability", return_value=stable):
                service._scan(ledger)
        self.assertEqual(reader.call_count, 2)
