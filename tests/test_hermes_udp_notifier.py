import json
import socket
import unittest
from unittest.mock import MagicMock, patch

from hermes.models import AuditEvent, AuditEventKind
from hermes.udp_notifier import UdpNotifier


def event():
    return AuditEvent("event-1", AuditEventKind.STABILITY_ALERT, "2026-09-09T14:30:00Z", {})


class HermesUdpNotifierTests(unittest.TestCase):
    def test_notification_reaches_loopback_listener(self):
        listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        listener.bind(("127.0.0.1", 0))
        listener.settimeout(1)
        try:
            with UdpNotifier(port=listener.getsockname()[1]) as notifier:
                notifier.notify(event())
                payload, _ = listener.recvfrom(2048)
            self.assertEqual(json.loads(payload), {"schema_version": 1, "event_id": "event-1",
                                                    "kind": "STABILITY_ALERT", "occurred_at": "2026-09-09T14:30:00Z"})
        finally:
            listener.close()

    def test_unbound_port_is_safe(self):
        with UdpNotifier(port=65500) as notifier:
            self.assertIsNone(notifier.notify(event()))

    def test_connection_reset_is_safely_ignored(self):
        fake_socket = MagicMock()
        fake_socket.sendto.side_effect = ConnectionResetError("no listener")
        with patch("hermes.udp_notifier.socket.socket", return_value=fake_socket):
            notifier = UdpNotifier()
            self.assertIsNone(notifier.notify(event()))
            fake_socket.setblocking.assert_called_once_with(False)
            notifier.close()
