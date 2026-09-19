"""Database-free Hermes stability analysis aligned with Pipeline A."""

import numpy as np
import pandas as pd
from statsmodels.tsa.stattools import adfuller

from hermes.models import StabilityConfig, StabilityReport
from strategies.kalman_pair import fit_ou
from strategies.mean_reversion import estimate_hurst
from strategies.pair_trading import PairTradingConfig, PairTradingStrategy


def _rejected(pair: str, observations: int, reason: str) -> StabilityReport:
    return StabilityReport(pair, observations, None, None, None, None, None, None, False, (reason,))


def analyze_pair_stability(pair: str, prices_a: pd.Series, prices_b: pd.Series,
                           config: StabilityConfig | None = None) -> StabilityReport:
    """Analyze aligned price levels without DB, network, filesystem, or mutation."""
    if config is None:
        config = StabilityConfig()
    if not isinstance(pair, str) or not pair:
        raise ValueError("pair must be a nonempty string")
    if not isinstance(prices_a, pd.Series) or not isinstance(prices_b, pd.Series):
        return _rejected(pair, 0, "INSUFFICIENT_DATA")
    try:
        aligned = pd.concat([prices_a.rename("a"), prices_b.rename("b")], axis=1, join="inner").sort_index()
        observations = len(aligned)
        values = aligned.to_numpy(dtype=float)
    except (TypeError, ValueError):
        return _rejected(pair, 0, "INSUFFICIENT_DATA")
    if (observations < config.min_observations or aligned.index.has_duplicates or not np.isfinite(values).all()
            or (values <= 0).any()):
        return _rejected(pair, observations, "INSUFFICIENT_DATA")

    try:
        strategy = PairTradingStrategy(PairTradingConfig(window=observations))
        model = strategy.fit(aligned.a, aligned.b)
        if model is None:
            return _rejected(pair, observations, "INVALID_SPREAD_MODEL")
        spread = aligned.a - model.alpha - model.beta * aligned.b
        adf_pvalue = float(adfuller(spread, autolag="AIC")[1])
        ou = fit_ou(spread.to_numpy(), decay=1, min_observations=config.min_observations)
        hurst = estimate_hurst(spread.to_numpy())
    except Exception:  # noqa: BLE001
        return _rejected(pair, observations, "ANALYSIS_ERROR")

    reasons = []
    if model.pvalue > config.max_coint_pvalue:
        reasons.append("COINTEGRATION_PVALUE")
    if adf_pvalue > config.max_adf_pvalue:
        reasons.append("ADF_PVALUE")
    if ou is None:
        reasons.append("INVALID_OU_MODEL")
    elif ou.half_life > config.max_half_life:
        reasons.append("OU_HALF_LIFE")
    if hurst is None:
        reasons.append("INVALID_HURST")
    elif hurst > config.max_hurst:
        reasons.append("HURST")
    return StabilityReport(pair, observations, model.alpha, model.beta, model.pvalue, adf_pvalue,
                           ou.half_life if ou is not None else None, hurst, not reasons, tuple(reasons))
