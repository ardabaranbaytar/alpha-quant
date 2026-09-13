"""Local SQLite WAL audit ledger for Hermes events; independent of SQLAlchemy."""

import json
import os
import sqlite3
from datetime import datetime, timezone

from hermes.models import AuditEvent, AuditEventKind


DEFAULT_LEDGER_PATH = os.environ.get(
    "HERMES_LEDGER_PATH",
    os.path.expandvars(r"%LOCALAPPDATA%\AlphaQuantBot\hermes_audit.db" if os.name == "nt"
                       else "/tmp/alpha_quant_hermes/hermes_audit.db"),
)


def _epoch_ms(timestamp_iso: str) -> int:
    timestamp = datetime.fromisoformat(timestamp_iso.replace("Z", "+00:00"))
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return int(timestamp.timestamp() * 1000)


class AuditLedger:
    def __init__(self, db_path: str = DEFAULT_LEDGER_PATH):
        self.db_path = os.fspath(db_path)
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        self._connection = sqlite3.connect(self.db_path)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL;")
        self._connection.execute("PRAGMA busy_timeout=5000;")
        self._connection.execute("PRAGMA synchronous=NORMAL;")
        self._connection.execute("""
            CREATE TABLE IF NOT EXISTS audit_events (
                event_id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                epoch_ms INTEGER NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self._connection.execute("CREATE INDEX IF NOT EXISTS idx_audit_events_epoch_ms ON audit_events(epoch_ms);")
        self._connection.commit()

    def append(self, event: AuditEvent) -> bool:
        cursor = self._connection.execute("""
            INSERT OR IGNORE INTO audit_events (event_id, kind, occurred_at, epoch_ms, payload_json)
            VALUES (?, ?, ?, ?, ?)
        """, (event.event_id, event.kind.value, event.occurred_at, _epoch_ms(event.occurred_at),
               json.dumps(event.payload, sort_keys=True, separators=(",", ":"), allow_nan=False)))
        self._connection.commit()
        return cursor.rowcount == 1

    def get(self, event_id: str) -> AuditEvent | None:
        row = self._connection.execute("""
            SELECT event_id, kind, occurred_at, payload_json FROM audit_events WHERE event_id = ?
        """, (event_id,)).fetchone()
        return self._event_from_row(row) if row is not None else None

    def iter_since(self, timestamp_iso: str) -> list[AuditEvent]:
        rows = self._connection.execute("""
            SELECT event_id, kind, occurred_at, payload_json FROM audit_events
            WHERE epoch_ms >= ? ORDER BY epoch_ms ASC, event_id ASC
        """, (_epoch_ms(timestamp_iso),)).fetchall()
        return [self._event_from_row(row) for row in rows]

    def iter_after(self, epoch_ms: int, event_id: str = "") -> list[AuditEvent]:
        """Return a stable page after an ``(epoch_ms, event_id)`` cursor."""
        rows = self._connection.execute("""
            SELECT event_id, kind, occurred_at, payload_json FROM audit_events
            WHERE epoch_ms > ? OR (epoch_ms = ? AND event_id > ?)
            ORDER BY epoch_ms ASC, event_id ASC
        """, (int(epoch_ms), int(epoch_ms), str(event_id))).fetchall()
        return [self._event_from_row(row) for row in rows]

    @staticmethod
    def _event_from_row(row: sqlite3.Row) -> AuditEvent:
        return AuditEvent(row["event_id"], AuditEventKind(row["kind"]), row["occurred_at"], json.loads(row["payload_json"]))

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()
