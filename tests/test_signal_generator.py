from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd

from research.pair_screener import ScreenedPair, ScreeningResult, universe_symbols


class SignalGeneratorTests(unittest.TestCase):
    def test_candidate_pool_matches_full_research_universe(self):
        from core.signal_generator import SignalGenerator

        self.assertEqual(SignalGenerator().candidate_pool, universe_symbols())
        self.assertEqual(len(SignalGenerator().candidate_pool), 60)

    def test_daily_matrix_reads_daily_close_rows_once(self):
        from core.signal_generator import SignalGenerator

        bars = pd.DataFrame({
            "date": ["2025-01-03", "2025-01-02", "2025-01-03"],
            "symbol": ["AAPL", "AAPL", "MSFT"],
            "close": [101.0, 100.0, 200.0],
        })
        connect = MagicMock()
        connect.return_value.__enter__.return_value = MagicMock()
        with patch("core.signal_generator.db.engine.connect", connect), \
             patch("core.signal_generator.pd.read_sql", return_value=bars) as read_sql:
            matrix = SignalGenerator()._build_price_matrix()

        self.assertEqual(list(matrix.columns), [symbol for symbol in universe_symbols() if symbol in {"AAPL", "MSFT"}])
        self.assertEqual(list(matrix.index), list(pd.DatetimeIndex(["2025-01-02", "2025-01-03"])))
        self.assertEqual(read_sql.call_args.kwargs["params"]["symbols"], universe_symbols())

    def test_scan_cache_returns_a_copy_until_forced_refresh(self):
        from core.signal_generator import SignalGenerator

        generator = SignalGenerator()
        first_result = [{"pair": "AAPL / MSFT", "z_score": 2.1}]
        refreshed_result = [{"pair": "NVDA / AMD", "z_score": -2.2}]
        with patch.object(generator, "_scan_instant_opportunities_uncached", side_effect=[first_result, refreshed_result]) as scan:
            self.assertEqual(generator.scan_instant_opportunities(), first_result)
            cached = generator.scan_instant_opportunities()
            cached[0]["pair"] = "MUTATED"
            self.assertEqual(generator.get_cached_opportunities(), first_result)
            self.assertEqual(generator.scan_instant_opportunities(force_refresh=True), refreshed_result)

        self.assertEqual(scan.call_count, 2)

    def test_scan_uses_research_screening_thresholds_before_computing_z_score(self):
        from core.signal_generator import SignalGenerator

        index = pd.bdate_range("2024-01-02", periods=252)
        matrix = pd.DataFrame({"AAPL": np.linspace(100, 110, len(index)), "MSFT": np.linspace(200, 205, len(index))}, index=index)
        selected = ScreenedPair("Technology / semiconductors", ("AAPL", "MSFT"), 0.01, 5.0, 0.3, len(index))
        screening = ScreeningResult((selected,), 1, 1, {}, "2024-01-02", "2025-01-01")
        model = SimpleNamespace(z_score=lambda *_prices: 2.5)
        generator = SignalGenerator()
        with patch.object(generator, "_build_price_matrix", return_value=matrix), \
             patch("core.signal_generator.scorer.score_companies"), \
             patch("core.signal_generator.scorer.get_score", return_value=50), \
             patch("core.signal_generator.screen_pairs", return_value=screening) as screen, \
             patch("core.signal_generator.PairTradingStrategy.fit", return_value=model):
            opportunities = generator.scan_instant_opportunities()

        config = screen.call_args.kwargs["config"]
        self.assertEqual((config.max_pvalue, config.max_half_life, config.max_hurst), (0.05, 20.0, 0.45))
        self.assertEqual(opportunities[0]["pair"], "AAPL / MSFT")
        self.assertEqual(opportunities[0]["z_score"], 2.5)
