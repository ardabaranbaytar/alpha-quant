import unittest
from dataclasses import replace
from itertools import combinations
from unittest.mock import patch

import numpy as np
import pandas as pd

from research.pair_screener import (
    SECTOR_UNIVERSE,
    ScreenedPair,
    ScreenerConfig,
    ScreeningResult,
    WalkForwardRebalance,
    WalkForwardResult,
    download_research_history,
    screen_pairs,
    symbol_sectors,
    universe_symbols,
    walk_forward_screen,
)
from research.run_backtest import execution_time, simulate_pair
from strategies.kalman_pair import OUModel
from strategies.mean_reversion import EntryFilterConfig, estimate_hurst
from strategies.pair_trading import PairTradingConfig, PairTradingStrategy, SpreadModel


class FixedSpreadStrategy(PairTradingStrategy):
    def __init__(self):
        super().__init__(PairTradingConfig(window=30))

    def fit(self, a, b):
        return SpreadModel(0, 1, 0, 1, 0.01)


def profit_history(direction=1, entry_open=None, exit_open=None):
    dates = pd.bdate_range("2025-01-02", periods=125)
    a = pd.DataFrame({"Open": 100.0, "Close": 100.0}, index=dates)
    b = a.copy()
    a.loc[dates[120]:, "Close"] = 100 + direction * 3
    a.loc[dates[121]:, "Open"] = entry_open if entry_open is not None else 100 + direction * 3
    a.loc[dates[122]:, "Close"] = 100 + direction * 0.5
    a.loc[dates[123]:, "Open"] = exit_open if exit_open is not None else 100 + direction
    return {"AAPL": a, "MSFT": b}, dates


class PairScreenerTests(unittest.TestCase):
    def test_provider_ticker_rename_preserves_research_identity_without_duplicate_security(self):
        bars = pd.DataFrame({"Close": [100.0]}, index=pd.DatetimeIndex(["2025-01-02"]))
        with patch("research.pair_screener.DataFetcher.download_daily_history", return_value={"BNY": bars}) as download:
            result = download_research_history(["BK"], "2021-01-01", "2026-09-09", None)
            self.assertEqual(download.call_args.args[0], ["BNY"])
            self.assertIs(result["BK"], bars)
            self.assertNotIn("BNY", result)
            with self.assertRaises(ValueError):
                download_research_history(["BK", "BNY"], "2021-01-01", "2026-09-09", None)

    def test_missing_provider_history_is_logged_and_skipped_per_symbol(self):
        bars = pd.DataFrame({"Close": [100.0]}, index=pd.DatetimeIndex(["2025-01-02"]))

        def download(symbols, *_args):
            if symbols == ["BBB"]:
                raise RuntimeError("No Yahoo Finance history returned for BBB")
            return {"AAA": bars}

        with patch("research.pair_screener.DataFetcher.download_daily_history", side_effect=download), \
                self.assertLogs("research.pair_screener", level="WARNING") as logs:
            result = download_research_history(["AAA", "BBB"], "2021-01-01", "2026-09-09", None)

        self.assertEqual(set(result), {"AAA"})
        self.assertIs(result["AAA"], bars)
        self.assertIn("Skipping BBB", logs.output[0])

    def test_sixty_symbols_and_163_same_sector_candidates(self):
        symbols = universe_symbols()
        self.assertEqual(len(symbols), 60)
        self.assertEqual(len(set(symbols)), 60)
        self.assertEqual(len(SECTOR_UNIVERSE), 10)
        candidates = [pair for group in SECTOR_UNIVERSE.values() for pair in combinations(group, 2)]
        self.assertEqual(len(candidates), 163)
        self.assertIn(("NEE", "DUK"), candidates)
        self.assertNotIn(("JPM", "CB"), candidates)
        self.assertNotIn(("GOOGL", "NFLX"), candidates)
        self.assertNotIn(("CAT", "MSFT"), candidates)
        self.assertNotIn(("JPM", "XOM"), candidates)

    def test_symbol_sectors_covers_the_full_universe_for_portfolio_exposure_caps(self):
        mapping = symbol_sectors()
        self.assertEqual(set(mapping), set(universe_symbols()))
        self.assertEqual(mapping["NEE"], "Utilities")
        self.assertEqual(mapping["JPM"], "Financials")
        for sector, symbols in SECTOR_UNIVERSE.items():
            self.assertTrue(all(mapping[symbol] == sector for symbol in symbols))

    def test_ranking_caps_at_ten_and_rejection_counts_reconcile(self):
        index = pd.bdate_range("2024-01-02", periods=252)
        history = {symbol: pd.DataFrame({"Close": 100 + np.sin(np.arange(252) / 5)}, index=index) for symbol in universe_symbols()}
        ou = OUModel(0.8, 0.2, 0, 1, 1, 5)
        with patch("research.pair_screener.PairTradingStrategy.fit", return_value=SpreadModel(0, 1, 0, 1, 0.001)), patch("research.pair_screener.fit_ou", return_value=ou), patch("research.pair_screener.estimate_hurst", return_value=0.3):
            report = screen_pairs(history, "2024-01-01", "2025-01-01")
        self.assertEqual(len(report.selected), 10)
        self.assertEqual(report.eligible, 163)
        self.assertEqual(report.rejected, {"rank_limit": 153})
        self.assertEqual(sum(report.rejected.values()) + len(report.selected), report.candidates)
        keys = [(p.pvalue, p.half_life, p.hurst, p.pair) for p in report.selected]
        self.assertEqual(keys, sorted(keys))

    def test_strict_screening_thresholds_and_no_padding_to_eight(self):
        index = pd.bdate_range("2024-01-02", periods=252)
        bars = pd.DataFrame({"Close": 100 + np.sin(np.arange(252) / 5)}, index=index)
        history, sectors = {"AAA": bars, "BBB": bars.copy()}, {"Test sector": ("AAA", "BBB")}
        base = SpreadModel(0, 1, 0, 1, 0.001)
        ou = OUModel(0.8, 0.2, 0, 1, 1, 5)
        for model, ou_fit, hurst, reason in (
            (replace(base, pvalue=0.05), ou, 0.3, "cointegration"),
            (base, replace(ou, half_life=20), 0.3, "half_life"),
            (base, ou, 0.45, "hurst"),
            (base, ou, None, "hurst"),
        ):
            with patch("research.pair_screener.PairTradingStrategy.fit", return_value=model), patch("research.pair_screener.fit_ou", return_value=ou_fit), patch("research.pair_screener.estimate_hurst", return_value=hurst):
                report = screen_pairs(history, "2024-01-01", "2025-01-01", sectors=sectors)
            self.assertEqual(report.selected, ())
            self.assertEqual(report.rejected, {reason: 1})
        with patch("research.pair_screener.PairTradingStrategy.fit", return_value=replace(base, pvalue=0.0499)), patch("research.pair_screener.fit_ou", return_value=ou), patch("research.pair_screener.estimate_hurst", return_value=0.3):
            report = screen_pairs(history, "2024-01-01", "2025-01-01", sectors=sectors)
        self.assertEqual(len(report.selected), 1)
        self.assertEqual(ScreenerConfig().max_pvalue, 0.05)

    def test_future_evaluation_prices_cannot_change_training_selection(self):
        rng = np.random.default_rng(71)
        index = pd.bdate_range("2023-01-02", periods=530)
        b = 100 + rng.normal(0, 0.7, len(index)).cumsum()
        spread = np.zeros(len(index))
        for i in range(1, len(index)):
            spread[i] = 0.7 * spread[i - 1] + rng.normal(0, 0.2)
        history = {"AAA": pd.DataFrame({"Close": 10 + 1.5 * b + spread}, index=index), "BBB": pd.DataFrame({"Close": b}, index=index)}
        sectors = {"Test sector": ("AAA", "BBB")}
        before = screen_pairs(history, "2023-01-01", "2024-01-01", sectors=sectors)
        self.assertEqual(len(before.selected), 1)
        for bars in history.values():
            bars.loc[bars.index >= "2024-01-01", "Close"] = np.nan
        after = screen_pairs(history, "2023-01-01", "2024-01-01", sectors=sectors)
        self.assertEqual(before, after)

    def test_missing_and_short_histories_are_counted(self):
        index = pd.bdate_range("2024-01-02", periods=50)
        bars = pd.DataFrame({"Close": 100.0}, index=index)
        report = screen_pairs({"AAA": bars, "BBB": bars.copy()}, "2024-01-01", "2025-01-01", sectors={"Test": ("AAA", "BBB", "CCC")})
        self.assertEqual(report.rejected, {"insufficient_training": 1, "missing_data": 2})
        self.assertEqual(report.candidates, 3)


class MeanReversionFilterTests(unittest.TestCase):
    def test_hurst_distinguishes_random_walk_from_mean_reverting_spread(self):
        rng = np.random.default_rng(17)
        walk = rng.normal(size=15000).cumsum()
        spread = np.zeros(15000)
        for i in range(1, len(spread)):
            spread[i] = 0.7 * spread[i - 1] + rng.normal()
        self.assertAlmostEqual(estimate_hurst(walk), 0.5, delta=0.06)
        self.assertLess(estimate_hurst(spread), 0.45)
        self.assertIsNone(estimate_hurst(np.ones(200)))
        self.assertIsNone(estimate_hurst([1, 2, 3]))

    def test_strict_hurst_and_cost_aware_profit_boundaries(self):
        rules = EntryFilterConfig()
        self.assertTrue(rules.permits_entry(0.4499))
        self.assertFalse(rules.permits_entry(0.45))
        self.assertFalse(rules.permits_entry(None))
        self.assertFalse(rules.permits_entry(-0.1))
        for z in (-0.5, 0.5):
            self.assertTrue(rules.take_profit(z, 0.01))
            self.assertFalse(rules.take_profit(z, 0))
            self.assertFalse(rules.take_profit(z, -10))
        self.assertFalse(rules.take_profit(0.5001, 100))
        self.assertFalse(rules.take_profit(None, 100))

    def test_hurst_gate_blocks_entries_and_excludes_current_signal_bar(self):
        history, _ = profit_history()
        for hurst in (None, 0.45, 0.6):
            with patch("research.run_backtest.estimate_hurst", return_value=hurst) as estimator:
                trades, _ = simulate_pair(("AAPL", "MSFT"), history, FixedSpreadStrategy(), entry_filters=EntryFilterConfig())
            self.assertEqual(trades, [])
            first_sample = np.asarray(estimator.call_args_list[0].args[0])
            self.assertEqual(len(first_sample), 120)
            self.assertTrue((first_sample == 0).all())

    def test_early_profit_executes_next_open_for_both_directions(self):
        for direction in (-1, 1):
            history, dates = profit_history(direction)
            with patch("research.run_backtest.estimate_hurst", return_value=0.3):
                trades, _ = simulate_pair(("AAPL", "MSFT"), history, FixedSpreadStrategy(), entry_filters=EntryFilterConfig())
            self.assertEqual(len(trades), 1)
            self.assertEqual(trades[0]["entryTime"], execution_time(dates[121]))
            self.assertEqual(trades[0]["exitTime"], execution_time(dates[123]))
            self.assertGreater(trades[0]["netPnl"], 0)

    def test_positive_gross_but_negative_net_does_not_trigger_take_profit(self):
        history, _ = profit_history(entry_open=100.6)
        with patch("research.run_backtest.estimate_hurst", return_value=0.3):
            trades, _ = simulate_pair(("AAPL", "MSFT"), history, FixedSpreadStrategy(), entry_filters=EntryFilterConfig())
        self.assertEqual(trades, [])

    def test_next_open_gap_can_turn_estimated_profit_into_realized_loss(self):
        history, dates = profit_history(exit_open=106)
        with patch("research.run_backtest.estimate_hurst", return_value=0.3):
            trades, _ = simulate_pair(("AAPL", "MSFT"), history, FixedSpreadStrategy(), entry_filters=EntryFilterConfig())
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["exitTime"], execution_time(dates[123]))
        self.assertLess(trades[0]["netPnl"], 0)

    def test_training_period_cannot_open_positions(self):
        history, dates = profit_history()
        with patch("research.run_backtest.estimate_hurst", return_value=0.3):
            trades, _ = simulate_pair(("AAPL", "MSFT"), history, FixedSpreadStrategy(), entry_filters=EntryFilterConfig(), trade_start=dates[123])
        self.assertEqual(trades, [])


class WalkForwardScreeningTests(unittest.TestCase):
    def history(self, count=1040):
        rng = np.random.default_rng(71)
        dates = pd.bdate_range("2021-01-04", periods=count + 3).delete([50, 300, 700])
        b = 100 + rng.normal(0, 0.3, count).cumsum()
        spread = np.zeros(count)
        for i in range(1, count):
            spread[i] = 0.7 * spread[i - 1] + rng.normal(0, 0.2)
        history = {"AAA": pd.DataFrame({"Close": 10 + 1.5 * b + spread}, index=dates),
                   "BBB": pd.DataFrame({"Close": b}, index=dates)}
        return history, dates, {"Test": ("AAA", "BBB")}

    def test_quarterly_windows_use_exactly_252_past_sessions_including_final_partial_quarter(self):
        history, dates, sectors = self.history()
        with patch("research.pair_screener.screen_pairs", wraps=screen_pairs) as screen:
            result = walk_forward_screen(history, dates[0], dates[-1] + pd.Timedelta(days=1), sectors=sectors)
        self.assertEqual(len(result.rebalances), 13)
        self.assertEqual(result.rebalance_sessions, 63)
        for i, (period, call) in enumerate(zip(result.rebalances, screen.call_args_list)):
            offset = 252 + i * 63
            self.assertEqual(period.effective_session, dates[offset].strftime("%Y-%m-%d"))
            self.assertEqual(period.training_last_session, dates[offset - 1].strftime("%Y-%m-%d"))
            self.assertEqual(period.screening.training_start, dates[offset - 252].strftime("%Y-%m-%d"))
            for bars in call.args[0].values():
                self.assertEqual(list(bars.index), list(dates[offset - 252:offset]))
                self.assertLess(bars.index.max(), pd.Timestamp(period.effective_session))
            self.assertEqual(period.screening.candidates, 1)
            self.assertEqual(sum(period.screening.rejected.values()) + len(period.screening.selected), 1)
        self.assertEqual(result.to_report()["candidate_evaluations"], 13)

    def test_future_prices_and_appended_history_cannot_change_past_rebalances(self):
        history, dates, sectors = self.history()
        end = dates[-1] + pd.Timedelta(days=1)
        before = walk_forward_screen(history, dates[0], end, sectors=sectors)
        self.assertTrue(before.pairs)
        prefix = {symbol: bars.iloc[:757] for symbol, bars in history.items()}
        truncated = walk_forward_screen(prefix, dates[0], end, sectors=sectors)
        self.assertEqual(before.rebalances[:9], truncated.rebalances)
        for bars in history.values():
            bars.loc[dates[756]:, "Close"] = np.nan
        after = walk_forward_screen(history, dates[0], end, sectors=sectors)
        self.assertEqual(before.rebalances[:9], after.rebalances[:9])
        self.assertEqual(after.rebalances[-1].screening.selected, ())

    def test_expired_training_prices_and_one_stocks_missing_quotes_do_not_shift_calendar(self):
        history, dates, sectors = self.history()
        before = walk_forward_screen(history, dates[0], dates[-1], sectors=sectors)
        history["AAA"].loc[:dates[251], "Close"] *= 2
        history["BBB"] = history["BBB"].drop(dates[25])
        after = walk_forward_screen(history, dates[0], dates[-1], sectors=sectors)
        self.assertEqual(before.rebalances[4:], after.rebalances[4:])
        self.assertEqual(before.rebalances[0].effective_session, after.rebalances[0].effective_session)

    def test_empty_year_disables_entries_and_reselection_restores_membership(self):
        candidate = ScreenedPair("Test", ("AAA", "BBB"), 0.01, 5, 0.3, 252)
        periods = []
        for year, selected in ((2022, (candidate,)), (2023, ()), (2024, (candidate,))):
            report = ScreeningResult(selected, 1, len(selected), {} if selected else {"cointegration": 1},
                                     f"{year - 1}-01-01", f"{year}-01-01")
            periods.append(WalkForwardRebalance(f"{year}-01-01", f"{year - 1}-12-31", report))
        result = WalkForwardResult(tuple(periods), 252, 252)
        self.assertEqual(result.entry_schedule(("AAA", "BBB")).tolist(), [True, False, True])
        self.assertEqual(result.entry_schedule(("BBB", "CCC")).tolist(), [False, False, False])
        self.assertEqual(result.pairs, (("AAA", "BBB"),))
        self.assertEqual(result.to_report()["unique_selected_pairs"], 1)

    def test_rejects_invalid_windows_and_missing_evaluation_history(self):
        history, dates, sectors = self.history()
        for kwargs in ({"training_sessions": 199}, {"rebalance_sessions": 0}, {"rebalance_sessions": 2.5}):
            with self.assertRaises(ValueError):
                walk_forward_screen(history, dates[0], dates[-1], sectors=sectors, **kwargs)
        with self.assertRaisesRegex(ValueError, "evaluation"):
            walk_forward_screen(history, dates[0], dates[252], sectors=sectors)


class WalkForwardExecutionTests(unittest.TestCase):
    def simulate(self, history, schedule):
        with patch("research.run_backtest.estimate_hurst", return_value=0.3):
            return simulate_pair(("AAPL", "MSFT"), history, FixedSpreadStrategy(),
                                 entry_filters=EntryFilterConfig(), entry_schedule=schedule)[0]

    def test_future_selection_does_not_enable_past_signals(self):
        history, dates = profit_history()
        self.assertEqual(self.simulate(history, pd.Series([True], index=dates[[123]])), [])
        trades = self.simulate(history, pd.Series([True], index=dates[[121]]))
        self.assertEqual(trades[0]["entryTime"], execution_time(dates[122]))

    def test_removed_pair_cancels_pending_entry_at_rebalance_open(self):
        history, dates = profit_history()
        schedule = pd.Series([True, False], index=dates[[120, 121]])
        self.assertEqual(self.simulate(history, schedule), [])

    def test_removed_pair_keeps_position_until_normal_take_profit_without_resetting_costs(self):
        history, dates = profit_history()
        expected = self.simulate(history, None)
        schedule = pd.Series([True, False], index=dates[[120, 122]])
        self.assertEqual(self.simulate(history, schedule), expected)
        self.assertEqual(expected[0]["exitTime"], execution_time(dates[123]))

    def test_pending_exit_is_executed_even_if_pair_is_removed_that_open(self):
        history, dates = profit_history()
        expected = self.simulate(history, None)
        self.assertEqual(self.simulate(history, pd.Series([True, False], index=dates[[120, 123]])), expected)

    def test_time_stop_survives_removal_and_no_new_entries_follow(self):
        dates = pd.bdate_range("2025-01-02", periods=180)
        a = pd.DataFrame({"Open": 100.0, "Close": 100.0}, index=dates)
        b = a.copy()
        a.loc[dates[120]:, "Close"] = 103.0
        a.loc[dates[121]:, "Open"] = 103.0
        history = {"AAPL": a, "MSFT": b}
        schedule = pd.Series([True, False], index=dates[[120, 125]])
        trades = self.simulate(history, schedule)
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["entryTime"], execution_time(dates[121]))
        self.assertEqual(trades[0]["exitTime"], execution_time(dates[166]))

    def test_removal_during_missing_pair_session_still_cancels_next_fill(self):
        history, dates = profit_history()
        history["AAPL"] = history["AAPL"].drop(dates[121])
        schedule = pd.Series([True, False], index=dates[[120, 121]])
        self.assertEqual(self.simulate(history, schedule), [])

    def test_empty_schedule_blocks_all_entries_and_invalid_schedule_is_rejected(self):
        history, dates = profit_history()
        self.assertEqual(self.simulate(history, pd.Series([], index=pd.DatetimeIndex([]), dtype=bool)), [])
        for schedule in (pd.Series([True, False], index=dates[[122, 120]]),
                         pd.Series([True, False], index=dates[[120, 120]]),
                         pd.Series([1], index=dates[[120]])):
            with self.assertRaises(ValueError):
                self.simulate(history, schedule)


if __name__ == "__main__":
    unittest.main()
