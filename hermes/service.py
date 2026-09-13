"""Testable Hermes UDP-triggered shadow stability service."""

import hashlib
import json
import logging
import socket
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from hermes.audit_ledger import DEFAULT_LEDGER_PATH, AuditLedger, _epoch_ms
from hermes.models import AuditEvent, AuditEventKind, StabilityConfig
from hermes.stability_monitor import analyze_pair_stability


logger = logging.getLogger(__name__)


class HermesService:
    def __init__(self, udp_port=9876, poll_interval_s=300, ledger_path=None, price_reader=None,
                 stability_config=StabilityConfig(), ollama_client=None, portfolio_reader=None):
        self.udp_port = int(udp_port)
        self.poll_interval_s = float(poll_interval_s)
        self.ledger_path = ledger_path or DEFAULT_LEDGER_PATH
        self.price_reader = price_reader
        self.stability_config = stability_config
        self.ollama_client = ollama_client
        # The web layer injects this read-only adapter; Hermes never imports the
        # execution or database layers to build a desk briefing.
        self.portfolio_reader = portfolio_reader
        self._stop_event = threading.Event()
        self._scan_event = threading.Event()
        self._listener_socket = None
        self._listener_thread = None
        self._worker_thread = None
        # This monotonically advancing cursor avoids rescanning the whole ledger
        # and does not retain an unbounded in-memory event-id set.  The event id
        # is the tie-breaker for events recorded in the same millisecond.
        self._last_epoch_ms = 0
        self._last_event_id = ""

    @staticmethod
    def _fallback_briefing(rejection_count: int, alert_count: int) -> str:
        return f"Ollama çevrimdışı - Salt Veri Özeti: {rejection_count} ret, {alert_count} alarm"

    def generate_desk_briefing(self, lookback_hours: int = 24) -> dict:
        """Return an LLM-enriched, failure-isolated operator risk briefing.

        Only bounded ledger aggregates and an injected read-only portfolio
        summary are included in the prompt.  A missing ledger/model is a normal
        operating state and returns a deterministic Turkish fallback.
        """
        if isinstance(lookback_hours, bool) or not isinstance(lookback_hours, (int, float)) or lookback_hours <= 0:
            raise ValueError("lookback_hours must be positive")
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=float(lookback_hours))
        events = []
        try:
            if Path(self.ledger_path).exists():
                with AuditLedger(self.ledger_path) as ledger:
                    events = ledger.iter_since(cutoff.isoformat().replace("+00:00", "Z"))
        except Exception:
            events = []

        rejection_reasons = {}
        alerts = []
        for event in events:
            payload = event.payload if isinstance(event.payload, dict) else {}
            if str(payload.get("type", "")).lower() == "gatekeeper_rejection":
                reason = str(payload.get("reason") or "Unspecified gate")
                rejection_reasons[reason] = rejection_reasons.get(reason, 0) + 1
            if event.kind is AuditEventKind.STABILITY_ALERT and payload.get("active", True):
                report = payload.get("report") if isinstance(payload.get("report"), dict) else {}
                alerts.append({
                    "pair": str(payload.get("pair") or "UNKNOWN"),
                    "reasons": [str(reason) for reason in report.get("rejection_reasons", [])],
                })

        portfolio = {"open_leg_count": 0, "closed_leg_count": 0, "exposure_map": []}
        try:
            if self.portfolio_reader is not None:
                candidate = self.portfolio_reader()
                if isinstance(candidate, dict):
                    portfolio.update(candidate)
        except Exception:
            # A desk briefing must remain available even when the live SQL
            # reader is temporarily unavailable.
            portfolio["data_available"] = False

        metrics_summary = {
            "lookback_hours": float(lookback_hours),
            "gatekeeper_rejections": sum(rejection_reasons.values()),
            "rejections_by_reason": dict(sorted(rejection_reasons.items())),
            "active_stability_alerts": len(alerts),
            "alert_pairs": alerts,
            "portfolio": portfolio,
        }
        briefing = None
        try:
            if self.ollama_client is not None:
                briefing = self.ollama_client.generate_desk_briefing(summary=metrics_summary)
            else:
                logger.warning(
                    "Desk briefing skipped because client unavailable: "
                    "Ollama client is disabled or not configured"
                )
        except Exception as exc:
            logger.warning("Ollama desk briefing error: %s - %s", type(exc).__name__, str(exc))
            briefing = None
        if not briefing:
            briefing = self._fallback_briefing(
                metrics_summary["gatekeeper_rejections"], metrics_summary["active_stability_alerts"]
            )
        return {
            "briefing": briefing,
            "timestamp": now.isoformat().replace("+00:00", "Z"),
            "metrics_summary": metrics_summary,
        }

    def start(self) -> None:
        if self._worker_thread is not None and self._worker_thread.is_alive():
            return
        self._stop_event.clear()
        self._listener_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._listener_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener_socket.bind(("127.0.0.1", self.udp_port))
        self._listener_socket.settimeout(0.05)
        self.udp_port = self._listener_socket.getsockname()[1]
        self._listener_thread = threading.Thread(target=self._listen, name="hermes-udp", daemon=True)
        self._worker_thread = threading.Thread(target=self._work, name="hermes-service", daemon=True)
        self._listener_thread.start()
        self._worker_thread.start()

    def _listen(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._listener_socket.recvfrom(2048)
                self._scan_event.set()
            except socket.timeout:
                continue
            except Exception:
                if not self._stop_event.is_set():
                    continue

    def _work(self) -> None:
        try:
            with AuditLedger(self.ledger_path) as ledger:
                while not self._stop_event.is_set():
                    self._scan_event.wait(self.poll_interval_s)
                    self._scan_event.clear()
                    try:
                        self._scan(ledger)
                    except Exception:
                        pass
        except Exception:
            pass

    def _scan(self, ledger: AuditLedger) -> None:
        events = ledger.iter_after(self._last_epoch_ms, self._last_event_id)
        cursor = (self._last_epoch_ms, self._last_event_id)
        for event in events:
            try:
                event_epoch_ms = _epoch_ms(event.occurred_at)
            except (TypeError, ValueError):
                continue
            cursor = max(cursor, (event_epoch_ms, event.event_id))
            if event.kind is not AuditEventKind.PAIR_ORDER_TERMINAL:
                continue
            pair = event.payload.get("pair")
            if not isinstance(pair, str) or self.price_reader is None:
                continue
            try:
                prices_a, prices_b = self.price_reader(pair)
                report = analyze_pair_stability(pair, prices_a, prices_b, self.stability_config)
                if report.stable:
                    continue
                payload = {"source_event_id": event.event_id, "pair": pair, "report": report.to_dict()}
                digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
                ledger.append(AuditEvent(digest, AuditEventKind.STABILITY_ALERT,
                                         datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), payload))
                if self.ollama_client is not None:
                    diagnosis = self.ollama_client.diagnose(
                        pair=pair,
                        coint_pvalue=report.coint_pvalue,
                        adf_pvalue=report.adf_pvalue,
                        ou_half_life=report.ou_half_life,
                        hurst=report.hurst,
                        rejection_reasons=report.rejection_reasons,
                        recent_event_kind=event.kind.value,
                    )
                    if diagnosis:
                        diagnosis_payload = {
                            "source_event_id": event.event_id,
                            "pair": pair,
                            "diagnosis": diagnosis,
                        }
                        diagnosis_id = hashlib.sha256(
                            json.dumps(diagnosis_payload, sort_keys=True).encode("utf-8")
                        ).hexdigest()
                        ledger.append(AuditEvent(
                            diagnosis_id,
                            AuditEventKind.DIAGNOSIS_CREATED,
                            datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                            diagnosis_payload,
                        ))
            except Exception:
                continue
        self._last_epoch_ms, self._last_event_id = cursor

    def stop(self, timeout=5.0) -> None:
        self._stop_event.set()
        self._scan_event.set()
        if self._listener_socket is not None:
            try:
                self._listener_socket.close()
            except Exception:
                pass
            self._listener_socket = None
        for thread in (self._listener_thread, self._worker_thread):
            if thread is not None:
                thread.join(timeout)
