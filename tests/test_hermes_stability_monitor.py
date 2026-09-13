import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd

from hermes.stability_monitor import analyze_pair_stability
from strategies.pair_trading import SpreadModel


def stationary_pair(count=260, seed=42):
    rng = np.random.default_rng(seed)
    b = 100 + np.cumsum(rng.normal(0, 1, count))
    residual = np.zeros(count)
    for index in range(1, count):
        residual[index] = 0.75 * residual[index - 1] + rng.normal(0, 0.25)
    a = 12 + 1.7 * b + residual
    index = pd.bdate_range("2025-01-02", periods=count)
    return pd.Series(a, index=index), pd.Series(b, index=index)


class HermesStabilityMonitorTests(unittest.TestCase):
    def test_cointegrated_stationary_pair_is_stable(self):
        a, b = stationary_pair()
        report = analyze_pair_stability("AAA / BBB", a, b)
        self.assertTrue(report.stable, report)
        self.assertEqual(report.rejection_reasons, ())

    def test_independent_random_walks_fail_cointegration(self):
        rng = np.random.default_rng(9)
        index = pd.bdate_range("2025-01-02", periods=260)
        shared_walk = np.cumsum(rng.normal(size=260))
        independent_walk = np.cumsum(rng.normal(size=260))
        report = analyze_pair_stability("AAA / BBB", pd.Series(100 + 0.5 * shared_walk + independent_walk, index=index),
                                        pd.Series(100 + shared_walk, index=index))
        self.assertFalse(report.stable)
        self.assertIn("COINTEGRATION_PVALUE", report.rejection_reasons)

    def test_insufficient_history_is_a_nonthrowing_rejection(self):
        series = pd.Series(np.arange(50, dtype=float) + 100)
        report = analyze_pair_stability("AAA / BBB", series, series)
        self.assertFalse(report.stable)
        self.assertEqual(report.rejection_reasons, ("INSUFFICIENT_DATA",))

    def test_prefix_invariance_for_a_fixed_historical_window(self):
        history_a, history_b = stationary_pair(260)
        baseline = analyze_pair_stability("AAA / BBB", history_a, history_b)
        future_a, future_b = stationary_pair(40, seed=999)
        future_index = pd.bdate_range(history_a.index[-1] + pd.Timedelta(days=1), periods=40)
        extended_a = pd.concat([history_a, pd.Series(future_a.to_numpy() + 1_000, index=future_index)])
        extended_b = pd.concat([history_b, pd.Series(future_b.to_numpy() + 1_000, index=future_index)])
        historical_window = analyze_pair_stability("AAA / BBB", extended_a.iloc[:260], extended_b.iloc[:260])
        self.assertEqual(baseline, historical_window)

    def test_adf_half_life_and_hurst_rejections_are_independent(self):
        a, b = stationary_pair()
        model = SpreadModel(1.0, 1.5, 0.0, 1.0, 0.01)
        cases = (
            ("ADF_PVALUE", {"adf": 0.06, "half_life": 5.0, "hurst": 0.3}),
            ("OU_HALF_LIFE", {"adf": 0.01, "half_life": 21.0, "hurst": 0.3}),
            ("HURST", {"adf": 0.01, "half_life": 5.0, "hurst": 0.46}),
        )
        for expected, values in cases:
            with self.subTest(expected=expected), \
                 patch("hermes.stability_monitor.PairTradingStrategy.fit", return_value=model), \
                 patch("hermes.stability_monitor.adfuller", return_value=(0.0, values["adf"], 0, 0, {}, 0.0)), \
                 patch("hermes.stability_monitor.fit_ou", return_value=SimpleNamespace(half_life=values["half_life"])), \
                 patch("hermes.stability_monitor.estimate_hurst", return_value=values["hurst"]):
                report = analyze_pair_stability("AAA / BBB", a, b)
            self.assertFalse(report.stable)
            self.assertEqual(report.rejection_reasons, (expected,))
