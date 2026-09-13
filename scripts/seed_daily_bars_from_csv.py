"""Bulk-load research daily CSV snapshots into ``stock_prices_daily``."""

import argparse
from itertools import islice
import logging
from pathlib import Path
import re
import sys

import numpy as np
import pandas as pd
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.database import db

logger = logging.getLogger(__name__)
DEFAULT_CSV_DIRECTORY = ROOT / "research" / "artifacts" / "pair_backtest"
BATCH_SIZE = 5_000
UPSERT_DAILY_BAR = text("""
    INSERT INTO stock_prices_daily
        (symbol, date, open, high, low, close, adj_close, volume)
    VALUES
        (:symbol, :date, :open, :high, :low, :close, :adj_close, :volume)
    ON DUPLICATE KEY UPDATE
        open = VALUES(open), high = VALUES(high), low = VALUES(low),
        close = VALUES(close), adj_close = VALUES(adj_close), volume = VALUES(volume)
""")


def _csv_paths(directory: Path) -> list[Path]:
    paths = sorted(path for path in directory.glob("*.csv") if re.fullmatch(r"[A-Z]{1,6}", path.stem))
    if not paths:
        raise ValueError(f"No ticker CSV files found in {directory}")
    return paths


def iter_csv_rows(directory: Path):
    """Yield validated daily bars, preserving CSV symbols such as V and MA."""
    for path in _csv_paths(directory):
        symbol = path.stem
        frame = pd.read_csv(path, usecols=["Date", "Open", "Close"])
        frame["Date"] = pd.to_datetime(frame["Date"], errors="coerce")
        frame["Open"] = pd.to_numeric(frame["Open"], errors="coerce")
        frame["Close"] = pd.to_numeric(frame["Close"], errors="coerce")
        invalid = frame[["Date", "Open", "Close"]].isna().any(axis=1)
        invalid |= ~np.isfinite(frame[["Open", "Close"]]).all(axis=1)
        invalid |= (frame[["Open", "Close"]] <= 0).any(axis=1)
        if invalid.any() or frame["Date"].duplicated().any():
            raise ValueError(f"Invalid daily bars in {path.name}")
        for row in frame.itertuples(index=False):
            close = float(row.Close)
            yield {
                "symbol": symbol,
                "date": pd.Timestamp(row.Date).date(),
                "open": float(row.Open),
                "high": None,
                "low": None,
                "close": close,
                "adj_close": close,
                "volume": None,
            }


def _batches(rows, batch_size: int):
    iterator = iter(rows)
    while batch := list(islice(iterator, batch_size)):
        yield batch


def seed_daily_bars(directory: Path = DEFAULT_CSV_DIRECTORY, batch_size: int = BATCH_SIZE) -> int:
    if type(batch_size) is not int or batch_size < 1:
        raise ValueError("batch_size must be a positive integer")
    inserted = 0
    with db.engine.begin() as connection:
        for batch in _batches(iter_csv_rows(Path(directory)), batch_size):
            connection.execute(UPSERT_DAILY_BAR, batch)
            inserted += len(batch)
    logger.info("Upserted %d daily bars from %s", inserted, directory)
    return inserted


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv-dir", type=Path, default=DEFAULT_CSV_DIRECTORY)
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    args = parser.parse_args(argv)
    print(f"Upserted {seed_daily_bars(args.csv_dir, args.batch_size)} daily bars into stock_prices_daily")


if __name__ == "__main__":
    main()
