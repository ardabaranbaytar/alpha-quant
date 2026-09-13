"""Causal price-history cleaning: drop bad prints without ever synthesizing a fill.

A rejected or missing session is simply absent from the returned frame. Callers
that align two symbols (inner join) then see that date disappear for the pair
rather than being interpolated or carried forward from a stale quote.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class SanitizerConfig:
    max_daily_move: float = 0.5

    def __post_init__(self):
        if not np.isfinite(self.max_daily_move) or self.max_daily_move <= 0:
            raise ValueError("Maximum daily move must be finite and positive")


def sanitize_daily_bars(bars: pd.DataFrame, config: SanitizerConfig | None = None) -> tuple[pd.DataFrame, dict[str, int]]:
    """Drop invalid prices, zero/missing-volume sessions, and single-day bad ticks.

    Expects a sorted, unique DatetimeIndex and an ``Open``/``Close`` pair of
    columns; a ``Volume`` column is used for the zero-volume check when present
    and is never required. Rows are dropped outright, never filled or clipped.
    """
    config = config or SanitizerConfig()
    if not isinstance(bars.index, pd.DatetimeIndex) or bars.index.has_duplicates or not bars.index.is_monotonic_increasing:
        raise ValueError("Expected a sorted, unique daily DatetimeIndex")
    if not {"Open", "Close"} <= set(bars.columns):
        raise ValueError("Expected Open and Close columns")
    dropped = {"invalid_price": 0, "zero_volume": 0, "bad_tick": 0}
    working = bars

    prices = working.loc[:, ["Open", "Close"]].to_numpy(dtype=float)
    valid_price = np.isfinite(prices).all(axis=1) & (prices > 0).all(axis=1)
    dropped["invalid_price"] = int((~valid_price).sum())
    working = working.loc[valid_price]

    if "Volume" in working.columns:
        has_volume = working["Volume"].fillna(0).to_numpy(dtype=float) > 0
        dropped["zero_volume"] = int((~has_volume).sum())
        working = working.loc[has_volume]

    if len(working) > 1:
        daily_return = working["Close"].pct_change().abs().to_numpy()
        bad_tick = np.nan_to_num(daily_return, nan=0.0) > config.max_daily_move
        dropped["bad_tick"] = int(bad_tick.sum())
        working = working.loc[~bad_tick]

    return working, dropped
