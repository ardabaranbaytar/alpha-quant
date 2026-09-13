"""Best-effort persistent audit trail for simulated order lifecycle transitions."""

import logging
from typing import Iterable

import pandas as pd
from sqlalchemy import text

from config.database import db

logger = logging.getLogger(__name__)


class OrderLedger:
    """Append lifecycle records without allowing audit storage to halt execution."""

    _CREATE_TABLE = text("""
        CREATE TABLE IF NOT EXISTS orders (
            order_id VARCHAR(64) NOT NULL,
            pair VARCHAR(128) NOT NULL,
            timestamp DATETIME NOT NULL,
            leg_symbol VARCHAR(16) NOT NULL,
            side VARCHAR(16) NOT NULL,
            target_shares DOUBLE NOT NULL,
            limit_price DOUBLE NULL,
            status VARCHAR(32) NOT NULL,
            rejection_reason TEXT NULL,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    _INSERT_TRANSITION = text("""
        INSERT INTO orders (
            order_id, pair, timestamp, leg_symbol, side, target_shares,
            limit_price, status, rejection_reason
        ) VALUES (
            :order_id, :pair, :timestamp, :leg_symbol, :side, :target_shares,
            :limit_price, :status, :rejection_reason
        )
    """)

    @staticmethod
    def _database_timestamp(value) -> object:
        timestamp = pd.Timestamp(value)
        timestamp = timestamp.tz_localize("UTC") if timestamp.tzinfo is None else timestamp.tz_convert("UTC")
        return timestamp.tz_localize(None).to_pydatetime()

    def record_transitions(self, transitions: Iterable[dict]) -> None:
        """Atomically append a batch of lifecycle rows when the database is available."""
        rows = [
            {**transition, "timestamp": self._database_timestamp(transition["timestamp"])}
            for transition in transitions
        ]
        if not rows:
            return
        try:
            with db.engine.begin() as connection:
                connection.execute(self._CREATE_TABLE)
                connection.execute(self._INSERT_TRANSITION, rows)
        except Exception:
            # The SQL positions ledger and paper order simulator are canonical.
            # Losing an audit write must never turn a successful paper fill into
            # a failed execution cycle.
            logger.warning("Could not persist order lifecycle audit records", exc_info=True)
