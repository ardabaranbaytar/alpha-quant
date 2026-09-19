import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from research.run_backtest import (
    ExecutionConfig,
    RiskConfig,
    VolatilitySizingConfig,
    execution_time,
    liquidation_pnl,
    main,
    simulate_pair,
)
from strategies.kalman_pair import (
    KalmanObservation,
    KalmanPairConfig,
    KalmanPairStrategy,
)
from strategies.mean_reversion import EntryFilterConfig
from strategies.pair_trading import PairTradingConfig, PairTradingStrategy, SpreadModel


class SizingStrategy(PairTradingStrategy):
    def __init__(self, std=1):
        super().__init__(PairTradingConfig(window=30, entry_z=0.5))
        self.std = std

    def fit(self, a, b):
        return SpreadModel(0, 1, 0, self.std, 0.01)


def sizing_history(direction=1, count=35, signal_index=30):
    dates = pd.bdate_range("2025-01-02", periods=count)
    a = pd.DataFrame({"Open": 100.0, "Close": 100.0}, index=dates)
    b = a.copy()
    a.loc[dates[signal_index]:, "Close"] = 100 + direction * 3
    a.loc[dates[signal_index + 1]:, "Open"] = 100 + direction * 3
    a.loc[dates[signal_index + 2]:, "Close"] = 100 - direction
    a.loc[dates[signal_index + 3]:, "Open"] = 100.0
    return {"AAA": a, "BBB": b}, dates


class InverseVolatilitySizingTests(unittest.TestCase):
    def test_inverse_volatility_target_and_notional_bounds(self):
        sizing = VolatilitySizingConfig()
        self.assertEqual((sizing.target_spread_risk, sizing.min_gross_notional, sizing.max_gross_notional),
                         (100, 10000, 25000))
        exposures = [sizing.units(std, [100, 50], 2) * 200 for std in (0.1, 0.5, 1, 2, 10)]
        np.testing.assert_allclose(exposures, [25000, 25000, 20000, 10000, 10000])
        self.assertEqual(exposures, sorted(exposures, reverse=True))
        self.assertEqual(sizing.units(1, [100, 50], 2) * 1, sizing.target_spread_risk)

    def test_price_and_residual_rescaling_preserves_gross_notional(self):
        sizing = VolatilitySizingConfig()
        units = sizing.units(1.1, [100, 60], 2)
        rescaled = sizing.units(11, [1000, 600], 2)
        self.assertAlmostEqual(units, 10 * rescaled)
        self.assertAlmostEqual(units * 220, rescaled * 2200)

    def test_invalid_risk_parameters_and_degenerate_volatility(self):
        for kwargs in ({"target_spread_risk": 0}, {"target_spread_risk": np.nan},
                       {"target_spread_risk": np.inf}, {"min_gross_notional": -1},
                       {"min_gross_notional": 26000}, {"max_gross_notional": np.inf}):
            with self.assertRaises(ValueError):
                VolatilitySizingConfig(**kwargs)
        sizing = VolatilitySizingConfig()
        for std in (None, 0, -1, np.nan, np.inf, 1e-10):
            self.assertIsNone(sizing.units(std, [100, 100], 1))
        for prices, beta in (([0, 100], 1), ([100, np.nan], 1), ([100], 1), ([100, 100], 0)):
            with self.assertRaises(ValueError):
                sizing.units(1, prices, beta)

    def test_costs_borrow_and_hedged_quantities_scale_with_executed_notional(self):
        costs, sizing = ExecutionConfig(), VolatilitySizingConfig()
        for direction in (-1, 1):
            for std in (0.1, 1, 4):
                history, dates = sizing_history(direction)
                with patch("research.run_backtest.liquidation_pnl", wraps=liquidation_pnl) as liquidate:
                    trades, _ = simulate_pair(("AAA", "BBB"), history, SizingStrategy(std), costs,
                                              RiskConfig(stop_z=100), sizing=sizing)
                self.assertEqual(len(trades), 1)
                position = liquidate.call_args.args[0]
                gross = np.dot(np.abs(position["quantities"]), position["prices"])
                self.assertGreaterEqual(gross, 10000 - 1e-8)
                self.assertLessEqual(gross, 25000 + 1e-8)
                expected_prices = np.array([100 + direction * 3, 100]) * (1 + np.array([-direction, direction]) * 0.0005)
                expected_gross = np.clip(100 / std * sum(expected_prices), 10000, 25000)
                units = expected_gross / sum(expected_prices)
                quantities = np.array([-direction * units, direction * units])
                np.testing.assert_allclose(position["quantities"], quantities)
                np.testing.assert_allclose(position["prices"], expected_prices)
                self.assertAlmostEqual(position["entry_fee"], expected_gross * 0.001)
                exit_prices = np.array([100, 100]) * (1 - np.sign(quantities) * 0.0005)
                short_notional = np.dot(np.maximum(-quantities, 0), expected_prices)
                days = (pd.Timestamp(execution_time(dates[33])) - pd.Timestamp(execution_time(dates[31]))).total_seconds() / 86400
                expected_pnl = (np.dot(quantities, exit_prices - expected_prices)
                                - expected_gross * 0.001 - np.dot(np.abs(quantities), exit_prices) * 0.001
                                - short_notional * 0.03 * days / 365)
                self.assertEqual(trades[0]["netPnl"], round(expected_pnl, 2))

    def test_opening_gap_keeps_actual_entry_exposure_within_bounds(self):
        history, dates = sizing_history()
        history["AAA"].loc[dates[31], "Open"] = 200.0
        sizing = VolatilitySizingConfig()
        with patch("research.run_backtest.liquidation_pnl", wraps=liquidation_pnl) as liquidate:
            simulate_pair(("AAA", "BBB"), history, SizingStrategy(0.1), risk=RiskConfig(stop_z=100), sizing=sizing)
        position = liquidate.call_args.args[0]
        self.assertAlmostEqual(np.dot(np.abs(position["quantities"]), position["prices"]), 25000)

    def test_current_signal_and_future_closes_cannot_affect_ols_volatility_at_entry(self):
        history, dates = sizing_history()
        history["AAA"].loc[dates[:30], "Close"] += np.sin(np.arange(30))
        strategy = PairTradingStrategy(PairTradingConfig(window=30))
        sizing = VolatilitySizingConfig()

        def fit(a, b):
            return SpreadModel(0, 1, 0, float(np.std(a - b, ddof=1)), 0.01)

        expected_std = float(np.std(history["AAA"].Close.iloc[:30] - history["BBB"].Close.iloc[:30], ddof=1))
        for current in (103.0, 150.0):
            history["AAA"].loc[dates[30], "Close"] = current
            history["AAA"].loc[dates[34], "Close"] = current * 2
            with patch.object(strategy, "fit", side_effect=fit), patch.object(VolatilitySizingConfig, "units", autospec=True, return_value=50) as size:
                simulate_pair(("AAA", "BBB"), history, strategy, risk=RiskConfig(stop_z=100), sizing=sizing)
            self.assertAlmostEqual(size.call_args_list[0].args[1], expected_std)

    def test_scaled_early_profit_and_rebalance_preserve_entry_quantities(self):
        history, dates = sizing_history(count=125, signal_index=120)
        history["AAA"].loc[dates[122]:, "Close"] = 100.5
        schedule = pd.Series([True, False], index=dates[[120, 122]])
        with patch("research.run_backtest.estimate_hurst", return_value=0.3), patch("research.run_backtest.liquidation_pnl", wraps=liquidation_pnl) as liquidate:
            trades, _ = simulate_pair(("AAA", "BBB"), history, SizingStrategy(),
                                      entry_filters=EntryFilterConfig(), entry_schedule=schedule,
                                      sizing=VolatilitySizingConfig())
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["exitTime"], execution_time(dates[123]))
        self.assertGreater(trades[0]["netPnl"], 0)
        estimated_position = liquidate.call_args_list[0].args[0]
        actual_position = liquidate.call_args_list[1].args[0]
        self.assertIs(estimated_position, actual_position)

    def test_kalman_volatility_uses_only_previous_innovations_and_dollar_scale(self):
        history, _dates = sizing_history(count=125, signal_index=120)
        strategy = KalmanPairStrategy(KalmanPairConfig())
        observations = [KalmanObservation(0, 1, 0.01 * np.sin(i), None, None, False) for i in range(125)]
        observations[120] = KalmanObservation(0, 1, 999, 3, 5, True)
        observations[122] = KalmanObservation(0, 1, 999, 0, None, False)
        expected = float(np.std([o.residual for o in observations[60:120]], ddof=1) * 100)
        with patch.object(strategy, "analyze", return_value=observations), patch.object(VolatilitySizingConfig, "units", autospec=True, return_value=50) as size:
            trades, _ = simulate_pair(("AAA", "BBB"), history, strategy, sizing=VolatilitySizingConfig())
        self.assertEqual(len(trades), 1)
        self.assertAlmostEqual(size.call_args.args[1], expected)

    def test_invalid_target_risk_cli_is_rejected_before_download(self):
        for value in ("0", "nan", "-1"):
            with patch("research.run_backtest.download_universe") as download, patch("sys.stderr"):
                with self.assertRaises(SystemExit):
                    main(["--target-spread-risk", value])
                download.assert_not_called()


if __name__ == "__main__":
    unittest.main()
