import sys
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from data_pipeline.yfinance_fetcher import DataFetcher
from research.run_backtest import DEFAULT_PAIRS, ROOT, ExecutionConfig, RiskConfig, execution_time, main, publish_snapshot, simulate_pair
from strategies.pair_trading import PairTradingConfig, PairTradingStrategy, SpreadModel


class KnownModelStrategy(PairTradingStrategy):
    def __init__(self):
        super().__init__(PairTradingConfig(window=30))
        self.training_ends = []

    def fit(self, a, b):
        self.training_ends.append(a.index[-1])
        return SpreadModel(0, 1, 0, 1, 0.01)


def make_history(direction=1, close_position=True):
    dates = pd.bdate_range("2026-01-05", periods=35)
    a = pd.DataFrame({"Open": 100.0, "Close": 100.0}, index=dates)
    b = a.copy()
    a.loc[dates[30]:, "Close"] = 100 + direction * 3
    a.loc[dates[31]:, "Open"] = 100 + direction * 3
    if close_position:
        a.loc[dates[32]:, "Close"] = 100 - direction
        a.loc[dates[33]:, "Open"] = 100.0
    return {"AAPL": a, "MSFT": b}, dates


class PairBacktestTests(unittest.TestCase):
    def test_legacy_manual_universe_and_new_default_download_window(self):
        self.assertEqual(set(DEFAULT_PAIRS), {"AAPL/MSFT", "XOM/CVX", "JPM/BAC", "V/MA", "GOOGL/META", "KO/PEP", "NVDA/AMD"})
        self.assertEqual(len(DEFAULT_PAIRS), 7)
        with patch("research.run_backtest.download_universe", side_effect=RuntimeError("stop before network")) as download:
            with self.assertRaisesRegex(RuntimeError, "stop before network"):
                main(["--end", "2026-01-01"])
        self.assertEqual(download.call_args.args[:2], ("2021-01-01", "2026-01-01"))
        with patch("research.run_backtest.download_universe") as automatic, patch.object(DataFetcher, "download_daily_history", side_effect=RuntimeError("manual download")) as manual:
            with self.assertRaisesRegex(RuntimeError, "No Yahoo Finance history returned"):
                main(["--end", "2026-01-01", "--pairs", "AAPL/MSFT"])
            automatic.assert_not_called()
            self.assertEqual([call.args[0] for call in manual.call_args_list], [["AAPL"], ["MSFT"]])

    def test_risk_boundaries_and_invalid_parameters(self):
        risk = RiskConfig()
        for z in (-3.5, 3.5):
            self.assertFalse(risk.should_close(z, 44))
            self.assertTrue(risk.should_close(z, 45))
        for z in (-3.5001, 3.5001):
            self.assertTrue(risk.should_close(z, 1))
        for kwargs in ({"max_holding_sessions": 0}, {"max_holding_sessions": 1.5}, {"stop_z": float("nan")}, {"stop_z": 0}):
            with self.assertRaises(ValueError):
                RiskConfig(**kwargs)

    def test_z_stop_executes_next_open_in_both_directions_and_can_realize_loss(self):
        for direction in (-1, 1):
            history, dates = make_history(direction, close_position=False)
            history["AAPL"].loc[dates[32]:, "Close"] = 100 + direction * 4
            history["AAPL"].loc[dates[33]:, "Open"] = 100 + direction * 5
            trades, _ = simulate_pair(("AAPL", "MSFT"), history, KnownModelStrategy())
            self.assertEqual(len(trades), 1)
            self.assertEqual(trades[0]["exitTime"], execution_time(dates[33]))
            self.assertLess(trades[0]["netPnl"], 0)
            truncated = {symbol: bars.iloc[:33] for symbol, bars in history.items()}
            self.assertEqual(simulate_pair(("AAPL", "MSFT"), truncated, KnownModelStrategy())[0], [])

    def test_time_stop_counts_45_observed_sessions_including_entry(self):
        dates = pd.bdate_range("2026-01-05", periods=79).delete([35, 36])
        a = pd.DataFrame({"Open": 100.0, "Close": 100.0}, index=dates)
        b = a.copy()
        a.loc[dates[30]:, "Close"] = 103.0
        a.loc[dates[31]:, "Open"] = 103.0
        history = {"AAPL": a, "MSFT": b}
        trades, _ = simulate_pair(("AAPL", "MSFT"), history, KnownModelStrategy())
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["entryTime"], execution_time(dates[31]))
        self.assertEqual(trades[0]["exitTime"], execution_time(dates[76]))
        self.assertLess(trades[0]["netPnl"], 0)
        for length in (75, 76):
            truncated = {symbol: bars.iloc[:length] for symbol, bars in history.items()}
            self.assertEqual(simulate_pair(("AAPL", "MSFT"), truncated, KnownModelStrategy())[0], [])

    def test_imports_do_not_load_database_settings_or_dotenv(self):
        for module in ("config.database", "config.settings", "dotenv", "execution.execution_engine", "web_app.app"):
            self.assertNotIn(module, sys.modules)

    def test_public_writer_rejects_open_records_without_replacing_the_snapshot(self):
        import subprocess
        output = ROOT / "public_site" / "demo-data.js"
        before = output.read_bytes()
        invalid = {"mode": "demo", "asOf": "2026-09-08T00:00:00Z", "currency": "USD", "trades": [{"id": "DEMO-001", "pair": "AAPL / MSFT", "status": "OPEN", "entryTime": "2026-01-05T14:30:00Z", "exitTime": "2026-01-06T14:30:00Z", "netPnl": 12.0}]}
        with self.assertRaises(subprocess.CalledProcessError):
            publish_snapshot(invalid, {}, output)
        self.assertEqual(output.read_bytes(), before)

    def test_cointegration_fit_uses_statsmodels_and_finds_stationary_residual(self):
        rng = np.random.default_rng(24)
        b = pd.Series(100 + rng.normal(0, 1, 250).cumsum())
        a = 12 + 1.7 * b + rng.normal(0, 0.3, 250)
        model = PairTradingStrategy(PairTradingConfig(window=200)).fit(a, b)
        self.assertIsNotNone(model)
        self.assertLess(model.pvalue, 0.05)
        self.assertAlmostEqual(model.beta, 1.7, delta=0.05)
        self.assertGreater(model.std, 0)

    def test_degenerate_series_are_not_tradeable(self):
        series = pd.Series(np.arange(100) + 100.0)
        strategy = PairTradingStrategy()
        self.assertIsNone(strategy.fit(series * 0 + 1, series))
        self.assertIsNone(strategy.fit(series * 2 + 10, series))
        self.assertIsNone(strategy.fit(series[:10], series[:10]))

    def test_exact_thresholds_cointegration_gate_and_zero_crossing(self):
        strategy = PairTradingStrategy()
        model = SpreadModel(0, 1, 0, 1, 0.01)
        self.assertEqual(strategy.entry_signal(model, 103, 100), "SHORT_SPREAD")
        self.assertEqual(strategy.entry_signal(model, 97, 100), "LONG_SPREAD")
        self.assertIsNone(strategy.entry_signal(model, 102, 100))
        self.assertIsNone(strategy.entry_signal(model, 98, 100))
        self.assertIsNone(strategy.entry_signal(SpreadModel(0, 1, 0, 1, 0.2), 110, 100))
        for z in (0, 0.1):
            self.assertTrue(strategy.should_close("LONG_SPREAD", z))
        for z in (0, -0.1):
            self.assertTrue(strategy.should_close("SHORT_SPREAD", z))

    def test_both_directions_execute_on_next_open_and_freeze_the_model(self):
        for direction in (1, -1):
            history, dates = make_history(direction)
            strategy = KnownModelStrategy()
            trades, _ = simulate_pair(("AAPL", "MSFT"), history, strategy, ExecutionConfig(10000, 0, 0, 0))
            self.assertEqual(len(trades), 1)
            self.assertEqual(trades[0]["entryTime"], execution_time(dates[31]))
            self.assertEqual(trades[0]["exitTime"], execution_time(dates[33]))
            expected = 10000 / (200 + direction * 3) * 3
            self.assertAlmostEqual(trades[0]["netPnl"], round(expected, 2))
            self.assertEqual(strategy.training_ends[0], dates[29])
            self.assertNotIn(dates[30], strategy.training_ends)
            self.assertNotIn(dates[31], strategy.training_ends)

    def test_commission_slippage_and_borrow_are_deducted_on_both_legs(self):
        history, dates = make_history()
        costs = ExecutionConfig(10000, 10, 5, 0.03)
        trades, _ = simulate_pair(("AAPL", "MSFT"), history, KnownModelStrategy(), costs)
        units = 10000 / 203
        short_entry, long_entry = 103 * 0.9995, 100 * 1.0005
        short_exit, long_exit = 100 * 1.0005, 100 * 0.9995
        gross = units * (short_entry - short_exit + long_exit - long_entry)
        fees = units * (short_entry + long_entry + short_exit + long_exit) * 0.001
        days = (dates[33] - dates[31]).days
        borrow = units * short_entry * 0.03 * days / 365
        self.assertEqual(trades[0]["netPnl"], round(gross - fees - borrow, 2))

    def test_open_positions_and_unfilled_exit_orders_are_not_exported(self):
        history, _ = make_history(close_position=False)
        trades, _ = simulate_pair(("AAPL", "MSFT"), history, KnownModelStrategy())
        self.assertEqual(trades, [])
        history, _ = make_history()
        history = {symbol: bars.iloc[:33] for symbol, bars in history.items()}
        trades, _ = simulate_pair(("AAPL", "MSFT"), history, KnownModelStrategy())
        self.assertEqual(trades, [])

    def test_later_prices_do_not_change_completed_trades(self):
        history, dates = make_history()
        before, _ = simulate_pair(("AAPL", "MSFT"), history, KnownModelStrategy())
        history["AAPL"].loc[dates[-1], ["Open", "Close"]] = [500, 900]
        after, _ = simulate_pair(("AAPL", "MSFT"), history, KnownModelStrategy())
        self.assertEqual(before, after)

    def test_us_session_open_converts_daylight_saving_to_utc(self):
        self.assertEqual(execution_time("2026-01-05"), "2026-01-05T14:30:00Z")
        self.assertEqual(execution_time("2026-07-06"), "2026-07-06T13:30:00Z")

    def test_downloader_aligns_actual_data_without_filling_missing_sessions(self):
        from pathlib import Path
        import tempfile
        raw = pd.DataFrame({"Open": [100, np.nan, 103], "Close": [101, np.nan, 104]}, index=pd.date_range("2026-01-05", periods=3))
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / "tests") as folder:
            with patch("data_pipeline.yfinance_fetcher.yf.download", return_value=raw) as download, patch("data_pipeline.yfinance_fetcher.yf.set_tz_cache_location"):
                result = DataFetcher().download_daily_history(["AAPL"], "2026-01-05", "2026-01-08", Path(folder))
                self.assertEqual(len(result["AAPL"]), 2)
                self.assertTrue(download.call_args.kwargs["auto_adjust"])
                self.assertFalse(download.call_args.kwargs["threads"])
                self.assertEqual(download.call_args.kwargs["timeout"], DataFetcher.DAILY_HISTORY_TIMEOUT_SECONDS)


if __name__ == "__main__":
    unittest.main()
