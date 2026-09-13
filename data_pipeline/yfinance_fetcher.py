import logging
from pathlib import Path
import re
import time
from io import StringIO

import pandas as pd
import requests
import yfinance as yf
from sqlalchemy import text

from data_pipeline.data_sanitizer import SanitizerConfig, sanitize_daily_bars

logger = logging.getLogger(__name__)

FALLBACK_SYMBOLS = ["AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "GOOGL", "META", "AMD", "NFLX", "INTC", "QCOM", "AVGO"]

_HOURLY_INSERT = text("""
    INSERT IGNORE INTO stock_prices (date, symbol, price, volume)
    VALUES (:date, :symbol, :price, :volume)
""")

_DAILY_INSERT = text("""
    INSERT INTO stock_prices_daily (symbol, date, open, high, low, close, adj_close, volume)
    VALUES (:symbol, :date, :open, :high, :low, :close, :adj_close, :volume)
    ON DUPLICATE KEY UPDATE
        open = VALUES(open), high = VALUES(high), low = VALUES(low),
        close = VALUES(close), adj_close = VALUES(adj_close), volume = VALUES(volume)
""")


class DataFetcher:
    # Research downloads isolate failures per symbol; keep a stalled provider
    # request from serially blocking the rest of the screening universe.
    DAILY_HISTORY_TIMEOUT_SECONDS = 5

    def __init__(self):
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        }

    def download_daily_history(
        self, symbols: list[str], start: str, end: str, cache_dir: Path, sanitizer: SanitizerConfig | None = None
    ) -> dict[str, pd.DataFrame]:
        """Download adjusted daily OHLC without loading settings or using the bot DB.

        Start is inclusive; end is exclusive. Missing, invalid, zero-volume, and
        single-day bad-tick sessions are dropped outright, never filled.
        The caller supplies a local cache location for yfinance's own HTTP metadata.
        """
        if not symbols or any(not re.fullmatch(r"[A-Z]{1,6}", s) for s in symbols):
            raise ValueError("Expected US equity ticker symbols")
        if pd.Timestamp(start) >= pd.Timestamp(end):
            raise ValueError("History start must precede end")
        cache_dir.mkdir(parents=True, exist_ok=True)
        yf.set_tz_cache_location(str(cache_dir))
        history = {}
        for symbol in dict.fromkeys(symbols):
            raw = yf.download(
                symbol, start=start, end=end, interval="1d", auto_adjust=True,
                actions=False, progress=False, threads=False,
                multi_level_index=False, timeout=self.DAILY_HISTORY_TIMEOUT_SECONDS,
            )
            if raw is None or raw.empty:
                raise RuntimeError(f"No Yahoo Finance history returned for {symbol}")
            if isinstance(raw.columns, pd.MultiIndex):
                raw = raw.xs(symbol, axis=1, level="Ticker")
            columns = ["Open", "Close", "Volume"] if "Volume" in raw.columns else ["Open", "Close"]
            bars = raw.loc[:, columns].apply(pd.to_numeric, errors="raise")
            bars.index = pd.DatetimeIndex(bars.index).tz_localize(None).normalize()
            bars = bars.loc[(bars.index >= pd.Timestamp(start)) & (bars.index < pd.Timestamp(end))]
            bars = bars.dropna(subset=["Open", "Close"]).sort_index()
            if bars.index.has_duplicates:
                raise ValueError(f"Duplicate session dates returned for {symbol}")
            bars, dropped = sanitize_daily_bars(bars, sanitizer)
            if any(dropped.values()):
                logger.warning("Sanitized %s: dropped %s", symbol, dropped)
            bars = bars.loc[:, ["Open", "Close"]]
            if bars.empty or bars.index.has_duplicates or not ((bars > 0) & (bars < float("inf"))).all().all():
                raise ValueError(f"Invalid daily history for {symbol}")
            history[symbol] = bars
        return history

    # ---------------------------------------------------------
    # SYMBOL UNIVERSE
    # ---------------------------------------------------------

    def update_nasdaq100_list(self) -> list[str]:
        """Fetch the current NASDAQ-100 constituent list from Wikipedia.

        Falls back to a predefined list if scraping fails.
        """
        logger.info("Fetching NASDAQ-100 list from Wikipedia.")
        try:
            response = requests.get(
                "https://en.wikipedia.org/wiki/Nasdaq-100", headers=self.headers, timeout=15
            )
            response.raise_for_status()
            for table in pd.read_html(StringIO(response.text)):
                for column in ("Ticker", "Symbol"):
                    if column in table.columns:
                        return [str(symbol).replace(".", "-") for symbol in table[column].tolist()]
        except Exception as exc:
            logger.warning("Could not scrape NASDAQ-100 list. Using fallback list: %s", exc)
        return list(FALLBACK_SYMBOLS)

    # ---------------------------------------------------------
    # BAR INGESTION
    # ---------------------------------------------------------

    @staticmethod
    def _hourly_params(symbol: str, timestamp, row) -> dict | None:
        price = row.get("Close")
        if pd.isna(price):
            return None
        volume = row.get("Volume")
        return {
            "date": timestamp.to_pydatetime(),
            "symbol": symbol,
            "price": float(price),
            "volume": None if pd.isna(volume) else int(volume),
        }

    @staticmethod
    def _daily_params(symbol: str, timestamp, row) -> dict | None:
        close = row.get("Close")
        if pd.isna(close):
            return None

        def optional(column: str):
            value = row.get(column)
            return None if pd.isna(value) else float(value)

        volume = row.get("Volume")
        return {
            "symbol": symbol,
            "date": timestamp.date(),
            "open": optional("Open"),
            "high": optional("High"),
            "low": optional("Low"),
            "close": float(close),
            "adj_close": optional("Adj Close"),
            "volume": None if pd.isna(volume) else int(volume),
        }

    def _ingest_bars(self, symbols, period, interval, insert_query, build_params, unit_label) -> None:
        """Shared per-symbol download/insert loop for hourly and daily bar ingestion.

        The network fetch (plus the rate-limit sleep) happens outside any
        transaction; a pooled DB connection is only checked out afterward,
        briefly, to write that symbol's rows. Holding one transaction open
        across ~60 symbols' worth of sleeps and yfinance HTTP calls would tie
        up a connection for minutes and delay every row's durability until
        the whole run finished.
        """
        from config.database import db

        if symbols is None:
            symbols = self.update_nasdaq100_list()
        logger.info("Starting %s bar injection for %d symbols.", unit_label, len(symbols))

        for idx, symbol in enumerate(symbols, start=1):
            try:
                time.sleep(0.3)
                data = yf.download(symbol, period=period, interval=interval, progress=False, auto_adjust=False)
                if data.empty:
                    logger.warning("%s returned no %s data.", symbol, unit_label)
                    continue
                if isinstance(data.columns, pd.MultiIndex):
                    data.columns = data.columns.get_level_values(0)
                if data.index.tz is not None:
                    data.index = data.index.tz_localize(None)

                rows = [row for row in (build_params(symbol, timestamp, series)
                                        for timestamp, series in data.iterrows()) if row is not None]
                added = 0
                if rows:
                    with db.engine.begin() as connection:
                        for params in rows:
                            if connection.execute(insert_query, params).rowcount > 0:
                                added += 1
                logger.info("[%d/%d] %s: %d %s bars.", idx, len(symbols), symbol, added, unit_label)
            except Exception as exc:
                logger.error("%s download failed for %s: %s", unit_label.capitalize(), symbol, exc)

        logger.info("%s bar injection completed.", unit_label.capitalize())

    def inject_hourly_bars(self, period: str = "60d", symbols: list[str] | None = None) -> None:
        """Download hourly market data and insert it into stock_prices.

        Intended mainly for live/recent signal scanning.
        """
        self._ingest_bars(symbols, period, "1h", _HOURLY_INSERT, self._hourly_params, "hourly")

    def inject_daily_bars(self, period: str = "10y", symbols: list[str] | None = None) -> None:
        """Download long-term daily OHLCV data and store it in stock_prices_daily.

        Daily data is intended primarily for research and long-horizon backtesting.
        """
        self._ingest_bars(symbols, period, "1d", _DAILY_INSERT, self._daily_params, "daily")


fetcher = DataFetcher()
