import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from scripts.seed_daily_bars_from_csv import iter_csv_rows, seed_daily_bars


class SeedDailyBarsFromCsvTests(unittest.TestCase):
    def test_csv_rows_preserve_v_and_ma_and_map_nullable_columns(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            path = Path(directory)
            for symbol in ("AAPL", "V", "MA"):
                (path / f"{symbol}.csv").write_text(
                    "Date,Open,Close\n2025-01-02,100.5,101.25\n", encoding="utf-8"
                )
            rows = list(iter_csv_rows(path))

        self.assertEqual([row["symbol"] for row in rows], ["AAPL", "MA", "V"])
        self.assertEqual(rows[0]["open"], 100.5)
        self.assertEqual(rows[0]["close"], 101.25)
        self.assertEqual(rows[0]["adj_close"], 101.25)
        self.assertIsNone(rows[0]["high"])
        self.assertIsNone(rows[0]["low"])
        self.assertIsNone(rows[0]["volume"])

    def test_seed_uses_batched_upserts(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            path = Path(directory)
            (path / "AAPL.csv").write_text(
                "Date,Open,Close\n2025-01-02,100,101\n2025-01-03,102,103\n2025-01-06,104,105\n",
                encoding="utf-8",
            )
            begin = MagicMock()
            connection = begin.return_value.__enter__.return_value
            with patch("scripts.seed_daily_bars_from_csv.db.engine.begin", begin):
                self.assertEqual(seed_daily_bars(path, batch_size=2), 3)

        self.assertEqual(connection.execute.call_count, 2)
        first_query, first_batch = connection.execute.call_args_list[0].args
        self.assertIn("ON DUPLICATE KEY UPDATE", str(first_query))
        self.assertEqual(len(first_batch), 2)

