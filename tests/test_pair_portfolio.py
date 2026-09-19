import unittest
from dataclasses import replace
from unittest.mock import patch

import numpy as np
import pandas as pd

from research.pair_accounting import close_time, execution_time
from research.portfolio import PortfolioConfig, PortfolioEngine
from research.run_backtest import (
    ExecutionConfig,
    RiskConfig,
    VolatilitySizingConfig,
    main,
    simulate_pair,
)
from strategies.kalman_pair import KalmanObservation, KalmanPairStrategy
from strategies.mean_reversion import EntryFilterConfig
from strategies.pair_trading import PairTradingConfig, PairTradingStrategy, SpreadModel


class FixedStrategy(PairTradingStrategy):
    def __init__(self):
        super().__init__(PairTradingConfig(window=30))

    def fit(self, a, b):
        return SpreadModel(0, 1, 0, 1, 0.01)


def portfolio_history(pairs, count=40, signal=30, exit_signal=34):
    dates = pd.bdate_range("2025-01-02", periods=count)
    history = {s: pd.DataFrame({"Open": 100.0, "Close": 100.0}, index=dates)
               for pair in pairs for s in pair}
    for a, _ in pairs:
        history[a].loc[dates[signal]:, "Close"] = 103.0
        history[a].loc[dates[signal + 1]:, "Open"] = 103.0
        if exit_signal is not None and exit_signal < count:
            history[a].loc[dates[exit_signal]:, "Close"] = 99.0
            if exit_signal + 1 < count:
                history[a].loc[dates[exit_signal + 1]:, "Open"] = 100.0
    return history, dates


def run_portfolio(history, pairs, config=None, schedules=None, filters=None, costs=None, risk=None, strategies=None,
                  risk_free_rate=0.0, sector_of=None):
    # Isolate the original trading-ledger regressions from the separately tested cash yield.
    config = replace(config or PortfolioConfig(), risk_free_rate=risk_free_rate)
    engine = PortfolioEngine(history, strategies or {p: FixedStrategy() for p in pairs},
                             costs or ExecutionConfig(), risk or RiskConfig(), VolatilitySizingConfig(),
                             config, filters, entry_schedules=schedules, sector_of=sector_of)
    return engine, engine.run()


class PairPortfolioTests(unittest.TestCase):
    def test_default_configuration_and_invalid_cli_parameters(self):
        config = PortfolioConfig()
        self.assertEqual((config.initial_equity, config.max_concurrent_pairs, config.max_ticker_fraction), (100000, 8, 0.35))
        self.assertEqual(config.risk_free_rate, 0.045)
        self.assertEqual(config.max_sector_fraction, 0.40)
        for kwargs in ({"initial_equity": 0}, {"initial_equity": np.nan}, {"initial_equity": np.inf},
                       {"max_concurrent_pairs": 0}, {"max_concurrent_pairs": 1.5},
                       {"max_ticker_fraction": 0}, {"max_ticker_fraction": 1.1},
                       {"risk_free_rate": -0.01}, {"risk_free_rate": np.nan}, {"risk_free_rate": np.inf},
                       {"max_sector_fraction": 0}, {"max_sector_fraction": 1.1}, {"max_sector_fraction": np.nan}):
            with self.assertRaises(ValueError):
                PortfolioConfig(**kwargs)
        for argv in (["--initial-equity", "nan"], ["--initial-equity", "-100"],
                     ["--max-concurrent-pairs", "0"], ["--max-ticker-fraction", "2"],
                     ["--risk-free-rate", "-0.1"], ["--risk-free-rate", "nan"],
                     ["--max-sector-fraction", "0"], ["--max-sector-fraction", "2"]):
            with patch("research.run_backtest.download_universe") as download, patch("sys.stderr"):
                with self.assertRaises(SystemExit):
                    main(argv)
                download.assert_not_called()

    def test_sector_cap_blocks_same_sector_concentration_even_with_spare_cash_and_loose_ticker_cap(self):
        pairs = [("AAA", "BBB"), ("CCC", "DDD")]
        history, _ = portfolio_history(pairs)
        sector_of = {"AAA": "Tech", "BBB": "Tech", "CCC": "Tech", "DDD": "Tech"}
        config = PortfolioConfig(60000, 8, 1)
        self.assertEqual(config.max_sector_fraction, 0.40)
        _, result = run_portfolio(history, pairs, config, sector_of=sector_of)
        entries = [e for e in result.events if e["event"] == "entry"]
        self.assertEqual(len(entries), 1)
        self.assertGreater(result.summary["rejectedEntries"]["sector_cap"], 0)
        self.assertTrue(any(e["event"] == "rejected" and e["pair"] == ["CCC", "DDD"] and e["reason"] == "sector_cap"
                            for e in result.events))

    def test_sector_cap_does_not_block_pairs_in_different_sectors(self):
        pairs = [("AAA", "BBB"), ("CCC", "DDD")]
        history, _ = portfolio_history(pairs)
        sector_of = {"AAA": "Tech", "BBB": "Tech", "CCC": "Energy", "DDD": "Energy"}
        config = PortfolioConfig(60000, 8, 1)
        _, result = run_portfolio(history, pairs, config, sector_of=sector_of)
        entries = [e for e in result.events if e["event"] == "entry"]
        self.assertEqual(len(entries), 2)
        self.assertNotIn("sector_cap", result.summary["rejectedEntries"])

    def test_sector_cap_disabled_without_sector_map(self):
        pairs = [("AAA", "BBB"), ("CCC", "DDD")]
        history, _ = portfolio_history(pairs)
        sector_of = {"AAA": "Tech", "BBB": "Tech", "CCC": "Tech", "DDD": "Tech"}
        config = PortfolioConfig(60000, 8, 1)
        _, with_map = run_portfolio(history, pairs, config, sector_of=sector_of)
        _, without_map = run_portfolio(history, pairs, config)
        self.assertEqual(len([e for e in with_map.events if e["event"] == "entry"]), 1)
        self.assertEqual(len([e for e in without_map.events if e["event"] == "entry"]), 2)
        self.assertNotIn("sector_cap", without_map.summary["rejectedEntries"])

    def test_sector_of_must_be_a_mapping(self):
        pair = ("AAA", "BBB")
        history, _ = portfolio_history([pair])
        with self.assertRaises(ValueError):
            run_portfolio(history, [pair], sector_of=["AAA", "Tech"])

    def test_nine_simultaneous_signals_admit_only_eight_positions(self):
        pairs = [(f"{s}AX", f"{s}AY") for s in "ABCDEFGHI"]
        history, _ = portfolio_history(pairs)
        _, result = run_portfolio(history, pairs, PortfolioConfig(250000, 8, 1))
        self.assertEqual(result.summary["peakConcurrentPositions"], 8)
        self.assertEqual(len(result.trades), 8)
        self.assertGreater(result.summary["rejectedEntries"]["concurrency"], 0)
        self.assertTrue(all(row["openPositions"] <= 8 for row in result.equity_curve))

    def test_shared_cash_resizes_second_pair_and_cannot_reuse_short_proceeds(self):
        pairs = [("AAA", "AAB"), ("BBB", "BBC"), ("CCC", "CCD")]
        history, _ = portfolio_history(pairs)
        _, result = run_portfolio(history, pairs, PortfolioConfig(40000, 8, 1))
        entries = [e for e in result.events if e["event"] == "entry"]
        self.assertEqual(len(entries), 2)
        self.assertFalse(entries[0]["cashResized"])
        self.assertTrue(entries[1]["cashResized"])
        self.assertGreaterEqual(entries[1]["grossNotional"], 10000)
        self.assertLess(entries[1]["grossNotional"], entries[0]["grossNotional"])
        entry_rows = [r for r in result.equity_curve if r["phase"] == "entry"]
        self.assertTrue(all(r["availableCash"] >= -1e-8 for r in entry_rows))
        self.assertTrue(all(r["reservedMargin"] <= 40000 for r in entry_rows))
        self.assertGreater(result.summary["rejectedEntries"]["cash"], 0)

    def test_minimum_size_includes_entry_fees_and_slippage_in_cash_budget(self):
        pair = ("AAA", "BBB")
        history, _ = portfolio_history([pair])
        for equity in (9000, 10000):
            _, result = run_portfolio(history, [pair], PortfolioConfig(equity, 8, 1))
            self.assertEqual(result.trades, [])
            self.assertEqual(result.summary["peakConcurrentPositions"], 0)
        _, result = run_portfolio(history, [pair], PortfolioConfig(10020, 8, 1))
        self.assertEqual(result.summary["peakConcurrentPositions"], 1)

    def test_single_ticker_cap_blocks_concentration_even_with_spare_cash(self):
        pairs = [("SHR", "AAA"), ("SHR", "BBB")]
        history, _ = portfolio_history(pairs)
        _, result = run_portfolio(history, pairs, PortfolioConfig(60000, 8, 0.25))
        entries = [e for e in result.events if e["event"] == "entry"]
        self.assertEqual(len(entries), 1)
        self.assertGreater(result.summary["rejectedEntries"]["ticker_cap"], 0)

    def test_opposite_ticker_legs_are_gross_not_netted(self):
        pairs = [("SHR", "AAA"), ("SHR", "BBB")]
        history, _ = portfolio_history(pairs)
        history["BBB"][:] = 106.0
        _, result = run_portfolio(history, pairs, PortfolioConfig(60000, 8, 0.25))
        self.assertEqual(result.summary["peakConcurrentPositions"], 1)
        self.assertTrue(any(e["event"] == "rejected" and e["pair"] == ["SHR", "BBB"]
                            and e["reason"] == "ticker_cap" for e in result.events))

    def test_cap_uses_current_open_marks_and_post_cost_equity_not_future_close(self):
        pairs = [("SHR", "AAA"), ("SHR", "BBB")]
        history, dates = portfolio_history(pairs)
        schedules = {pairs[0]: pd.Series([True], index=dates[[30]]), pairs[1]: pd.Series([True], index=dates[[32]])}
        history["SHR"].loc[dates[33], "Open"] = 200.0
        _, before = run_portfolio(history, pairs, PortfolioConfig(60000, 8, 0.25), schedules)
        opening = execution_time(dates[33])
        decisions = [e for e in before.events if e["time"] == opening]
        self.assertTrue(any(e.get("reason") == "ticker_cap" for e in decisions))
        history["SHR"].loc[dates[33], "Close"] = 50.0
        _, after = run_portfolio(history, pairs, PortfolioConfig(60000, 8, 0.25), schedules)
        self.assertEqual(decisions, [e for e in after.events if e["time"] == opening])

    def test_exits_release_capacity_and_margin_before_same_open_entries(self):
        first, second = ("ZZZ", "ZZB"), ("AAA", "AAB")
        history, dates = portfolio_history([first], exit_signal=32)
        other, _ = portfolio_history([second], signal=32, exit_signal=35)
        history.update(other)
        _, result = run_portfolio(history, [first, second], PortfolioConfig(25000, 1, 1))
        events = [e for e in result.events if e["time"] == execution_time(dates[33])]
        self.assertEqual([e["event"] for e in events], ["exit", "entry"])
        self.assertEqual(events[1]["pair"], list(second))
        self.assertEqual(result.summary["peakConcurrentPositions"], 1)
        self.assertEqual(len(result.trades), 2)

    def test_existing_cap_breach_blocks_even_an_unrelated_new_pair(self):
        first, second = ("SHR", "AAA"), ("BBB", "BBC")
        history, dates = portfolio_history([first])
        other, _ = portfolio_history([second], signal=32, exit_signal=35)
        history.update(other)
        history["SHR"].loc[dates[33], "Open"] = 200.0
        _, result = run_portfolio(history, [first, second], PortfolioConfig(60000, 8, 0.25))
        events = [e for e in result.events if e["time"] == execution_time(dates[33])]
        self.assertTrue(any(e.get("reason") == "ticker_cap" and e["pair"] == list(second) for e in events))

    def test_input_order_cannot_change_simultaneous_allocation(self):
        pairs = [("AAA", "AAB"), ("BBB", "BBC"), ("CCC", "CCD")]
        history, _ = portfolio_history(pairs)
        config = PortfolioConfig(40000, 1, 1)
        _, before = run_portfolio(history, pairs, config)
        _, after = run_portfolio(dict(reversed(list(history.items()))), list(reversed(pairs)), config)
        self.assertEqual(before.events, after.events)
        self.assertEqual(before.equity_curve, after.equity_curve)

    def test_single_pair_matches_legacy_net_accounting_and_settles_without_double_costs(self):
        pair = ("AAA", "BBB")
        for direction in (-1, 1):
            history, _ = portfolio_history([pair])
            if direction == -1:
                history["AAA"] = 200 - history["AAA"]
            legacy, _ = simulate_pair(pair, history, FixedStrategy(), sizing=VolatilitySizingConfig())
            engine, result = run_portfolio(history, [pair])
            self.assertEqual(result.trades, legacy)
            self.assertAlmostEqual(result.summary["portfolioPnl"], result.summary["realizedPnlUnrounded"], places=8)
            self.assertAlmostEqual(engine.cash, 100000 + result.events[-1]["netPnl"], places=8)
            self.assertEqual(engine.reserved, 0)

    def test_time_stop_keeps_original_45_session_clock_after_deselection(self):
        pair = ("AAA", "BBB")
        history, dates = portfolio_history([pair], count=80, exit_signal=None)
        schedules = {pair: pd.Series([True, False], index=dates[[30, 32]])}
        _, result = run_portfolio(history, [pair], schedules=schedules)
        self.assertEqual(len(result.trades), 1)
        self.assertEqual(result.trades[0]["entryTime"], execution_time(dates[31]))
        self.assertEqual(result.trades[0]["exitTime"], execution_time(dates[76]))
        _, truncated = run_portfolio({s: b.iloc[:76] for s, b in history.items()}, [pair], schedules=schedules)
        self.assertEqual(truncated.trades, [])

    def test_kalman_portfolio_path_matches_single_pair_reference(self):
        pair = ("AAA", "BBB")
        history, _ = portfolio_history([pair], count=125, signal=120, exit_signal=None)
        observations = [KalmanObservation(0, 1, 0.01 * np.sin(i), None, None, False) for i in range(125)]
        observations[120] = KalmanObservation(0, 1, 999, 3, 5, True)
        observations[122] = KalmanObservation(0, 1, 999, 0, None, False)
        with patch.object(KalmanPairStrategy, "analyze", return_value=observations):
            legacy, _ = simulate_pair(pair, history, KalmanPairStrategy(), sizing=VolatilitySizingConfig())
            _, result = run_portfolio(history, [pair], strategies={pair: KalmanPairStrategy()})
        self.assertEqual(result.trades, legacy)

    def test_removed_pair_keeps_trade_but_pending_new_entry_is_canceled(self):
        pair = ("AAA", "BBB")
        history, dates = portfolio_history([pair])
        schedule = {pair: pd.Series([True, False], index=dates[[30, 32]])}
        _, result = run_portfolio(history, [pair], schedules=schedule)
        self.assertEqual(result.trades[0]["entryTime"], execution_time(dates[31]))
        self.assertEqual(result.trades[0]["exitTime"], execution_time(dates[35]))
        schedule[pair] = pd.Series([True, False], index=dates[[30, 31]])
        _, result = run_portfolio(history, [pair], schedules=schedule)
        self.assertEqual(result.trades, [])
        self.assertGreater(result.summary["rejectedEntries"]["inactive"], 0)

    def test_hurst_and_cost_aware_early_profit_are_preserved(self):
        pair = ("AAA", "BBB")
        history, dates = portfolio_history([pair], count=125, signal=120, exit_signal=None)
        history["AAA"].loc[dates[122]:, "Close"] = 100.5
        history["AAA"].loc[dates[123]:, "Open"] = 101.0
        for hurst, count in ((0.45, 0), (0.3, 1)):
            with patch("research.portfolio.estimate_hurst", return_value=hurst) as estimator:
                _, result = run_portfolio(history, [pair], filters=EntryFilterConfig())
            self.assertEqual(len(result.trades), count)
            self.assertTrue((np.asarray(estimator.call_args_list[0].args[0]) == 0).all())
        self.assertEqual(result.trades[0]["exitTime"], execution_time(dates[123]))
        self.assertGreater(result.trades[0]["netPnl"], 0)
        history["AAA"].loc[dates[121], "Open"] = 100.6
        with patch("research.portfolio.estimate_hurst", return_value=0.3):
            _, result = run_portfolio(history, [pair], filters=EntryFilterConfig())
        self.assertEqual(result.trades, [])

    def test_borrow_accrues_through_missing_quotes_and_calendar_gaps(self):
        pair = ("AAA", "BBB")
        history, dates = portfolio_history([pair])
        history["AAA"] = history["AAA"].drop(dates[32])
        _, result = run_portfolio(history, [pair])
        entry = next(e for e in result.events if e["event"] == "entry")
        short = abs(entry["quantities"][0]) * 103 * 0.9995
        days = (pd.Timestamp(execution_time(dates[35])) - pd.Timestamp(entry["time"])).total_seconds() / 86400
        self.assertAlmostEqual(result.summary["borrow"], short * 0.03 * days / 365)
        previous = next(r for r in result.equity_curve if r["time"] == close_time(dates[31]))
        missing = next(r for r in result.equity_curve if r["time"] == close_time(dates[32]))
        gap_days = (dates[32] - dates[31]).days
        self.assertAlmostEqual(previous["equity"] - missing["equity"], short * 0.03 * gap_days / 365)

    def test_missing_open_defers_fill_without_filling_at_a_stale_quote(self):
        pair = ("AAA", "BBB")
        history, dates = portfolio_history([pair])
        history["AAA"] = history["AAA"].drop(dates[31])
        _, result = run_portfolio(history, [pair])
        self.assertEqual(result.trades[0]["entryTime"], execution_time(dates[32]))

    def test_open_positions_affect_equity_and_costs_but_never_closed_export(self):
        pair = ("AAA", "BBB")
        history, _ = portfolio_history([pair], count=34, exit_signal=None)
        _, result = run_portfolio(history, [pair])
        summary = result.summary
        self.assertEqual(result.trades, [])
        self.assertEqual(summary["openPositions"], 1)
        self.assertAlmostEqual(summary["portfolioPnl"], -summary["commission"] - summary["slippage"] - summary["borrow"], places=8)
        self.assertGreater(summary["maxDrawdown"], 0)
        self.assertAlmostEqual(summary["finalEquity"], summary["availableCash"] + summary["reservedMargin"])

    def test_portfolio_drawdown_includes_open_losses_before_profitable_exit(self):
        pair = ("AAA", "BBB")
        history, dates = portfolio_history([pair])
        history["AAA"].loc[dates[32], "Close"] = 106.0
        history["AAA"].loc[dates[33], "Open"] = 102.0
        history["AAA"].loc[dates[33], "Close"] = 99.0
        _, result = run_portfolio(history, [pair])
        self.assertGreater(result.trades[0]["netPnl"], 0)
        self.assertGreater(result.summary["maxDrawdown"], 150)
        self.assertGreater(result.summary["maxDrawdownPct"], 0.15)

    def test_future_prices_cannot_change_past_cash_equity_or_allocations(self):
        pairs = [("AAA", "AAB"), ("BBB", "BBC")]
        history, dates = portfolio_history(pairs)
        config = PortfolioConfig(20000, 8, 1)
        _, before = run_portfolio(history, pairs, config)
        prefix = {s: b.iloc[:34] for s, b in history.items()}
        _, short = run_portfolio(prefix, pairs, config)
        cutoff = close_time(dates[33])
        self.assertEqual(short.events, [e for e in before.events if e["time"] <= cutoff])
        self.assertEqual(short.equity_curve, [e for e in before.equity_curve if e["time"] <= cutoff])
        for bars in history.values():
            bars.loc[dates[34]:] *= 3
        _, after = run_portfolio(history, pairs, config)
        self.assertEqual(short.equity_curve, [e for e in after.equity_curve if e["time"] <= cutoff])

    def test_empty_selection_leaves_cash_unchanged_and_engine_cannot_be_reused(self):
        history, _ = portfolio_history([("AAA", "BBB")])
        engine, result = run_portfolio(history, [], strategies={})
        self.assertEqual(result.summary["finalEquity"], 100000)
        self.assertEqual(result.summary["peakConcurrentPositions"], 0)
        self.assertEqual(result.summary["portfolioReturnPct"], 0)
        with self.assertRaises(RuntimeError):
            engine.run()


if __name__ == "__main__":
    unittest.main()
