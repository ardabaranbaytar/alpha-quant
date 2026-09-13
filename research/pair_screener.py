"""Rank same-sector pairs on an explicitly bounded historical training interval."""

from collections import Counter
from dataclasses import asdict, dataclass
from itertools import combinations
import logging
from pathlib import Path

import pandas as pd

from data_pipeline.yfinance_fetcher import DataFetcher
from strategies.kalman_pair import fit_ou
from strategies.mean_reversion import estimate_hurst
from strategies.pair_trading import PairTradingConfig, PairTradingStrategy

logger = logging.getLogger(__name__)


# The requested nine groups contain 56 names; four utilities complete the 60-name universe.
SECTOR_UNIVERSE = {
    "Financials": ("JPM", "BAC", "WFC", "C", "MS", "GS", "BLK", "BK"),
    "Energy": ("XOM", "CVX", "COP", "SLB", "EOG", "OXY"),
    "Consumer staples": ("KO", "PEP", "PG", "CL", "KMB", "MDLZ"),
    "Healthcare": ("JNJ", "PFE", "MRK", "ABBV", "BMY", "LLY", "AMGN"),
    "Industrials / defense": ("CAT", "DE", "HON", "MMM", "GE", "LMT", "RTX", "NOC"),
    "Technology / semiconductors": ("GOOGL", "META", "MSFT", "AAPL", "NVDA", "AMD", "QCOM", "AVGO"),
    "Insurance / specialized finance": ("CB", "PGR", "TRV", "ALL", "MET"),
    "Communication / media": ("CMCSA", "DIS", "NFLX", "T"),
    "Real estate / infrastructure": ("PLD", "AMT", "EQIX", "CCI"),
    "Utilities": ("NEE", "DUK", "SO", "AEP"),
}

# Same security/CUSIP, renamed on 2026-05-21. Keep the user's stable research ID.
YAHOO_SYMBOL_ALIASES = {"BK": "BNY"}


def universe_symbols(sectors=None) -> list[str]:
    sectors = SECTOR_UNIVERSE if sectors is None else sectors
    symbols = [symbol for group in sectors.values() for symbol in group]
    if len(symbols) != len(set(symbols)):
        raise ValueError("Each symbol must belong to exactly one screening group")
    return symbols


def symbol_sectors(sectors=None) -> dict[str, str]:
    """Map each universe symbol to its sector label, for portfolio-level exposure caps."""
    sectors = SECTOR_UNIVERSE if sectors is None else sectors
    universe_symbols(sectors)
    return {symbol: sector for sector, symbols in sectors.items() for symbol in symbols}


def download_universe(start: str, end: str, cache_dir: Path) -> dict[str, pd.DataFrame]:
    return download_research_history(universe_symbols(), start, end, cache_dir)


def download_research_history(symbols, start, end, cache_dir):
    mapping = {symbol: YAHOO_SYMBOL_ALIASES.get(symbol, symbol) for symbol in symbols}
    if len(set(mapping.values())) != len(mapping):
        raise ValueError("The same security cannot appear under both its old and new symbol")
    downloaded = {}
    fetcher = DataFetcher()
    for symbol, provider_symbol in mapping.items():
        try:
            provider_history = fetcher.download_daily_history([provider_symbol], start, end, cache_dir)
            downloaded[symbol] = provider_history[provider_symbol]
        except (RuntimeError, ValueError, KeyError) as exc:
            # Research screening can evaluate the unaffected same-sector pairs;
            # one provider outage must not abort the full historical run.
            logger.warning("Skipping %s because Yahoo history is unavailable: %s", symbol, exc)
    return downloaded


@dataclass(frozen=True)
class ScreenerConfig:
    max_pairs: int = 10
    min_training_bars: int = 200
    max_pvalue: float = 0.05
    max_half_life: float = 20.0
    max_hurst: float = 0.45

    def __post_init__(self):
        if type(self.max_pairs) is not int or not 1 <= self.max_pairs <= 10:
            raise ValueError("Select at most ten pairs")
        if type(self.min_training_bars) is not int or self.min_training_bars < 100:
            raise ValueError("Training requires at least 100 observations")
        if not 0 < self.max_pvalue < 1 or not 0 < self.max_hurst < 1 or not 0 < self.max_half_life < float("inf"):
            raise ValueError("Invalid screening thresholds")


@dataclass(frozen=True)
class ScreenedPair:
    sector: str
    pair: tuple[str, str]
    pvalue: float
    half_life: float
    hurst: float
    training_bars: int


@dataclass(frozen=True)
class ScreeningResult:
    selected: tuple[ScreenedPair, ...]
    candidates: int
    eligible: int
    rejected: dict[str, int]
    training_start: str
    training_end_exclusive: str

    def to_report(self):
        return asdict(self)


@dataclass(frozen=True)
class WalkForwardRebalance:
    effective_session: str
    training_last_session: str
    screening: ScreeningResult


@dataclass(frozen=True)
class WalkForwardResult:
    rebalances: tuple[WalkForwardRebalance, ...]
    training_sessions: int
    rebalance_sessions: int

    @property
    def pairs(self) -> tuple[tuple[str, str], ...]:
        return tuple(sorted({candidate.pair for period in self.rebalances for candidate in period.screening.selected}))

    def entry_schedule(self, pair) -> pd.Series:
        """Membership effective at each rebalance open, including empty selections."""
        return pd.Series(
            [any(candidate.pair == tuple(pair) for candidate in period.screening.selected) for period in self.rebalances],
            index=pd.DatetimeIndex([period.effective_session for period in self.rebalances]), dtype=bool,
        )

    def to_report(self):
        return {
            **asdict(self),
            "calendar": "union_of_observed_universe_sessions",
            "candidate_evaluations": sum(period.screening.candidates for period in self.rebalances),
            "unique_selected_pairs": len(self.pairs),
        }


def walk_forward_screen(history, start, end, config=None, sectors=None,
                        training_sessions=252, rebalance_sessions=63) -> WalkForwardResult:
    config = config or ScreenerConfig()
    sectors = SECTOR_UNIVERSE if sectors is None else sectors
    if type(training_sessions) is not int or training_sessions < config.min_training_bars:
        raise ValueError("Walk-forward training must cover the minimum screening history")
    if type(rebalance_sessions) is not int or rebalance_sessions < 1:
        raise ValueError("Rebalance interval must be a positive session count")
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    if start >= end:
        raise ValueError("History start must precede its exclusive end")
    symbols = universe_symbols(sectors)
    calendar = pd.DatetimeIndex([])
    for symbol in symbols:
        if symbol not in history:
            continue
        index = history[symbol].index
        if (not isinstance(index, pd.DatetimeIndex) or index.has_duplicates
                or index.hasnans or index.tz is not None or not index.equals(index.normalize())):
            raise ValueError("Expected unique timezone-naive daily session dates")
        # A missing quote for one stock must not delete sessions for every other stock.
        calendar = calendar.union(index[(index >= start) & (index < end)])
    calendar = calendar.sort_values()
    if len(calendar) <= training_sessions:
        raise ValueError("Walk-forward requires training sessions followed by evaluation data")
    rebalances = []
    for i in range(training_sessions, len(calendar), rebalance_sessions):
        effective = calendar[i]
        training_start = calendar[i - training_sessions]
        # Materialize past-only inputs before screening; even the effective day's close is unavailable.
        historical = {symbol: bars.loc[(bars.index >= training_start) & (bars.index < effective)]
                      for symbol, bars in history.items() if symbol in symbols}
        screening = screen_pairs(historical, training_start, effective, config, sectors)
        rebalances.append(WalkForwardRebalance(
            effective.strftime("%Y-%m-%d"), calendar[i - 1].strftime("%Y-%m-%d"), screening,
        ))
    return WalkForwardResult(tuple(rebalances), training_sessions, rebalance_sessions)


def screen_pairs(history, training_start, training_end, config=None, sectors=None) -> ScreeningResult:
    config = config or ScreenerConfig()
    sectors = SECTOR_UNIVERSE if sectors is None else sectors
    universe_symbols(sectors)
    start, end = pd.Timestamp(training_start), pd.Timestamp(training_end)
    if start >= end:
        raise ValueError("Training start must precede its exclusive end")
    rejected = Counter()
    eligible = []
    candidates = 0
    for sector, symbols in sectors.items():
        for a, b in combinations(symbols, 2):
            candidates += 1
            if a not in history or b not in history:
                rejected["missing_data"] += 1
                continue
            # Slice before alignment or model fitting; evaluation rows cannot affect selection.
            frames = [history[s].loc[(history[s].index >= start) & (history[s].index < end), "Close"] for s in (a, b)]
            aligned = pd.concat(frames, axis=1, keys=["a", "b"], join="inner").dropna().sort_index()
            if len(aligned) < config.min_training_bars:
                rejected["insufficient_training"] += 1
                continue
            strategy = PairTradingStrategy(PairTradingConfig(window=len(aligned)))
            model = strategy.fit(aligned.a, aligned.b)
            if model is None:
                rejected["invalid_spread_model"] += 1
                continue
            if model.pvalue >= config.max_pvalue:
                rejected["cointegration"] += 1
                continue
            spread = aligned.a - model.alpha - model.beta * aligned.b
            ou = fit_ou(spread.to_numpy(), decay=1, min_observations=config.min_training_bars)
            if ou is None or not 0 < ou.half_life < config.max_half_life:
                rejected["half_life"] += 1
                continue
            hurst = estimate_hurst(spread)
            if hurst is None or not 0 <= hurst < config.max_hurst:
                rejected["hurst"] += 1
                continue
            eligible.append(ScreenedPair(sector, (a, b), model.pvalue, ou.half_life, hurst, len(aligned)))
    eligible.sort(key=lambda item: (item.pvalue, item.half_life, item.hurst, item.pair))
    selected = tuple(eligible[:config.max_pairs])
    if len(eligible) > len(selected):
        rejected["rank_limit"] = len(eligible) - len(selected)
    return ScreeningResult(selected, candidates, len(eligible), dict(rejected), start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
