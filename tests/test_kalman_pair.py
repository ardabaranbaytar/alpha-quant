import unittest
from dataclasses import replace
from unittest.mock import patch

import numpy as np
import pandas as pd

from research.run_backtest import ExecutionConfig, execution_time, simulate_pair
from strategies.kalman_pair import (
    KalmanObservation,
    KalmanPairConfig,
    KalmanPairStrategy,
    OUModel,
    fit_ou,
)


def synthetic_pair(count=240):
    rng = np.random.default_rng(43)
    b = 100 + np.cumsum(rng.normal(0, 0.7, count))
    residual = np.zeros(count)
    for i in range(1, count):
        residual[i] = 0.9 * residual[i - 1] + rng.normal(0, 0.3)
    a = 15 + 1.4 * b + residual
    index = pd.bdate_range("2024-01-02", periods=count)
    return pd.Series(a, index=index), pd.Series(b, index=index)


class ScriptedKalman(KalmanPairStrategy):
    def __init__(self, missing_after_entry=False):
        super().__init__()
        self.missing_after_entry = missing_after_entry

    def analyze(self, a, b):
        observations = []
        for i in range(len(a)):
            z = 3.0 if i == 60 else (2.5 if i <= 61 else -0.1)
            if self.missing_after_entry and i > 60:
                z = None
            observations.append(KalmanObservation(0, 2 if i <= 60 else 20, 0, z, 10, True))
        return observations


class KalmanPairTests(unittest.TestCase):
    def test_ou_recovers_known_exact_discretization(self):
        rng = np.random.default_rng(31)
        kappa = np.log(2) / 10
        phi = np.exp(-kappa)
        mu, sigma = 0.2, 0.03
        noise_std = sigma * np.sqrt((1 - phi ** 2) / (2 * kappa))
        values = np.zeros(10000)
        values[0] = mu
        for i in range(1, len(values)):
            values[i] = mu * (1 - phi) + phi * values[i - 1] + rng.normal(0, noise_std)
        model = fit_ou(values, decay=1)
        self.assertIsNotNone(model)
        self.assertAlmostEqual(model.half_life, 10, delta=2)
        self.assertAlmostEqual(model.mean, mu, delta=0.015)
        self.assertAlmostEqual(model.diffusion_sigma, sigma, delta=0.003)
        self.assertAlmostEqual(model.equilibrium_std, model.diffusion_sigma / np.sqrt(2 * model.kappa))
        self.assertAlmostEqual(model.half_life, -np.log(2) / np.log(model.phi))

    def test_ou_rejects_degenerate_explosive_and_oscillating_processes(self):
        self.assertIsNone(fit_ou(np.ones(60)))
        self.assertIsNone(fit_ou(np.arange(10)))
        self.assertIsNone(fit_ou(1.02 ** np.arange(120)))
        self.assertIsNone(fit_ou((-1.01) ** np.arange(120)))
        with self.assertRaises(ValueError):
            fit_ou([1, 2, float("nan")])

    def test_filter_matches_state_space_innovation_and_updates_beta(self):
        a, b = synthetic_pair()
        observations = KalmanPairStrategy().analyze(a, b)
        for i, observation in enumerate(observations):
            expected = (a.iloc[i] - observation.alpha - observation.beta * b.iloc[i]) / a.iloc[0]
            self.assertAlmostEqual(observation.residual, expected, places=12)
        self.assertGreater(np.ptp([o.beta for o in observations]), 0.001)
        self.assertTrue(any(o.ou_fresh for o in observations[60:]))
        self.assertTrue(all(o.z is None for o in observations[:60]))

    def test_forward_filter_is_prefix_invariant_and_future_prices_cannot_change_signals(self):
        a, b = synthetic_pair()
        strategy = KalmanPairStrategy()
        full = strategy.analyze(a, b)
        prefix = strategy.analyze(a.iloc[:160], b.iloc[:160])
        changed_a, changed_b = a.copy(), b.copy()
        changed_a.iloc[160:] *= 3
        changed_b.iloc[160:] *= 0.3
        changed = strategy.analyze(changed_a, changed_b)
        self.assertEqual(full[:160], prefix)
        self.assertEqual(full[:160], changed[:160])

    def test_current_residual_does_not_affect_its_own_ou_normalization(self):
        a, b = synthetic_pair()
        strategy = KalmanPairStrategy()
        original = strategy.analyze(a, b)
        index = next(i for i in range(100, len(a)) if original[i].ou_fresh)
        changed_a = a.copy()
        changed_a.iloc[index] += 10
        changed = strategy.analyze(changed_a, b)
        self.assertEqual(original[index].alpha, changed[index].alpha)
        self.assertEqual(original[index].beta, changed[index].beta)
        self.assertEqual(original[index].half_life, changed[index].half_life)
        self.assertGreater(changed[index].z, original[index].z)

    def test_half_life_and_z_entry_boundaries_are_strict(self):
        strategy = KalmanPairStrategy()
        base = KalmanObservation(0, 1, 0, 2.01, 10, True)
        self.assertEqual(strategy.entry_signal(base), "SHORT_SPREAD")
        self.assertEqual(strategy.entry_signal(replace(base, z=-2.01)), "LONG_SPREAD")
        for observation in [replace(base, half_life=3), replace(base, half_life=25),
                            replace(base, half_life=2), replace(base, half_life=30),
                            replace(base, half_life=None), replace(base, beta=-1),
                            replace(base, z=2), replace(base, z=-2),
                            replace(base, z=None), replace(base, ou_fresh=False)]:
            self.assertIsNone(strategy.entry_signal(observation))
        self.assertTrue(strategy.should_close("SHORT_SPREAD", 0))
        self.assertTrue(strategy.should_close("LONG_SPREAD", 0))

    def test_invalid_ou_blocks_entry_but_preserves_last_valid_exit_scale(self):
        a, b = synthetic_pair(65)
        valid = OUModel(0.9, -np.log(0.9), 0, 0.01, 0.02, 10)
        with patch("strategies.kalman_pair.fit_ou", side_effect=[valid, None, None, None, None]):
            observations = KalmanPairStrategy().analyze(a, b)
        self.assertTrue(observations[60].ou_fresh)
        self.assertFalse(observations[61].ou_fresh)
        self.assertIsNone(observations[61].half_life)
        self.assertAlmostEqual(observations[61].z, observations[61].residual / 0.02)
        self.assertIsNone(KalmanPairStrategy().entry_signal(observations[61]))

    def test_dynamic_exit_uses_new_model_but_does_not_rebalance_existing_shares(self):
        dates = pd.bdate_range("2025-01-02", periods=65)
        a = pd.DataFrame({"Open": 103.0, "Close": 104.0}, index=dates)
        b = pd.DataFrame({"Open": 100.0, "Close": 100.0}, index=dates)
        a.loc[dates[63]:, "Open"] = 100.0
        b.loc[dates[63]:, "Open"] = 101.0
        trades, _ = simulate_pair(("AAPL", "MSFT"), {"AAPL": a, "MSFT": b}, ScriptedKalman(), ExecutionConfig(10000, 0, 0, 0))
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["entryTime"], execution_time(dates[61]))
        self.assertEqual(trades[0]["exitTime"], execution_time(dates[63]))
        self.assertEqual(trades[0]["netPnl"], round(10000 / 303 * 5, 2))

    def test_time_exit_remains_available_when_dynamic_z_is_missing(self):
        dates = pd.bdate_range("2025-01-02", periods=108)
        bars = pd.DataFrame({"Open": 100.0, "Close": 100.0}, index=dates)
        trades, _ = simulate_pair(("AAPL", "MSFT"), {"AAPL": bars, "MSFT": bars.copy()}, ScriptedKalman(True))
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["exitTime"], execution_time(dates[106]))

    def test_configuration_and_input_validation(self):
        for kwargs in ({"ou_decay": 0}, {"observation_variance": 0}, {"min_half_life": 30}, {"warmup_sessions": 20}):
            with self.assertRaises(ValueError):
                KalmanPairConfig(**kwargs)
        a, b = synthetic_pair()
        with self.assertRaises(ValueError):
            KalmanPairStrategy().analyze(a, b.iloc[1:])
        a.iloc[10] = np.nan
        with self.assertRaises(ValueError):
            KalmanPairStrategy().analyze(a, b)


if __name__ == "__main__":
    unittest.main()
