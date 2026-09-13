import copy
import logging
import threading
import time

import numpy as np
import pandas as pd
from sqlalchemy import bindparam, text

from config.database import db
from config.settings import settings
from research.pair_screener import ScreenerConfig, screen_pairs, universe_symbols
from strategies.pair_trading import PairTradingConfig, PairTradingStrategy
from .health_score import scorer

logger = logging.getLogger(__name__)
SCREENING_LOOKBACK_BARS = 252


class SignalGenerator:
    """Read-only desk scanner backed by the research daily-price universe."""

    # The worker refreshes the cache roughly every TTL interval; tolerate a
    # few extra cycles of scan/refresh latency before treating it as stale,
    # so a normal cycle boundary never flickers empty. Anything older than
    # that means the worker stopped refreshing (a stuck DB call, a scan that
    # keeps raising, ...), and serving indefinitely old "live" opportunities
    # in that case is worse than serving none.
    _STALE_TOLERANCE_CYCLES = 3

    def __init__(self):
        # Keep the scanner universe identical to the research screener; this
        # deliberately replaces the old disconnected 12-ticker subset.
        self.candidate_pool = universe_symbols()
        self._scan_cache: list | None = None
        self._scan_cache_at = 0.0
        self._scan_cache_lock = threading.Lock()

    @staticmethod
    def _scan_cache_ttl_seconds() -> float:
        try:
            return max(0.0, float(settings.SCAN_CACHE_TTL_SECONDS))
        except (TypeError, ValueError):
            return 60.0

    def get_cached_opportunities(self) -> list:
        """Return the latest worker-warmed scan, or [] if it is missing or stale."""
        with self._scan_cache_lock:
            if self._scan_cache is None:
                return []
            age = time.monotonic() - self._scan_cache_at
            stale_after = max(self._scan_cache_ttl_seconds() * self._STALE_TOLERANCE_CYCLES, 1.0)
            if age > stale_after:
                logger.warning(
                    "Cached opportunities are %.0fs old (worker appears stuck); withholding until it refreshes.",
                    age,
                )
                return []
            return copy.deepcopy(self._scan_cache)

    @staticmethod
    def _finite_positive_rows(frame: pd.DataFrame, id_columns: list[str], value_column: str) -> pd.DataFrame:
        """Coerce ``value_column`` to numeric and drop rows with missing ids or a non-finite/non-positive value."""
        frame = frame.copy()
        frame[value_column] = pd.to_numeric(frame[value_column], errors="coerce")
        frame = frame.dropna(subset=id_columns)
        return frame.loc[np.isfinite(frame[value_column]) & (frame[value_column] > 0)]

    def _build_price_matrix(self) -> pd.DataFrame:
        """Reconcile daily history with the most recent available intraday close."""
        daily_query = text("""
            SELECT date, symbol, close
            FROM stock_prices_daily
            WHERE symbol IN :symbols
            ORDER BY date, symbol
        """).bindparams(bindparam("symbols", expanding=True))
        try:
            with db.engine.connect() as conn:
                bars = pd.read_sql(daily_query, conn, params={"symbols": self.candidate_pool})
        except Exception:
            logger.warning("Could not load daily price history for signal screening", exc_info=True)
            return pd.DataFrame()
        if bars.empty:
            return pd.DataFrame()
        bars["date"] = pd.to_datetime(bars["date"], errors="coerce", utc=True).dt.tz_localize(None).dt.normalize()
        bars = self._finite_positive_rows(bars, ["date", "symbol", "close"], "close")
        if bars.empty:
            return pd.DataFrame()
        matrix = bars.drop_duplicates(subset=["date", "symbol"], keep="last").pivot(
            index="date", columns="symbol", values="close"
        ).sort_index()
        matrix = matrix.reindex(columns=[symbol for symbol in self.candidate_pool if symbol in matrix])

        # The research history remains daily, but the current scan should use a
        # completed intraday bar whenever it is newer than that symbol's final
        # daily close.  The window query returns at most one bar per symbol.
        intraday_query = text("""
            SELECT date, symbol, price
            FROM (
                SELECT date, symbol, price,
                       ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY date DESC) AS row_number
                FROM stock_prices
                WHERE symbol IN :symbols
            ) AS latest_bars
            WHERE row_number = 1
        """).bindparams(bindparam("symbols", expanding=True))
        try:
            with db.engine.connect() as conn:
                intraday_bars = pd.read_sql(intraday_query, conn, params={"symbols": self.candidate_pool})
        except Exception:
            # Intraday data is an enhancement.  A transient database issue must
            # not discard usable daily research history.
            logger.warning("Could not load latest intraday price bars for signal screening", exc_info=True)
            intraday_bars = pd.DataFrame()

        required_intraday_columns = {"date", "symbol", "price"}
        if not intraday_bars.empty and required_intraday_columns.issubset(intraday_bars.columns):
            intraday_bars = intraday_bars.copy()
            intraday_bars["date"] = pd.to_datetime(
                intraday_bars["date"], errors="coerce", utc=True
            ).dt.tz_localize(None)
            intraday_bars = self._finite_positive_rows(intraday_bars, ["date", "symbol", "price"], "price")
            for bar in intraday_bars.drop_duplicates(subset=["symbol"], keep="last").itertuples(index=False):
                symbol, timestamp, price = bar.symbol, bar.date, float(bar.price)
                if symbol not in matrix.columns:
                    continue
                latest_daily = matrix[symbol].dropna()
                # Compare normalized (midnight) dates, not raw datetimes: an
                # intraday tick from earlier the same session (e.g. 15:30) is
                # otherwise ">" the daily row's midnight timestamp and would
                # clobber that day's official close with a stale mid-session
                # snapshot once the real daily bar has landed.
                if latest_daily.empty or timestamp.normalize() > latest_daily.index.max():
                    # Matrix consumers expect one observation per trading day.
                    # Normalizing retains cross-symbol alignment while replacing
                    # a provisional daily close during the active session.
                    matrix.loc[timestamp.normalize(), symbol] = price

        # Preserve gaps between symbols for later inner alignment, while never
        # exposing NaN, infinite, or non-positive observations as valid prices.
        matrix = matrix.apply(pd.to_numeric, errors="coerce")
        matrix = matrix.where(np.isfinite(matrix) & (matrix > 0))
        matrix = matrix.loc[~matrix.index.duplicated(keep="last")].sort_index()
        matrix = matrix.dropna(axis=0, how="all").dropna(axis=1, how="all")
        return matrix.reindex(columns=[symbol for symbol in self.candidate_pool if symbol in matrix])

    @staticmethod
    def _history_from_matrix(matrix: pd.DataFrame) -> dict[str, pd.DataFrame]:
        return {
            symbol: pd.DataFrame({"Close": matrix[symbol].dropna()})
            for symbol in matrix.columns
            if matrix[symbol].notna().any()
        }

    def scan_instant_opportunities(self, force_refresh: bool = False, price_matrix: pd.DataFrame | None = None) -> list:
        """Return a bounded-TTL desk scan, recalculating only when necessary.

        ``price_matrix``, when supplied, is used instead of rebuilding it from
        the database -- the worker cycle already computes it once for
        ``manage_orders_and_positions`` and can hand it here to avoid a second
        identical daily+intraday query and pivot on every 60s cycle.
        """
        now = time.monotonic()
        with self._scan_cache_lock:
            cache_is_fresh = (
                self._scan_cache is not None
                and now - self._scan_cache_at < self._scan_cache_ttl_seconds()
            )
            if cache_is_fresh and not force_refresh:
                return copy.deepcopy(self._scan_cache)

        opportunities = self._scan_instant_opportunities_uncached(price_matrix)
        with self._scan_cache_lock:
            self._scan_cache = copy.deepcopy(opportunities)
            self._scan_cache_at = time.monotonic()
        return copy.deepcopy(opportunities)

    def _scan_instant_opportunities_uncached(self, matrix: pd.DataFrame | None = None) -> list:
        """Return qualified same-sector desk candidates; never place orders."""
        scorer.score_companies()
        matrix = self._build_price_matrix() if matrix is None else matrix
        if matrix.empty:
            logger.warning("Daily price matrix is empty; scan aborted.")
            return []
        history = self._history_from_matrix(matrix)
        if len(history) < 2:
            logger.warning("Fewer than two symbols have daily price history; scan aborted.")
            return []

        screen_config = ScreenerConfig(max_pvalue=0.05, max_half_life=20.0, max_hurst=0.45)
        # A desk scan is current-state monitoring, not the five-year offline
        # walk-forward job.  One trading year satisfies the 200-bar screen and
        # keeps the worker responsive while still evaluating all 60 symbols.
        history = {
            symbol: bars.tail(max(SCREENING_LOOKBACK_BARS, screen_config.min_training_bars))
            for symbol, bars in history.items()
        }
        history_start = min(bars.index.min() for bars in history.values())
        history_end = max(bars.index.max() for bars in history.values()) + pd.Timedelta(days=1)
        screening = screen_pairs(
            history,
            history_start,
            history_end,
            config=screen_config,
        )
        opportunities = []
        logger.info("Screened %d same-sector pairs; %d qualified.", screening.candidates, len(screening.selected))
        for candidate in screening.selected:
            stock_a, stock_b = candidate.pair
            aligned = pd.concat(
                [history[stock_a]["Close"], history[stock_b]["Close"]], axis=1, keys=[stock_a, stock_b], join="inner"
            ).dropna()
            model = PairTradingStrategy(PairTradingConfig(window=len(aligned))).fit(aligned[stock_a], aligned[stock_b])
            if model is None:
                continue
            current_z = model.z_score(float(aligned[stock_a].iloc[-1]), float(aligned[stock_b].iloc[-1]))
            if not np.isfinite(current_z) or abs(current_z) < settings.DASHBOARD_Z_THRESHOLD:
                continue
            score_a, score_b = scorer.get_score(stock_a), scorer.get_score(stock_b)
            if abs(score_a - score_b) > 30:
                continue
            if current_z > 0 and score_b >= score_a - 10:
                action = f"SELL {stock_a} / BUY {stock_b}"
            elif current_z < 0 and score_a >= score_b - 10:
                action = f"BUY {stock_a} / SELL {stock_b}"
            else:
                continue
            opportunities.append({
                "pair": f"{stock_a} / {stock_b}",
                "z_score": round(float(current_z), 3),
                "price_A": round(float(aligned[stock_a].iloc[-1]), 2),
                "price_B": round(float(aligned[stock_b].iloc[-1]), 2),
                "p_value": round(float(candidate.pvalue), 4),
                "action": action,
            })
        return sorted(opportunities, key=lambda item: abs(item["z_score"]), reverse=True)[:settings.DASHBOARD_MAX_CANDIDATES]


signals_hub = SignalGenerator()
