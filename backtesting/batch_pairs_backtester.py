import itertools
import time

import numpy as np
import pandas as pd

from backtesting.pairs_backtester import pairs_backtester


class BatchPairsBacktester:
    def __init__(self):
        self.default_symbols = [
            "AAPL",
            "MSFT",
            "NVDA",
            "TSLA",
            "AMZN",
            "GOOGL",
            "META",
            "AMD",
            "NFLX",
            "INTC",
            "QCOM",
            "AVGO",
        ]

    def run(
        self,
        symbols: list[str] | None = None,
        debug: bool = True,
    ) -> dict:
        """
        Run the same backtest rules across all possible
        symbol pairs.

        Important:
        This is NOT parameter optimization.
        Every pair is tested using the exact same strategy rules.
        """

        if symbols is None:
            symbols = self.default_symbols

        pairs = list(
            itertools.combinations(
                symbols,
                2,
            )
        )

        total_pairs = len(pairs)

        if debug:
            print(
                f"\nStarting batch backtest "
                f"for {total_pairs} pairs.\n"
            )

        start_time = time.perf_counter()

        results = []
        errors = []

        for idx, (stock_a, stock_b) in enumerate(
            pairs,
            start=1,
        ):
            pair_name = (
                f"{stock_a}/{stock_b}"
            )

            pair_start = (
                time.perf_counter()
            )

            try:
                result = (
                    pairs_backtester.run(
                        stock_a,
                        stock_b,
                        debug=False,
                    )
                )

                metrics = result.get(
                    "metrics",
                    {}
                )

                pair_result = {
                    "pair":
                        pair_name,

                    "stock_a":
                        stock_a,

                    "stock_b":
                        stock_b,

                    "bars":
                        result.get(
                            "bars",
                            0,
                        ),

                    "start_date":
                        result.get(
                            "start_date"
                        ),

                    "end_date":
                        result.get(
                            "end_date"
                        ),

                    "initial_capital":
                        metrics.get(
                            "initial_capital",
                            np.nan,
                        ),

                    "final_equity":
                        metrics.get(
                            "final_equity",
                            np.nan,
                        ),

                    "total_return_pct":
                        metrics.get(
                            "total_return_pct",
                            np.nan,
                        ),

                    "number_of_trades":
                        metrics.get(
                            "number_of_trades",
                            0,
                        ),

                    "win_rate_pct":
                        metrics.get(
                            "win_rate_pct",
                            np.nan,
                        ),

                    "average_trade_return_pct":
                        metrics.get(
                            "average_trade_return_pct",
                            np.nan,
                        ),

                    "median_trade_return_pct":
                        metrics.get(
                            "median_trade_return_pct",
                            np.nan,
                        ),

                    "profit_factor":
                        metrics.get(
                            "profit_factor",
                            np.nan,
                        ),

                    "sharpe_ratio":
                        metrics.get(
                            "sharpe_ratio",
                            np.nan,
                        ),

                    "max_drawdown_pct":
                        metrics.get(
                            "max_drawdown_pct",
                            np.nan,
                        ),

                    "average_holding_days":
                        metrics.get(
                            "average_holding_days",
                            np.nan,
                        ),

                    "transaction_costs":
                        metrics.get(
                            "transaction_costs",
                            np.nan,
                        ),
                }

                results.append(
                    pair_result
                )

                pair_elapsed = (
                    time.perf_counter()
                    - pair_start
                )

                if debug:
                    print(
                        f"[{idx}/{total_pairs}] "
                        f"{pair_name:<12} | "
                        f"trades="
                        f"{pair_result['number_of_trades']:<3} | "
                        f"return="
                        f"{pair_result['total_return_pct']:>7.2f}% | "
                        f"sharpe="
                        f"{pair_result['sharpe_ratio']:>6.2f} | "
                        f"time="
                        f"{pair_elapsed:.1f}s"
                    )

            except Exception as exc:  # noqa: BLE001
                errors.append(
                    {
                        "pair":
                            pair_name,

                        "error":
                            str(exc),
                    }
                )

                if debug:
                    print(
                        f"[{idx}/{total_pairs}] "
                        f"{pair_name:<12} | "
                        f"ERROR: {exc}"
                    )

        elapsed = (
            time.perf_counter()
            - start_time
        )

        results_df = pd.DataFrame(
            results
        )

        errors_df = pd.DataFrame(
            errors
        )

        if not results_df.empty:
            results_df = (
                results_df
                .sort_values(
                    by=[
                        "sharpe_ratio",
                        "total_return_pct",
                    ],
                    ascending=[
                        False,
                        False,
                    ],
                )
                .reset_index(
                    drop=True
                )
            )

        summary = (
            self._build_summary(
                results_df,
                errors_df,
                elapsed,
            )
        )

        return {
            "results":
                results_df,

            "errors":
                errors_df,

            "summary":
                summary,
        }

    def _build_summary(
        self,
        results: pd.DataFrame,
        errors: pd.DataFrame,
        elapsed_seconds: float,
    ) -> dict:
        """
        Produce strategy-level statistics across all pairs.
        """

        if results.empty:
            return {
                "pairs_tested": 0,
                "pairs_failed": len(errors),
                "elapsed_seconds": round(
                    elapsed_seconds,
                    2,
                ),
            }

        traded_pairs = results[
            results[
                "number_of_trades"
            ] > 0
        ].copy()

        profitable_pairs = results[
            results[
                "total_return_pct"
            ] > 0
        ]

        losing_pairs = results[
            results[
                "total_return_pct"
            ] < 0
        ]

        total_trades = int(
            results[
                "number_of_trades"
            ].sum()
        )

        if not traded_pairs.empty:
            median_return = float(
                traded_pairs[
                    "total_return_pct"
                ].median()
            )

            mean_return = float(
                traded_pairs[
                    "total_return_pct"
                ].mean()
            )

            median_sharpe = float(
                traded_pairs[
                    "sharpe_ratio"
                ].median()
            )

            mean_sharpe = float(
                traded_pairs[
                    "sharpe_ratio"
                ].mean()
            )

            median_drawdown = float(
                traded_pairs[
                    "max_drawdown_pct"
                ].median()
            )

        else:
            median_return = 0.0
            mean_return = 0.0
            median_sharpe = 0.0
            mean_sharpe = 0.0
            median_drawdown = 0.0

        best_pair = None
        worst_pair = None

        if not traded_pairs.empty:
            best_row = (
                traded_pairs
                .sort_values(
                    "total_return_pct",
                    ascending=False,
                )
                .iloc[0]
            )

            worst_row = (
                traded_pairs
                .sort_values(
                    "total_return_pct",
                    ascending=True,
                )
                .iloc[0]
            )

            best_pair = {
                "pair":
                    best_row["pair"],

                "return_pct":
                    round(
                        float(
                            best_row[
                                "total_return_pct"
                            ]
                        ),
                        2,
                    ),

                "sharpe_ratio":
                    round(
                        float(
                            best_row[
                                "sharpe_ratio"
                            ]
                        ),
                        3,
                    ),

                "trades":
                    int(
                        best_row[
                            "number_of_trades"
                        ]
                    ),
            }

            worst_pair = {
                "pair":
                    worst_row["pair"],

                "return_pct":
                    round(
                        float(
                            worst_row[
                                "total_return_pct"
                            ]
                        ),
                        2,
                    ),

                "sharpe_ratio":
                    round(
                        float(
                            worst_row[
                                "sharpe_ratio"
                            ]
                        ),
                        3,
                    ),

                "trades":
                    int(
                        worst_row[
                            "number_of_trades"
                        ]
                    ),
            }

        return {
            "pairs_tested":
                len(results),

            "pairs_failed":
                len(errors),

            "pairs_with_trades":
                len(traded_pairs),

            "pairs_without_trades":
                int(
                    (
                        results[
                            "number_of_trades"
                        ] == 0
                    ).sum()
                ),

            "profitable_pairs":
                len(profitable_pairs),

            "losing_pairs":
                len(losing_pairs),

            "profitable_pair_pct":
                round(
                    (
                        len(profitable_pairs)
                        / len(results)
                        * 100
                    ),
                    2,
                ),

            "total_trades":
                total_trades,

            "median_pair_return_pct":
                round(
                    median_return,
                    2,
                ),

            "mean_pair_return_pct":
                round(
                    mean_return,
                    2,
                ),

            "median_sharpe_ratio":
                round(
                    median_sharpe,
                    3,
                ),

            "mean_sharpe_ratio":
                round(
                    mean_sharpe,
                    3,
                ),

            "median_max_drawdown_pct":
                round(
                    median_drawdown,
                    2,
                ),

            "best_pair":
                best_pair,

            "worst_pair":
                worst_pair,

            "elapsed_seconds":
                round(
                    elapsed_seconds,
                    2,
                ),

            "elapsed_minutes":
                round(
                    elapsed_seconds
                    / 60,
                    2,
                ),
        }


batch_pairs_backtester = (
    BatchPairsBacktester()
)