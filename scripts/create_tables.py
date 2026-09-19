"""Create the MySQL tables the pipeline expects. Idempotent; never drops anything.

`orders` is intentionally absent: OrderLedger creates its own table on first use.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text

from config.database import db

DDL = {
    # Intraday (hourly) bars: `date` is a DATETIME because several bars share a day.
    "stock_prices": """
        CREATE TABLE IF NOT EXISTS stock_prices (
            symbol VARCHAR(16) NOT NULL,
            date DATETIME NOT NULL,
            price DECIMAL(12, 4),
            volume BIGINT,
            PRIMARY KEY (symbol, date)
        )
    """,
    # One row per filled leg; a pair is two rows sharing a strategy_name.
    "positions": """
        CREATE TABLE IF NOT EXISTS positions (
            id INT AUTO_INCREMENT PRIMARY KEY,
            strategy_name VARCHAR(128) NOT NULL,
            symbol VARCHAR(16) NOT NULL,
            side VARCHAR(8) NOT NULL,
            quantity DECIMAL(16, 4) NOT NULL,
            entry_price DECIMAL(12, 4),
            current_price DECIMAL(12, 4),
            stop_price DECIMAL(12, 4) NULL,
            take_profit_price DECIMAL(12, 4) NULL,
            unrealized_pnl DECIMAL(14, 4) DEFAULT 0,
            status VARCHAR(16) NOT NULL DEFAULT 'OPEN',
            opened_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            closed_at DATETIME NULL,
            INDEX idx_positions_strategy (strategy_name),
            INDEX idx_positions_status (status)
        )
    """,
    "stock_prices_daily": """
        CREATE TABLE IF NOT EXISTS stock_prices_daily (
            symbol VARCHAR(16) NOT NULL,
            date DATE NOT NULL,
            open DECIMAL(12, 4),
            high DECIMAL(12, 4),
            low DECIMAL(12, 4),
            close DECIMAL(12, 4),
            adj_close DECIMAL(12, 4),
            volume BIGINT,
            PRIMARY KEY (symbol, date)
        )
    """,
}


def main() -> int:
    with db.engine.begin() as connection:
        for name, statement in DDL.items():
            connection.execute(text(statement))
            print(f"[OK] {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
