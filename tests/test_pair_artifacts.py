from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from research.compare_pair_models import compare_results
from research.pair_artifacts import load_available_history, load_history, save_history
from research.run_backtest import main


class PairArtifactTests(unittest.TestCase):
    def history(self):
        dates = pd.bdate_range("2025-01-02", periods=4)
        return {"AAA": pd.DataFrame({"Open": [np.pi, 100.12345678901234, 52.0, 4.9], "Close": [1.23456789012345, 2.1, 5.8, 6.1]}, index=dates)}

    def test_price_snapshot_round_trip_is_exact_and_checks_requested_universe_and_dates(self):
        history = self.history()
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            directory = Path(folder)
            save_history(history, directory, "2025-01-01", "2025-02-01")
            restored, hashes = load_history(directory, ["AAA"], "2025-01-01", "2025-02-01")
            np.testing.assert_array_equal(restored["AAA"].to_numpy(), history["AAA"].to_numpy())
            self.assertEqual(len(hashes["AAA"]), 64)
            for symbols, start, end in ((["BBB"], "2025-01-01", "2025-02-01"),
                                        (["AAA"], "2024-01-01", "2025-02-01"),
                                        (["AAA"], "2025-01-01", "2025-02-02")):
                with self.assertRaises(ValueError):
                    load_history(directory, symbols, start, end)
            # Simulate corruption without modifying the user's real artifacts.
            with patch.object(Path, "read_bytes", return_value=b"corrupted"):
                with self.assertRaisesRegex(ValueError, "hash mismatch"):
                    load_history(directory, ["AAA"], "2025-01-01", "2025-02-01")

    def test_hash_valid_but_invalid_prices_are_not_accepted(self):
        for invalid in ("nan", "negative", "outside", "duplicate"):
            history = self.history()
            if invalid == "nan":
                history["AAA"].iloc[0, 0] = np.nan
            elif invalid == "negative":
                history["AAA"].iloc[0, 0] = -1
            elif invalid == "outside":
                history["AAA"].index -= pd.Timedelta(days=365)
            else:
                history["AAA"].index = pd.DatetimeIndex(["2025-01-02"] * 4)
            with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
                directory = Path(folder)
                save_history(history, directory, "2025-01-01", "2025-02-01")
                with self.assertRaises(ValueError):
                    load_history(directory, ["AAA"], "2025-01-01", "2025-02-01")

    def test_available_local_history_uses_the_shared_observed_range_without_manifest_matching(self):
        dates_a = pd.bdate_range("2025-01-02", periods=5)
        dates_b = pd.bdate_range("2025-01-03", periods=5)
        history = {
            "AAA": pd.DataFrame({"Open": 100.0, "Close": 101.0}, index=dates_a),
            "BBB": pd.DataFrame({"Open": 200.0, "Close": 201.0}, index=dates_b),
        }
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            directory = Path(folder)
            save_history(history, directory, "2020-01-01", "2020-02-01")
            restored, hashes, first, end = load_available_history(directory, ["AAA", "BBB"])

        self.assertEqual(first, dates_b[0])
        self.assertEqual(end, dates_a[-1] + pd.Timedelta(days=1))
        self.assertEqual(restored["AAA"].index.min(), dates_b[0])
        self.assertEqual(restored["BBB"].index.max(), dates_a[-1])
        self.assertEqual(set(hashes), {"AAA", "BBB"})

    def test_reuse_cli_does_not_download_or_touch_network(self):
        with patch("research.run_backtest.download_universe") as download, patch("research.run_backtest.load_available_history", side_effect=RuntimeError("saved prices")) as load:
            with self.assertRaisesRegex(RuntimeError, "saved prices"):
                main(["--end", "2026-01-01", "--reuse-data", "--no-publish"])
            download.assert_not_called()
            self.assertEqual(len(load.call_args.args[1]), 60)


class PairComparisonTests(unittest.TestCase):
    def bundles(self):
        audit = {"requestedStart": "2021-01-01", "requestedEndExclusive": "2026-01-01",
                 "firstSession": "2021-01-04", "lastSession": "2025-12-31", "evaluationStart": "2022-01-04",
                 "dataSha256": {"AAA": "a" * 64}, "sourceSha256": {"source": "b" * 64}, "packages": {"numpy": "test"},
                 "execution": {"commission_bps": 10}, "positionSizing": {"target_spread_risk": 50},
                 "portfolioConfig": {"initial_equity": 100000, "risk_free_rate": 0.045},
                 "riskManagement": {"stop_z": 3.5}, "entryFilters": {"max_hurst": 0.45},
                 "selection": "manual", "pairs": [["AAA", "BBB"]], "providerSymbolAliases": {}}
        results = {}
        for model, sharpe, pnl in (("ols", -0.1, 1000), ("kalman", -0.2, 2000)):
            summary = {"sharpeRatio": sharpe, "portfolioPnl": pnl, "maxDrawdownPct": 0.5, "closedTrades": 0}
            results[model] = {"audit": {**deepcopy(audit), "model": model, "portfolio": summary},
                              "metadata": {"model": model}, "snapshot": {"trades": []},
                              "portfolioReport": {"summary": summary}}
        return results

    def test_winner_is_predeclared_sharpe_not_highest_absolute_pnl(self):
        report = compare_results(self.bundles())
        self.assertEqual(report["winner"], "ols")
        self.assertIn("Retrospective", report["selectionRule"])

    def test_ties_use_drawdown_then_ols_and_undefined_sharpe_ranks_last(self):
        results = self.bundles()
        results["kalman"]["audit"]["portfolio"]["sharpeRatio"] = -0.1
        self.assertEqual(compare_results(results)["winner"], "ols")
        results["kalman"]["audit"]["portfolio"]["maxDrawdownPct"] = 0.4
        self.assertEqual(compare_results(results)["winner"], "kalman")
        results["kalman"]["audit"]["portfolio"]["sharpeRatio"] = None
        self.assertEqual(compare_results(results)["winner"], "ols")
        results["ols"]["audit"]["portfolio"]["sharpeRatio"] = None
        self.assertEqual(compare_results(results)["winner"], "kalman")

    def test_changed_prices_code_configuration_or_screening_cannot_be_compared(self):
        for key in ("dataSha256", "sourceSha256", "packages", "portfolioConfig", "requestedEndExclusive",
                    "execution", "positionSizing", "screening", "riskManagement", "entryFilters"):
            results = self.bundles()
            results["kalman"]["audit"][key] = "different"
            with self.assertRaisesRegex(ValueError, "Incomparable"):
                compare_results(results)

    def test_missing_inputs_or_model_and_summary_mismatch_are_rejected(self):
        mutations = [lambda r: r.pop("ols"),
                     lambda r: r["kalman"]["audit"].pop("dataSha256"),
                     lambda r: r["kalman"]["metadata"].update(model="ols"),
                     lambda r: r["kalman"]["portfolioReport"].update(summary={}),
                     lambda r: r["kalman"]["snapshot"].update(trades=[{}]),
                     lambda r: r["kalman"]["audit"]["portfolio"].update(sharpeRatio=np.nan)]
        for mutate in mutations:
            results = self.bundles()
            mutate(results)
            with self.assertRaises(ValueError):
                compare_results(results)


if __name__ == "__main__":
    unittest.main()
