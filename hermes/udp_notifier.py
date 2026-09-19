"""Best-effort nonblocking loopback UDP wake-up notifications."""

import json
import logging
import socket

from hermes.models import AuditEvent

logger = logging.getLogger(__name__)


class UdpNotifier:
    def __init__(self, host="127.0.0.1", port=9876, schema_version=1):
        if host != "127.0.0.1":
            raise ValueError("Hermes UDP notifications are restricted to raw loopback 127.0.0.1")
        self.host, self.port, self.schema_version = host, int(port), int(schema_version)
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket.setblocking(False)

    def notify(self, event: AuditEvent) -> None:
        payload = json.dumps({"schema_version": self.schema_version, "event_id": event.event_id,
                              "kind": event.kind.value, "occurred_at": event.occurred_at},
                             separators=(",", ":")).encode("utf-8")
        try:
            self._socket.sendto(payload, ("127.0.0.1", self.port))
        except Exception:
            # UDP is an optional wake-up signal; Windows loopback may reset an
            # unconnected socket when there is no listener.
            logger.debug("Hermes UDP notify failed", exc_info=True)

    def close(self) -> None:
        if self._socket is not None:
            try:
                self._socket.close()
            except Exception:
                logger.debug("Hermes UDP socket close failed", exc_info=True)
            self._socket = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()
