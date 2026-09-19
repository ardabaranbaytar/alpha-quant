"""Nonblocking bridge from execution telemetry into the Hermes audit ledger."""

import hashlib
import json
import logging
import queue
import threading
from datetime import datetime, timezone
from enum import Enum
from typing import Protocol

from hermes.audit_ledger import DEFAULT_LEDGER_PATH, AuditLedger
from hermes.models import AuditEvent, AuditEventKind
from hermes.udp_notifier import UdpNotifier

logger = logging.getLogger(__name__)


class TelemetryHook(Protocol):
    """Structural boundary used by the execution layer without a reverse import."""

    def emit(self, event: dict) -> None:
        ...


class HermesAuditorHook(TelemetryHook):
    def __init__(self, ledger_path=None, udp_host="127.0.0.1", udp_port=9876, queue_maxsize=1000):
        self.ledger_path = ledger_path or DEFAULT_LEDGER_PATH
        self._queue = queue.Queue(maxsize=queue_maxsize)
        self._dropped_count = 0
        self._dropped_lock = threading.Lock()
        self._conversion_error_count = 0
        self._write_error_count = 0
        self._stop_event = threading.Event()
        self._notifier = UdpNotifier(udp_host, udp_port)
        self._thread = threading.Thread(target=self._run, name="hermes-auditor", daemon=True)
        self._thread.start()

    @property
    def dropped_count(self) -> int:
        with self._dropped_lock:
            return self._dropped_count

    @property
    def conversion_error_count(self) -> int:
        with self._dropped_lock:
            return self._conversion_error_count

    @property
    def write_error_count(self) -> int:
        with self._dropped_lock:
            return self._write_error_count

    def _record_error(self, kind: str, exc: Exception) -> None:
        with self._dropped_lock:
            if kind == "conversion":
                self._conversion_error_count += 1
            else:
                self._write_error_count += 1
        logger.warning("Hermes audit %s failed: %s", kind, exc, exc_info=exc)

    def emit(self, event: dict) -> None:
        try:
            self._queue.put_nowait(event)
        except queue.Full:
            with self._dropped_lock:
                self._dropped_count += 1

    @staticmethod
    def _json_safe(value):
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, dict):
            return {str(key): HermesAuditorHook._json_safe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [HermesAuditorHook._json_safe(item) for item in value]
        return value

    @staticmethod
    def _kind(event: dict) -> AuditEventKind:
        event_type = str(event.get("type", "")).lower()
        if "diagnosis" in event_type:
            return AuditEventKind.DIAGNOSIS_CREATED
        if "stability" in event_type:
            return AuditEventKind.STABILITY_ALERT
        if "de_risk" in event_type or "derisk" in event_type:
            return AuditEventKind.DE_RISK_TRIGGERED
        return AuditEventKind.PAIR_ORDER_TERMINAL

    def _to_audit_event(self, event: dict) -> AuditEvent:
        payload = self._json_safe(event)
        kind = self._kind(payload)
        occurred_at = payload.get("occurred_at") or payload.get("time")
        if not isinstance(occurred_at, str):
            occurred_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        supplied_id = payload.get("event_id")
        if isinstance(supplied_id, str) and supplied_id:
            # Execution has already content-addressed terminal order events.
            # Retain that identity even though the hook timestamps queue delivery.
            event_id = supplied_id
        else:
            canonical = json.dumps({"pair": payload.get("pair"), "kind": kind.value,
                                    "occurred_at": occurred_at, "payload": payload},
                                   sort_keys=True, separators=(",", ":"), allow_nan=False)
            event_id = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return AuditEvent(event_id, kind, occurred_at, payload)

    def _run(self) -> None:
        try:
            with AuditLedger(self.ledger_path) as ledger:
                while not self._stop_event.is_set() or not self._queue.empty():
                    try:
                        event = self._queue.get(timeout=0.05)
                    except queue.Empty:
                        continue
                    try:
                        audit_event = self._to_audit_event(event)
                    except Exception as exc:  # noqa: BLE001
                        self._record_error("conversion", exc)
                        continue
                    try:
                        appended = ledger.append(audit_event)
                    except Exception as exc:  # noqa: BLE001
                        self._record_error("write", exc)
                        continue
                    if appended:
                        try:
                            self._notifier.notify(audit_event)
                        except Exception:
                            logger.debug("Hermes notifier failed", exc_info=True)
        finally:
            self._notifier.close()

    def stop(self, timeout=5.0) -> None:
        self._stop_event.set()
        self._thread.join(timeout)
        if not self._thread.is_alive():
            self._notifier.close()
