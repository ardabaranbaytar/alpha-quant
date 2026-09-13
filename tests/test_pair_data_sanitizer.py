from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from data_pipeline.data_sanitizer import SanitizerConfig, sanitize_daily_bars
from data_pipeline.yfinance_fetcher import DataFetcher


def bars(rows, volume=None):
    dates = pd.bdate_range("2025-01-02", periods=len(rows))
    frame = pd.DataFrame(rows, columns=["Open", "Close"], index=dates)
    if volume is not None:
        frame["Volume"] = volume
    return frame


class DataSanitizerTests(unittest.TestCase):
    def test_invalid_sanitizer_config_is_rejected(self):
        for kwargs in ({"max_daily_move": 0}, {"max_daily_move": -0.1}, {"max_daily_move": np.nan}, {"max_daily_move": np.inf}):
            with self.assertRaises(ValueError):
                SanitizerConfig(**kwargs)

    def test_rejects_unaligned_or_unsorted_index(self):
        frame = bars([[100, 100], [101, 101]])
        with self.assertRaises(ValueError):
            sanitize_daily_bars(frame.iloc[::-1])
        with self.assertRaises(ValueError):
            sanitize_daily_bars(frame.reset_index(drop=True))

    def test_drops_nan_zero_negative_and_infinite_prices_without_filling(self):
        frame = bars([[100, 100], [np.nan, 101], [102, 0], [103, -5], [104, np.inf], [105, 105]])
        cleaned, dropped = sanitize_daily_bars(frame)
        self.assertEqual(dropped["invalid_price"], 4)
        self.assertEqual(len(cleaned), 2)
        self.assertTrue(cleaned.index.isin(frame.index).all())
        self.assertTrue(np.isfinite(cleaned.to_numpy(dtype=float)).all())

    def test_drops_zero_and_missing_volume_sessions(self):
        frame = bars([[100, 100], [101, 101], [102, 102], [103, 103]], volume=[1000, 0, np.nan, 500])
        cleaned, dropped = sanitize_daily_bars(frame)
        self.assertEqual(dropped["zero_volume"], 2)
        self.assertEqual(list(cleaned.index), [frame.index[0], frame.index[3]])

    def test_volume_column_is_optional_and_never_required(self):
        frame = bars([[100, 100], [101, 101]])
        cleaned, dropped = sanitize_daily_bars(frame)
        self.assertEqual(dropped["zero_volume"], 0)
        self.assertEqual(len(cleaned), 2)

    def test_drops_single_day_bad_tick_but_keeps_ordinary_moves(self):
        # 103 -> 101 is a normal ~2% move; 101 -> 155 is a 53% one-day bad print.
        frame = bars([[100, 100], [103, 103], [101, 101], [155, 155], [102, 102]])
        cleaned, dropped = sanitize_daily_bars(frame, SanitizerConfig(max_daily_move=0.5))
        self.assertEqual(dropped["bad_tick"], 1)
        self.assertNotIn(frame.index[3], cleaned.index)
        self.assertEqual(len(cleaned), 4)

    def test_bad_tick_threshold_is_configurable(self):
        frame = bars([[100, 100], [130, 130], [100, 100]])
        _, strict = sanitize_daily_bars(frame, SanitizerConfig(max_daily_move=0.2))
        _, loose = sanitize_daily_bars(frame, SanitizerConfig(max_daily_move=0.5))
        self.assertGreater(strict["bad_tick"], loose["bad_tick"])

    def test_missing_columns_are_rejected(self):
        frame = pd.DataFrame({"Close": [100.0]}, index=pd.bdate_range("2025-01-02", periods=1))
        with self.assertRaises(ValueError):
            sanitize_daily_bars(frame)

    def test_download_daily_history_sanitizes_and_never_leaks_volume_column(self):
        dates = pd.bdate_range("2025-01-02", periods=6)
        raw = pd.DataFrame({
            "Open": [100.0, 101.0, np.nan, 101.0, 160.0, 103.0],
            "Close": [100.0, 101.0, 102.0, 101.0, 160.0, 103.0],
            "Volume": [1000, 1000, 1000, 0, 1000, 1000],
        }, index=dates)
        with patch("data_pipeline.yfinance_fetcher.yf.download", return_value=raw), \
             patch("data_pipeline.yfinance_fetcher.yf.set_tz_cache_location"):
            history = DataFetcher().download_daily_history(
                ["AAA"], dates[0].strftime("%Y-%m-%d"), (dates[-1] + pd.Timedelta(days=1)).strftime("%Y-%m-%d"), Path("."),
            )
        bars_out = history["AAA"]
        self.assertEqual(list(bars_out.columns), ["Open", "Close"])
        # Dropped: NaN open (row 2), zero volume (row 3), and the 160 bad tick (row 4).
        self.assertEqual(len(bars_out), 3)
        self.assertNotIn(dates[2], bars_out.index)
        self.assertNotIn(dates[3], bars_out.index)
        self.assertNotIn(dates[4], bars_out.index)


if __name__ == "__main__":
    unittest.main()
