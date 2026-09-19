"""One-shot controlled paper-execution integration drill.

This script writes only the clearly labelled TEST_AAA / TEST_BBB pair and removes
those rows in ``finally``.  It is intentionally not part of the web worker.
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
from sqlalchemy import text

from config.database import db
from execution.execution_engine import ExecutionEngine
from execution.order_models import MarketSnapshot, Order, OrderStatus
from execution.position_ledger import insert_open_position_legs
from hermes.audit_ledger import AuditLedger
from hermes.auditor_hook import HermesAuditorHook
from hermes.models import AuditEventKind

TEST_PAIR = "TEST_AAA / TEST_BBB"
TEST_LABEL = "TEST_AAA-TEST_BBB"
GREEN, RED, AMBER, RESET = "\033[92m", "\033[91m", "\033[93m", "\033[0m"


def report(step: int, title: str, passed: bool, detail: str) -> bool:
    color, state = (GREEN, "PASS") if passed else (RED, "FAIL")
    print(f"{color}[{state}]{RESET} {step}/6 {title}: {detail}")
    return passed


def cleanup() -> None:
    """Remove synthetic records even if any verification step fails."""
    with db.engine.begin() as connection:
        connection.execute(text("DELETE FROM positions WHERE strategy_name = :pair"), {"pair": TEST_PAIR})


def wait_for_terminal_event(ledger_path: str, timeout_s: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        with AuditLedger(ledger_path) as ledger:
            events = ledger.iter_since("1970-01-01T00:00:00Z")
        if any(event.kind is AuditEventKind.PAIR_ORDER_TERMINAL and event.payload.get("pair") == TEST_PAIR
               for event in events):
            return True
        time.sleep(0.02)
    return False


class _LedgerWorkspace:
    """Stop the audit thread before Windows removes its temporary WAL files."""

    def __init__(self):
        self._temporary_directory = tempfile.TemporaryDirectory(prefix="alpha_quant_dry_run_")
        self.path = self._temporary_directory.name
        self.hook = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        if self.hook is not None:
            self.hook.stop(timeout=5.0)
        self._temporary_directory.cleanup()


def main() -> int:
    results: list[bool] = []
    hook = None
    try:
        with _LedgerWorkspace() as workspace:
            ledger_path = str(Path(workspace.path) / "hermes_dry_run.db")
            hook = HermesAuditorHook(ledger_path=ledger_path, udp_port=65500)
            workspace.hook = hook
            engine = ExecutionEngine(telemetry=hook, hermes_ledger_path=ledger_path)
            now = pd.Timestamp.now(tz="UTC")
            opportunity = {
                "pair": TEST_PAIR,
                "action": "SHORT_SPREAD",
                "z_score": 2.45,
                "coint_pvalue": 0.01,
                "half_life": 12.0,
                "price_a": 100.0,
                "price_b": 50.0,
                "price_timestamp": now,
            }

            cleanup()
            results.append(report(1, "Synthetic pair isolation", True, f"using {TEST_LABEL}"))

            with db.engine.connect() as connection:
                approved, reason = engine._pre_trade_risk_gate(connection, opportunity)
            results.append(report(2, "Signal & Gatekeeper", approved,
                                  "GATE_APPROVED" if approved else reason))
            if not approved:
                results.append(report(3, "Execution & DB insert", False, "skipped after gate rejection"))
                results.append(report(4, "Hermes terminal event", False, "skipped after gate rejection"))
                results.append(report(5, "Close cycle & PnL", False, "skipped after gate rejection"))
                return 1

            leg_a = Order(TEST_PAIR, "TEST_AAA", "SELL", 10.0, "MARKET", now)
            leg_b = Order(TEST_PAIR, "TEST_BBB", "BUY", 20.0, "MARKET", now)
            market_a = MarketSnapshot(100.0, 1_000_000.0, 5.0)
            market_b = MarketSnapshot(50.0, 1_000_000.0, 5.0)
            fill_result = engine.order_manager.submit_pair_orders(TEST_PAIR, leg_a, leg_b, market_a, market_b)
            if fill_result["status"] is not OrderStatus.FILLED:
                results.append(report(3, "Execution & DB insert", False,
                                      f"OrderManager returned {fill_result['status'].value}"))
                results.append(report(4, "Hermes terminal event", False, "no full fill"))
                results.append(report(5, "Close cycle & PnL", False, "no open position"))
                return 1

            fills = [leg.fill for leg in fill_result["legs"]]
            with db.engine.begin() as connection:
                insert_open_position_legs(connection, TEST_PAIR, zip((leg_a, leg_b), fills))
                stored = connection.execute(text("""
                    SELECT symbol, side, quantity, status FROM positions
                    WHERE strategy_name = :pair ORDER BY symbol ASC
                """), {"pair": TEST_PAIR}).mappings().all()
            inserted = len(stored) == 2 and all(row["status"] == "OPEN" for row in stored)
            results.append(report(3, "Execution & DB insert", inserted,
                                  f"{len(stored)} OPEN paper legs persisted"))

            telemetry_ok = wait_for_terminal_event(ledger_path)
            results.append(report(4, "Telemetry & Hermes ledger", telemetry_ok,
                                  "PAIR_ORDER_TERMINAL observed" if telemetry_ok else "event not observed"))

            open_records = engine._get_open_positions()
            record = open_records.get(TEST_PAIR)
            closed = bool(record) and engine._close_position(record, [99.0, 51.0], "dry-run close")
            with db.engine.connect() as connection:
                closed_rows = connection.execute(text("""
                    SELECT status, unrealized_pnl FROM positions
                    WHERE strategy_name = :pair ORDER BY symbol ASC
                """), {"pair": TEST_PAIR}).mappings().all()
            close_ok = closed and len(closed_rows) == 2 and all(row["status"] == "CLOSED" for row in closed_rows)
            results.append(report(5, "Close cycle & PnL", close_ok,
                                  "two CLOSED legs with recorded PnL" if close_ok else "close verification failed"))
            return 0 if all(results) else 1
    except Exception as error:  # noqa: BLE001
        while len(results) < 5:
            results.append(report(len(results) + 1, "Pipeline verification", False, str(error)))
        return 1
    finally:
        if hook is not None:
            hook.stop(timeout=5.0)
        try:
            cleanup()
            report(6, "Cleanup", True, f"removed synthetic {TEST_LABEL} records")
        except Exception as error:  # noqa: BLE001
            report(6, "Cleanup", False, str(error))


if __name__ == "__main__":
    raise SystemExit(main())
