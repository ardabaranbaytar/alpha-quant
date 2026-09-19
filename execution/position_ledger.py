"""Shared SQL for the one-row-per-leg simulated ``positions`` ledger.

The engine and the manual dry-run scripts (scripts/dry_run_pipeline.py,
scripts/dry_run_nvda_amd.py) all open a filled pair order and then need to
persist the same two OPEN rows the same way; this keeps that insert in one
place instead of three.
"""
from collections.abc import Iterable

from sqlalchemy import text

_INSERT_OPEN_LEGS = text("""
    INSERT INTO positions
        (strategy_name, symbol, side, quantity, entry_price, current_price,
         stop_price, take_profit_price, unrealized_pnl, status, opened_at)
    VALUES
        (:strategy_name, :symbol, :side, :quantity, :entry_price, :current_price,
         NULL, NULL, 0, 'OPEN', CURRENT_TIMESTAMP)
""")


def insert_open_position_legs(connection, pair: str, legs: Iterable[tuple]) -> None:
    """Insert one OPEN row per filled leg inside the caller's transaction.

    ``legs`` is an iterable of ``(order, fill)`` pairs, where ``order`` exposes
    ``leg_symbol``/``side`` and ``fill`` exposes ``filled_qty``/``filled_price``
    (see ``execution.order_models.Order``/``Fill``).
    """
    connection.execute(_INSERT_OPEN_LEGS, [
        {
            "strategy_name": pair,
            "symbol": order.leg_symbol,
            "side": order.side,
            "quantity": fill.filled_qty,
            "entry_price": fill.filled_price,
            "current_price": fill.filled_price,
        }
        for order, fill in legs
    ])
