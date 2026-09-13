"""Causal time-varying price relationship with an exponentially weighted OU gate."""

from dataclasses import dataclass

import numpy as np
import pandas as pd
import statsmodels.api as sm
from statsmodels.tsa.statespace.kalman_filter import KalmanFilter


@dataclass(frozen=True)
class KalmanPairConfig:
    warmup_sessions: int = 60
    burn_in: int = 20
    ou_min_observations: int = 30
    ou_decay: float = 0.98
    min_half_life: float = 3.0
    max_half_life: float = 25.0
    entry_z: float = 2.0
    observation_variance: float = 1e-4
    alpha_process_variance: float = 1e-7
    beta_process_variance: float = 1e-6
    initial_state_variance: float = 0.01

    def __post_init__(self):
        counts = (self.warmup_sessions, self.burn_in, self.ou_min_observations)
        if any(type(value) is not int for value in counts):
            raise ValueError("Observation counts must be integers")
        if self.burn_in < 0 or self.ou_min_observations < 10 or self.warmup_sessions < self.burn_in + self.ou_min_observations:
            raise ValueError("Warmup must include filter burn-in and enough OU observations")
        if not 0 < self.ou_decay <= 1:
            raise ValueError("OU decay must lie in (0, 1]")
        positive = (self.min_half_life, self.max_half_life, self.entry_z,
                    self.observation_variance, self.alpha_process_variance,
                    self.beta_process_variance, self.initial_state_variance)
        if not np.isfinite(positive).all() or min(positive) <= 0:
            raise ValueError("Model parameters must be finite and positive")
        if self.min_half_life >= self.max_half_life:
            raise ValueError("Half-life bounds must be ordered")


@dataclass(frozen=True)
class OUModel:
    phi: float
    kappa: float
    mean: float
    diffusion_sigma: float
    equilibrium_std: float
    half_life: float


def fit_ou(residuals, decay=0.98, min_observations=30) -> OUModel | None:
    """Fit e[t] = c + phi * e[t-1] + noise; dt is one observed session.

    Exponential weights use the complete supplied history, not a rolling cutoff.
    A nonstationary or non-OU-compatible AR coefficient is rejected, not clipped.
    """
    values = np.asarray(residuals, dtype=float)
    if values.ndim != 1 or not np.isfinite(values).all():
        raise ValueError("Expected a finite one-dimensional residual history")
    if not np.isfinite(decay) or not 0 < decay <= 1 or min_observations < 3:
        raise ValueError("Invalid OU fit settings")
    if len(values) < min_observations or np.std(values[:-1]) < 1e-10:
        return None
    x, y = values[:-1], values[1:]
    weights = decay ** np.arange(len(y) - 1, -1, -1, dtype=float)
    effective_n = weights.sum() ** 2 / np.dot(weights, weights)
    if effective_n <= 2:
        return None
    fit = sm.WLS(y, sm.add_constant(x), weights=weights).fit()
    intercept, phi = fit.params
    if not np.isfinite([intercept, phi]).all() or not 0 < phi < 1:
        return None
    variance = float(np.average(fit.resid ** 2, weights=weights) * effective_n / (effective_n - 2))
    if not np.isfinite(variance) or variance <= 1e-16:
        return None
    kappa = float(-np.log(phi))
    mean = float(intercept / (1 - phi))
    equilibrium_std = float(np.sqrt(variance / (1 - phi ** 2)))
    sigma = float(equilibrium_std * np.sqrt(2 * kappa))
    half_life = float(np.log(2) / kappa)
    if not np.isfinite([mean, equilibrium_std, sigma, half_life]).all():
        return None
    return OUModel(float(phi), kappa, mean, sigma, equilibrium_std, half_life)


@dataclass(frozen=True)
class KalmanObservation:
    alpha: float
    beta: float
    residual: float
    z: float | None
    half_life: float | None
    ou_fresh: bool


class KalmanPairStrategy:
    def __init__(self, config: KalmanPairConfig | None = None):
        self.config = config or KalmanPairConfig()

    def analyze(self, series_a: pd.Series, series_b: pd.Series) -> list[KalmanObservation]:
        """Forward filtering only: predictions at t never depend on closes after t.

        Prices are normalized by their first observed closes. State [alpha, beta]
        follows a random walk (T=I); observation design at t is [1, B[t]/B[0]].
        Q and R are fixed ex ante. No full-sample optimization or smoothing is used.
        """
        if not series_a.index.equals(series_b.index) or series_a.index.has_duplicates or not series_a.index.is_monotonic_increasing:
            raise ValueError("Series must have identical, ordered, unique session indices")
        values = np.column_stack([series_a.to_numpy(dtype=float), series_b.to_numpy(dtype=float)])
        if not len(values) or not np.isfinite(values).all() or (values <= 0).any():
            raise ValueError("Expected nonempty, finite positive price series")
        scale_a, scale_b = values[0]
        a, b = values[:, 0] / scale_a, values[:, 1] / scale_b
        config = self.config
        state_space = KalmanFilter(k_endog=1, k_states=2, nobs=len(values))
        state_space.bind(np.ascontiguousarray(a[:, None]))
        state_space.design = np.stack([np.ones(len(b)), b])[None, :, :]
        state_space.transition = np.eye(2)
        state_space.selection = np.eye(2)
        state_space.state_cov = np.diag([config.alpha_process_variance, config.beta_process_variance])
        state_space.obs_cov = np.array([[config.observation_variance]])
        state_space.initialize_known(np.array([0.0, 1.0]), np.eye(2) * config.initial_state_variance)
        filtered = state_space.filter()
        innovations = filtered.forecasts_error[0]
        predicted = filtered.predicted_state[:, :len(values)]
        if not np.isfinite(innovations).all() or not np.isfinite(predicted).all():
            raise ValueError("Kalman filtering produced non-finite estimates")
        observations = []
        last_valid_ou = None
        for i, residual in enumerate(innovations):
            # OU normalization excludes today's innovation, preventing self-normalization.
            ou = fit_ou(innovations[config.burn_in:i], config.ou_decay, config.ou_min_observations) if i >= config.warmup_sessions else None
            if ou is not None:
                last_valid_ou = ou
            z = None
            if last_valid_ou is not None:
                z = float((residual - last_valid_ou.mean) / last_valid_ou.equilibrium_std)
                if not np.isfinite(z):
                    raise ValueError("Non-finite Kalman residual z-score")
            observations.append(KalmanObservation(
                alpha=float(predicted[0, i] * scale_a),
                beta=float(predicted[1, i] * scale_a / scale_b),
                residual=float(residual), z=z,
                half_life=ou.half_life if ou else None, ou_fresh=ou is not None,
            ))
        return observations

    def entry_signal(self, observation: KalmanObservation) -> str | None:
        config = self.config
        if (not observation.ou_fresh or observation.half_life is None or observation.z is None
                or not np.isfinite([observation.beta, observation.half_life, observation.z]).all()
                or observation.beta <= 0
                or not config.min_half_life < observation.half_life < config.max_half_life):
            return None
        if observation.z > config.entry_z:
            return "SHORT_SPREAD"
        if observation.z < -config.entry_z:
            return "LONG_SPREAD"
        return None

    @staticmethod
    def should_close(direction: str, z_score: float) -> bool:
        if direction not in ("LONG_SPREAD", "SHORT_SPREAD") or not np.isfinite(z_score):
            raise ValueError("Invalid position direction or z-score")
        return z_score >= 0 if direction == "LONG_SPREAD" else z_score <= 0
