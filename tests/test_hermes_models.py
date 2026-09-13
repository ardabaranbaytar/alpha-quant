import json
import unittest
from dataclasses import FrozenInstanceError

from hermes.models import AuditEvent, AuditEventKind, StabilityConfig, StabilityReport


class HermesModelTests(unittest.TestCase):
    def test_models_are_immutable(self):
        config = StabilityConfig()
        with self.assertRaises(FrozenInstanceError):
            config.max_hurst = 0.4

    def test_invalid_nan_and_negative_values_are_rejected(self):
        with self.assertRaises(ValueError):
            StabilityConfig(max_hurst=float("nan"))
        with self.assertRaises(ValueError):
            StabilityConfig(max_half_life=-1)
        with self.assertRaises(ValueError):
            StabilityReport("A / B", 200, float("nan"), None, None, None, None, None, False, ())

    def test_to_dict_is_json_safe_primitives(self):
        report = StabilityReport("AAA / BBB", 200, 1.0, 1.5, 0.01, 0.02, 5.0, 0.3, True, ())
        event = AuditEvent("event-1", AuditEventKind.STABILITY_ALERT, "2026-09-09T14:30:00Z",
                           {"report": report.to_dict(), "flag": True})
        data = event.to_dict()
        self.assertEqual(data["kind"], "STABILITY_ALERT")
        self.assertIsInstance(data["payload"]["report"]["rejection_reasons"], list)
        json.dumps(data)
