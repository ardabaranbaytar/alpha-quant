"""Past-only spread persistence and cost-aware early-profit rules."""

from dataclasses import dataclass

import numpy as np


def estimate_hurst(spread, min_observations=100, max_lag=20) -> float | None:
    """Generalized H(2): half the log-variogram slope over lags 2..20.

    This finite-scale persistence estimate is not a long-memory or stationarity test.
    Invalid and degenerate estimates are rejected rather than clipped.
    """
    values = np.asarray(spread, dtype=float)
    if values.ndim != 1 or not np.isfinite(values).all():
        return None
    if len(values) < min_observations or np.std(values) < 1e-10:
        return None
    lags = np.arange(2, min(max_lag, len(values) // 5) + 1)
    if len(lags) < 3:
        return None
    variogram = np.array([np.mean((values[lag:] - values[:-lag]) ** 2) for lag in lags])
    if not np.isfinite(variogram).all() or (variogram <= 0).any():
        return None
    hurst = float(np.polyfit(np.log(lags), np.log(variogram), 1)[0] / 2)
    return hurst if np.isfinite(hurst) and 0 <= hurst <= 1 else None


@dataclass(frozen=True)
class EntryFilterConfig:
    max_hurst: float = 0.45
    hurst_window: int = 120
    early_profit_z: float = 0.5

    def __post_init__(self):
        if not 0 < self.max_hurst < 1:
            raise ValueError("Hurst threshold must be between zero and one")
        if type(self.hurst_window) is not int or self.hurst_window < 100:
            raise ValueError("Hurst window must contain at least 100 observations")
        if not np.isfinite(self.early_profit_z) or self.early_profit_z < 0:
            raise ValueError("Early-profit z threshold must be finite and nonnegative")

    def permits_entry(self, hurst: float | None) -> bool:
        return hurst is not None and np.isfinite(hurst) and 0 <= hurst < self.max_hurst

    def take_profit(self, z: float | None, estimated_net_pnl: float) -> bool:
        return (z is not None and np.isfinite([z, estimated_net_pnl]).all()
                and abs(z) <= self.early_profit_z and estimated_net_pnl > 0)
