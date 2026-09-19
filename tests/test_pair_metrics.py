import unittest

import numpy as np
import pandas as pd
from test_pair_portfolio import portfolio_history, run_portfolio

from research.pair_accounting import (
    cash_interest,
    close_time,
    execution_time,
    portfolio_metrics,
)
from research.portfolio import PortfolioConfig
from research.run_backtest import ExecutionConfig, RiskConfig


class PairCashYieldTests(unittest.TestCase):
    def test_interest_uses_actual_calendar_time_and_positive_cash_only(self):
        friday, monday = "2025-01-03T21:00:00Z", "2025-01-06T21:00:00Z"
        self.assertAlmostEqual(cash_interest(100000, 0.045, friday, monday), 100000 * 0.045 * 3 / 365)
        self.assertEqual(cash_interest(-100, 0.045, friday, monday), 0)
        self.assertEqual(cash_interest(100000, 0, friday, monday), 0)
        for args in ((100, -1, friday, monday), (np.nan, 0.045, friday, monday), (100, 0.045, monday, friday)):
            with self.assertRaises(ValueError):
                cash_interest(*args)

    def test_all_cash_compounds_at_close_and_has_no_excess_return(self):
        dates = pd.DatetimeIndex(["2025-01-03", "2025-01-06", "2025-01-08"])
        history = {"AAA": pd.DataFrame({"Open": 100.0, "Close": 100.0}, index=dates)}
        _, result = run_portfolio(history, [], risk_free_rate=0.045)
        expected, previous = 100000.0, execution_time(dates[0])
        for day in dates:
            closing = close_time(day)
            expected += cash_interest(expected, 0.045, previous, closing)
            previous = closing
        self.assertAlmostEqual(result.summary["finalEquity"], expected)
        self.assertAlmostEqual(result.summary["cashYield"], expected - 100000)
        self.assertAlmostEqual(result.summary["riskFreeBenchmarkPnl"], expected - 100000)
        self.assertAlmostEqual(result.summary["tradingPnl"], 0)
        self.assertEqual(result.summary["annualizedExcessReturnPct"], 0)
        self.assertIsNone(result.summary["sharpeRatio"])
        self.assertIsNone(result.summary["sortinoRatio"])
        self.assertIsNone(result.summary["calmarRatio"])
        self.assertEqual(result.summary["averageCapitalUtilizationPct"], 0)
        self.assertEqual(result.trades, [])
        self.assertTrue(all(e["time"] in [close_time(d) for d in dates] for e in result.events))

    def test_reserved_margin_receives_no_yield_and_released_cash_earns_only_forward(self):
        pair = ("AAA", "BBB")
        history, dates = portfolio_history([pair])
        _, result = run_portfolio(history, [pair], risk_free_rate=0.045)
        previous, expected, accrued = None, 0.0, 0.0
        for row in result.equity_curve:
            if previous is not None:
                accrued += cash_interest(previous["availableCash"], 0.045, previous["time"], row["time"])
            if row["phase"] == "close_mark":
                event = next(e for e in result.events if e["event"] == "cash_yield" and e["time"] == row["time"])
                self.assertAlmostEqual(event["amount"], accrued)
                expected += accrued
                accrued = 0.0
            previous = row
        self.assertAlmostEqual(result.summary["cashYield"], expected)
        exit_day = dates[35]
        prev_close = next(r for r in result.equity_curve if r["time"] == close_time(dates[34]))
        after_exit = next(r for r in result.equity_curve if r["time"] == execution_time(exit_day) and r["phase"] == "exit")
        correct = cash_interest(prev_close["availableCash"], 0.045, prev_close["time"], after_exit["time"])
        correct += cash_interest(after_exit["availableCash"], 0.045, after_exit["time"], close_time(exit_day))
        actual = next(e["amount"] for e in result.events if e["event"] == "cash_yield" and e["time"] == close_time(exit_day))
        self.assertAlmostEqual(actual, correct)
        self.assertLess(actual, cash_interest(after_exit["availableCash"], 0.045, prev_close["time"], close_time(exit_day)))

    def test_yield_is_separate_from_closed_trade_pnl_and_all_costs(self):
        pair = ("AAA", "BBB")
        history, _ = portfolio_history([pair])
        _, zero = run_portfolio(history, [pair])
        _, yielding = run_portfolio(history, [pair], risk_free_rate=0.045)
        self.assertEqual(zero.trades, yielding.trades)
        for key in ("commission", "slippage", "borrow", "realizedPnlUnrounded"):
            self.assertAlmostEqual(zero.summary[key], yielding.summary[key])
        summary = yielding.summary
        self.assertAlmostEqual(summary["portfolioPnl"], summary["realizedPnlUnrounded"] + summary["cashYield"], places=8)
        self.assertAlmostEqual(summary["openNetMarkedPnl"], 0, places=8)

    def test_future_close_cannot_change_accrual_before_that_close_or_past_cash(self):
        pair = ("AAA", "BBB")
        history, dates = portfolio_history([pair], exit_signal=None)
        _, before = run_portfolio(history, [pair], risk_free_rate=0.045)
        history["AAA"].loc[dates[32]:, "Close"] *= 2
        _, after = run_portfolio(history, [pair], risk_free_rate=0.045)
        cutoff = close_time(dates[32])
        self.assertEqual([r for r in before.equity_curve if r["time"] < cutoff], [r for r in after.equity_curve if r["time"] < cutoff])
        self.assertEqual([e for e in before.events if e["time"] == cutoff and e["event"] == "cash_yield"],
                         [e for e in after.events if e["time"] == cutoff and e["event"] == "cash_yield"])
        _, prefix = run_portfolio({s: b.iloc[:33] for s, b in history.items()}, [pair], risk_free_rate=0.045)
        self.assertEqual(prefix.equity_curve, [r for r in after.equity_curve if r["time"] <= cutoff])

    def test_negative_available_cash_gets_no_interest_even_with_positive_equity(self):
        pair = ("AAA", "BBB")
        history, dates = portfolio_history([pair], count=34, exit_signal=None)
        history["AAA"].loc[dates[32]:, "Open"] = 110
        history["AAA"].loc[dates[32]:, "Close"] = 110
        _, result = run_portfolio(history, [pair], PortfolioConfig(10020, 8, 1),
                                  costs=ExecutionConfig(commission_bps=0, slippage_bps=0, annual_borrow_rate=0),
                                  risk=RiskConfig(stop_z=100), risk_free_rate=0.045)
        at_close = next(r for r in result.equity_curve if r["time"] == close_time(dates[32]))
        self.assertLess(at_close["availableCash"], 0)
        self.assertGreater(at_close["equity"], 0)
        self.assertFalse(any(e["event"] == "cash_yield" and e["time"] == close_time(dates[33]) for e in result.events))


class PairRiskMetricTests(unittest.TestCase):
    def curve(self, returns):
        rows = [{"time": "2025-01-02T21:00:00Z", "phase": "initial", "equity": 100000.0, "grossExposure": 0}]
        for i, value in enumerate(returns, 1):
            rows.append({"time": (pd.Timestamp(rows[0]["time"]) + pd.Timedelta(days=i)).isoformat(),
                         "phase": "close_mark", "equity": rows[-1]["equity"] * (1 + value), "grossExposure": 10000.0})
        return rows

    def test_hand_calculated_sharpe_sortino_calmar_and_utilization(self):
        returns = np.array([0.01, -0.02, 0.015, -0.005])
        curve = self.curve(returns)
        result = portfolio_metrics(curve, 0.045, 0.02)
        excess = returns - 0.045 / 365
        numerator = excess.mean() * 252
        downside = np.sqrt(np.mean(np.minimum(excess, 0) ** 2) * 252)
        self.assertAlmostEqual(result["sharpeRatio"], numerator / (excess.std(ddof=1) * np.sqrt(252)))
        self.assertAlmostEqual(result["sortinoRatio"], numerator / downside)
        cagr = (curve[-1]["equity"] / 100000) ** (365 / 4) - 1
        self.assertAlmostEqual(result["calmarRatio"], cagr / 0.02)
        self.assertAlmostEqual(result["averageCapitalUtilizationPct"], np.mean([10000 / r["equity"] for r in curve[1:]]) * 100)

    def test_intraday_events_do_not_inflate_daily_sample_or_ratios(self):
        curve = self.curve([0.001, -0.002, 0.003])
        expected = portfolio_metrics(curve, 0.045, 0.02)
        curve.insert(2, {"phase": "entry", "time": curve[1]["time"], "equity": 90000, "grossExposure": 90000})
        self.assertEqual(portfolio_metrics(curve, 0.045, 0.02), expected)
        self.assertEqual(expected["dailyObservations"], 3)

    def test_zero_volatility_no_downside_and_insolvency_are_explicitly_undefined(self):
        constant = portfolio_metrics(self.curve([0, 0, 0]), 0, 0)
        self.assertIsNone(constant["sharpeRatio"])
        self.assertIsNone(constant["sortinoRatio"])
        self.assertIsNone(constant["calmarRatio"])
        positive = portfolio_metrics(self.curve([0.001, 0.002]), 0, 0)
        self.assertGreater(positive["sharpeRatio"], 0)
        self.assertIsNone(positive["sortinoRatio"])
        insolvent = portfolio_metrics(self.curve([-1.1, 0.1]), 0.045, 1.1)
        self.assertIsNone(insolvent["annualizedReturnPct"])
        self.assertIsNone(insolvent["sharpeRatio"])
        self.assertIsNone(insolvent["averageCapitalUtilizationPct"])

    def test_invalid_daily_timestamps_and_values_are_rejected(self):
        curve = self.curve([0, 0])
        curve[-1]["time"] = curve[-2]["time"]
        with self.assertRaises(ValueError):
            portfolio_metrics(curve, 0.045, 0)
        curve = self.curve([0, 0])
        curve[-1]["equity"] = np.nan
        with self.assertRaises(ValueError):
            portfolio_metrics(curve, 0.045, 0)


if __name__ == "__main__":
    unittest.main()
