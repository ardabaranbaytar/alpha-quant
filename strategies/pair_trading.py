"""Database-free Engle-Granger strategy on synchronized price-level series."""

import warnings
from dataclasses import dataclass

import numpy as np
import pandas as pd
import statsmodels.api as sm
from statsmodels.tools.sm_exceptions import CollinearityWarning
from statsmodels.tsa.stattools import coint


@dataclass(frozen=True)
class PairTradingConfig:
    window: int = 60
    entry_z: float = 2.0
    max_pvalue: float = 0.05

    def __post_init__(self):
        if self.window < 30 or not np.isfinite(self.entry_z) or self.entry_z <= 0:
            raise ValueError("Use a window of at least 30 and a positive entry threshold")
        if not 0 < self.max_pvalue < 1:
            raise ValueError("Cointegration significance must be between zero and one")


@dataclass(frozen=True)
class SpreadModel:
    alpha: float
    beta: float
    mean: float
    std: float
    pvalue: float

    def z_score(self, price_a: float, price_b: float) -> float:
        if not np.isfinite([price_a, price_b]).all() or min(price_a, price_b) <= 0:
            raise ValueError("Prices must be finite and positive")
        return float((price_a - self.alpha - self.beta * price_b - self.mean) / self.std)


class PairTradingStrategy:
    def __init__(self, config: PairTradingConfig | None = None):
        self.config = config or PairTradingConfig()

    def fit(self, series_a: pd.Series, series_b: pd.Series) -> SpreadModel | None:
        """Fit only on observations preceding the signal bar supplied by the caller."""
        aligned = pd.concat([series_a.rename("a"), series_b.rename("b")], axis=1, join="inner").dropna().sort_index()
        if aligned.index.has_duplicates:
            raise ValueError("Duplicate observations")
        if len(aligned) < self.config.window:
            return None
        values = aligned.tail(self.config.window).to_numpy(dtype=float)
        if not np.isfinite(values).all() or (values <= 0).any():
            raise ValueError("Prices must be finite and positive")
        a, b = values[:, 0], values[:, 1]
        if np.std(a) < 1e-10 or np.std(b) < 1e-10:
            return None
        with warnings.catch_warnings():
            warnings.simplefilter("error", CollinearityWarning)
            try:
                _, pvalue, _ = coint(a, b, trend="c", autolag="aic")
            except (CollinearityWarning, np.linalg.LinAlgError):
                return None
        alpha, beta = sm.OLS(a, sm.add_constant(b)).fit().params
        spread = a - alpha - beta * b
        mean, std = float(np.mean(spread)), float(np.std(spread, ddof=1))
        if not np.isfinite([alpha, beta, mean, std, pvalue]).all() or std < 1e-8 or beta <= 0:
            return None
        return SpreadModel(float(alpha), float(beta), mean, std, float(pvalue))

    def entry_signal(self, model: SpreadModel, price_a: float, price_b: float) -> str | None:
        if model.pvalue >= self.config.max_pvalue:
            return None
        z = model.z_score(price_a, price_b)
        if z > self.config.entry_z:
            return "SHORT_SPREAD"
        if z < -self.config.entry_z:
            return "LONG_SPREAD"
        return None

    @staticmethod
    def should_close(direction: str, z_score: float) -> bool:
        if direction not in ("LONG_SPREAD", "SHORT_SPREAD") or not np.isfinite(z_score):
            raise ValueError("Invalid position direction or z-score")
        return z_score >= 0 if direction == "LONG_SPREAD" else z_score <= 0
