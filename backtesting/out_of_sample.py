from dataclasses import dataclass
from itertools import combinations

import numpy as np
import pandas as pd

from backtesting.pairs_backtester import (
    PairsBacktestConfig,
    PairsBacktester,
)


@dataclass(frozen=True)
class OOSConfig:
    research_start: str = "2016-08-22"
    research_end: str = "2021-12-31"

    validation_start: str = "2022-01-01"
    validation_end: str = "2023-12-31"

    test_start: str = "2024-01-01"
    test_end: str = "2026-08-20"

    minimum_validation_trades: int = 3
    minimum_validation_return_pct: float = 0.0
    minimum_validation_sharpe: float = 0.0


class OutOfSampleTester:
    def __init__(
        self,
        config: OOSConfig | None = None,
    ):
        self.config = config or OOSConfig()

    # =========================================================
    # BACKTESTER
    # =========================================================

    @staticmethod
    def _create_backtester():
        return PairsBacktester(
            config=PairsBacktestConfig()
        )

    # =========================================================
    # CLEAN PERIOD BACKTEST
    # =========================================================

    def _run_period(
        self,
        stock_a: str,
        stock_b: str,
        start_date: str,
        end_date: str,
        debug: bool = False,
    ) -> dict:
        """
        Run a clean evaluation period.

        Important rules:

        - History before start_date MAY be used for model fitting.
        - Portfolio starts FLAT at start_date.
        - No position from a previous period is carried in.
        - New entries are allowed only inside the evaluation period.
        - Metrics are calculated only for the requested period.
        """

        backtester = self._create_backtester()

        data = backtester._load_daily_pair(
            stock_a,
            stock_b,
        )

        if data.empty:
            return self._empty_result(
                stock_a,
                stock_b,
                start_date,
                end_date,
            )

        start = pd.Timestamp(start_date)
        end = pd.Timestamp(end_date)

        # -----------------------------------------------------
        # Locate evaluation range
        # -----------------------------------------------------

        evaluation_mask = (
            (data.index >= start)
            & (data.index <= end)
        )

        evaluation_positions = np.where(
            evaluation_mask
        )[0]

        if len(evaluation_positions) == 0:
            return self._empty_result(
                stock_a,
                stock_b,
                start_date,
                end_date,
            )

        first_eval_index = int(
            evaluation_positions[0]
        )

        last_eval_index = int(
            evaluation_positions[-1]
        )

        required_history = (
            backtester.config.window
            + (
                backtester.config.stability_lookback_windows
                - 1
            )
            * backtester.config.stability_step
        )

        if (
            first_eval_index
            < required_history
        ):
            raise ValueError(
                f"Not enough pre-period history for "
                f"{stock_a}/{stock_b} starting {start_date}."
            )

        capital = float(
            backtester.config.initial_capital
        )

        trades = []
        equity_records = []

        position = None

        stability_checks = 0
        stability_passes = 0
        stability_rejections = 0

        # =====================================================
        # WALK FORWARD ONLY INSIDE EVALUATION PERIOD
        # =====================================================

        for i in range(
            first_eval_index,
            last_eval_index + 1,
        ):
            signal_date = (
                data.index[i]
            )

            close_a = float(
                data.iloc[i]["close_a"]
            )

            close_b = float(
                data.iloc[i]["close_b"]
            )

            # If this is the last evaluation day,
            # there is no next evaluation-day open.
            has_next_day = (
                i < last_eval_index
            )

            if has_next_day:
                next_date = (
                    data.index[i + 1]
                )

                next_open_a = float(
                    data.iloc[i + 1][
                        "open_a"
                    ]
                )

                next_open_b = float(
                    data.iloc[i + 1][
                        "open_b"
                    ]
                )

            else:
                next_date = None
                next_open_a = None
                next_open_b = None

            # =================================================
            # NO POSITION
            # =================================================

            if position is None:

                # Cannot open on final evaluation day because
                # execution happens next-day open.
                if not has_next_day:
                    equity_records.append(
                        {
                            "date":
                                signal_date,

                            "equity":
                                capital,
                        }
                    )
                    continue

                # All history up to today's close.
                historical = (
                    data.iloc[
                        :i + 1
                    ]
                )

                current_window = (
                    historical.iloc[
                        -backtester.config.window:
                    ]
                )

                model = backtester._fit_model(
                    current_window[
                        "close_a"
                    ],
                    current_window[
                        "close_b"
                    ],
                )

                if not backtester._model_is_valid(
                    model
                ):
                    equity_records.append(
                        {
                            "date":
                                signal_date,

                            "equity":
                                capital,
                        }
                    )
                    continue

                # ---------------------------------------------
                # Rolling stability filter
                # ---------------------------------------------

                stability = {
                    "windows_tested": 0,
                    "valid_windows": 0,
                    "stability_pct": 0.0,
                }

                if (
                    backtester.config
                    .use_stability_filter
                ):
                    stability_checks += 1

                    stability = (
                        backtester._calculate_stability(
                            historical[
                                "close_a"
                            ],
                            historical[
                                "close_b"
                            ],
                        )
                    )

                    if (
                        stability[
                            "windows_tested"
                        ]
                        < backtester.config
                        .stability_lookback_windows
                    ):
                        equity_records.append(
                            {
                                "date":
                                    signal_date,

                                "equity":
                                    capital,
                            }
                        )
                        continue

                    if (
                        stability[
                            "stability_pct"
                        ]
                        < backtester.config
                        .min_stability_pct
                    ):
                        stability_rejections += 1

                        equity_records.append(
                            {
                                "date":
                                    signal_date,

                                "equity":
                                    capital,
                            }
                        )
                        continue

                    stability_passes += 1

                # ---------------------------------------------
                # Signal
                # ---------------------------------------------

                signal_z = (
                    backtester._calculate_z_score(
                        close_a,
                        close_b,
                        model,
                    )
                )

                side = 0

                if (
                    -backtester.config.z_stop
                    < signal_z
                    < -backtester.config.z_entry
                ):
                    side = 1

                elif (
                    backtester.config.z_entry
                    < signal_z
                    < backtester.config.z_stop
                ):
                    side = -1

                if side == 0:
                    equity_records.append(
                        {
                            "date":
                                signal_date,

                            "equity":
                                capital,
                        }
                    )
                    continue

                # ---------------------------------------------
                # Entry next day open
                # ---------------------------------------------

                entry_cost = (
                    capital
                    * backtester.config
                    .transaction_cost_rate
                )

                capital_before_entry = (
                    capital
                )

                capital_after_cost = (
                    capital
                    - entry_cost
                )

                position = {
                    "signal_time":
                        signal_date,

                    "entry_time":
                        next_date,

                    "entry_a":
                        next_open_a,

                    "entry_b":
                        next_open_b,

                    "entry_z":
                        signal_z,

                    "side":
                        side,

                    "alpha":
                        model["alpha"],

                    "beta":
                        model["beta"],

                    "spread_mean":
                        model["spread_mean"],

                    "spread_std":
                        model["spread_std"],

                    "half_life":
                        model["half_life"],

                    "stability_pct":
                        stability[
                            "stability_pct"
                        ],

                    "capital_before_entry":
                        capital_before_entry,

                    "capital_at_entry":
                        capital_after_cost,

                    "entry_cost":
                        entry_cost,

                    "bars_held":
                        0,
                }

                capital = (
                    capital_after_cost
                )

                if debug:
                    print(
                        f"\nENTRY {next_date.date()} | "
                        f"{stock_a}/{stock_b} | "
                        f"z={signal_z:.3f} | "
                        f"stability="
                        f"{stability['stability_pct']:.2f}%"
                    )

                equity_records.append(
                    {
                        "date":
                            next_date,

                        "equity":
                            capital,
                    }
                )

                continue

            # =================================================
            # OPEN POSITION
            # =================================================

            position["bars_held"] += 1

            frozen_model = {
                "alpha":
                    position["alpha"],

                "beta":
                    position["beta"],

                "spread_mean":
                    position["spread_mean"],

                "spread_std":
                    position["spread_std"],
            }

            current_z = (
                backtester._calculate_z_score(
                    close_a,
                    close_b,
                    frozen_model,
                )
            )

            mtm_return = (
                backtester._calculate_position_return(
                    side=
                        position["side"],

                    beta=
                        position["beta"],

                    entry_a=
                        position["entry_a"],

                    entry_b=
                        position["entry_b"],

                    current_a=
                        close_a,

                    current_b=
                        close_b,
                )
            )

            mtm_equity = (
                position[
                    "capital_at_entry"
                ]
                * (
                    1.0
                    + mtm_return
                )
            )

            exit_reason = None

            if (
                abs(current_z)
                <= backtester.config.z_exit
            ):
                exit_reason = (
                    "MEAN_REVERSION"
                )

            elif (
                abs(current_z)
                >= backtester.config.z_stop
            ):
                exit_reason = (
                    "STOP"
                )

            elif (
                position["bars_held"]
                >= backtester.config
                .max_holding_bars
            ):
                exit_reason = (
                    "TIME_EXIT"
                )

            # ---------------------------------------------
            # If period ends, close at last close
            # ---------------------------------------------

            if (
                i == last_eval_index
                and exit_reason is None
            ):
                exit_reason = (
                    "END_OF_PERIOD"
                )

            # ---------------------------------------------
            # Keep position open
            # ---------------------------------------------

            if exit_reason is None:
                equity_records.append(
                    {
                        "date":
                            signal_date,

                        "equity":
                            mtm_equity,
                    }
                )
                continue

            # ---------------------------------------------
            # Exit mechanics
            # ---------------------------------------------

            if (
                exit_reason
                == "END_OF_PERIOD"
            ):
                exit_time = (
                    signal_date
                )

                exit_price_a = (
                    close_a
                )

                exit_price_b = (
                    close_b
                )

            else:
                # Normal exit requires next-day open.
                if not has_next_day:
                    exit_time = (
                        signal_date
                    )

                    exit_price_a = (
                        close_a
                    )

                    exit_price_b = (
                        close_b
                    )

                    exit_reason = (
                        "END_OF_PERIOD"
                    )

                else:
                    exit_time = (
                        next_date
                    )

                    exit_price_a = (
                        next_open_a
                    )

                    exit_price_b = (
                        next_open_b
                    )

            exit_return = (
                backtester._calculate_position_return(
                    side=
                        position["side"],

                    beta=
                        position["beta"],

                    entry_a=
                        position["entry_a"],

                    entry_b=
                        position["entry_b"],

                    current_a=
                        exit_price_a,

                    current_b=
                        exit_price_b,
                )
            )

            gross_exit_equity = (
                position[
                    "capital_at_entry"
                ]
                * (
                    1.0
                    + exit_return
                )
            )

            exit_cost = (
                gross_exit_equity
                * backtester.config
                .transaction_cost_rate
            )

            capital = (
                gross_exit_equity
                - exit_cost
            )

            net_pnl = (
                capital
                - position[
                    "capital_before_entry"
                ]
            )

            net_return = (
                net_pnl
                / position[
                    "capital_before_entry"
                ]
            )

            trades.append(
                {
                    "pair":
                        f"{stock_a}/{stock_b}",

                    "signal_time":
                        position[
                            "signal_time"
                        ],

                    "entry_time":
                        position[
                            "entry_time"
                        ],

                    "exit_signal_time":
                        signal_date,

                    "exit_time":
                        exit_time,

                    "side":
                        (
                            "LONG_SPREAD"
                            if position[
                                "side"
                            ] == 1
                            else
                            "SHORT_SPREAD"
                        ),

                    "entry_z":
                        position[
                            "entry_z"
                        ],

                    "exit_z":
                        current_z,

                    "beta":
                        position[
                            "beta"
                        ],

                    "half_life":
                        position[
                            "half_life"
                        ],

                    "stability_pct":
                        position[
                            "stability_pct"
                        ],

                    "bars_held":
                        position[
                            "bars_held"
                        ],

                    "gross_return":
                        exit_return,

                    "net_return":
                        net_return,

                    "net_pnl":
                        net_pnl,

                    "entry_cost":
                        position[
                            "entry_cost"
                        ],

                    "exit_cost":
                        exit_cost,

                    "exit_reason":
                        exit_reason,
                }
            )

            if debug:
                print(
                    f"EXIT {exit_time.date()} | "
                    f"{exit_reason} | "
                    f"net={net_return:.2%}"
                )

            position = None

            equity_records.append(
                {
                    "date":
                        exit_time,

                    "equity":
                        capital,
                }
            )

        # =====================================================
        # OUTPUT
        # =====================================================

        equity_curve = pd.DataFrame(
            equity_records
        )

        if not equity_curve.empty:
            equity_curve = (
                equity_curve
                .drop_duplicates(
                    subset=["date"],
                    keep="last",
                )
                .set_index("date")
                .sort_index()
            )

        trades_df = pd.DataFrame(
            trades
        )

        metrics = (
            self._calculate_period_metrics(
                equity_curve,
                trades_df,
                initial_capital=
                    backtester.config
                    .initial_capital,
            )
        )

        metrics[
            "stability_checks"
        ] = stability_checks

        metrics[
            "stability_passes"
        ] = stability_passes

        metrics[
            "stability_rejections"
        ] = stability_rejections

        return {
            "pair":
                f"{stock_a}/{stock_b}",

            "start_date":
                start,

            "end_date":
                end,

            "metrics":
                metrics,

            "trades":
                trades_df,

            "equity_curve":
                equity_curve,
        }

    # =========================================================
    # METRICS
    # =========================================================

    @staticmethod
    def _empty_metrics():
        return {
            "initial_equity": 10000.0,
            "final_equity": 10000.0,
            "total_return_pct": 0.0,
            "number_of_trades": 0,
            "win_rate_pct": 0.0,
            "average_trade_return_pct": 0.0,
            "profit_factor": 0.0,
            "sharpe_ratio": 0.0,
            "max_drawdown_pct": 0.0,
        }

    def _empty_result(
        self,
        stock_a: str,
        stock_b: str,
        start_date: str,
        end_date: str,
    ):
        return {
            "pair":
                f"{stock_a}/{stock_b}",

            "start_date":
                pd.Timestamp(start_date),

            "end_date":
                pd.Timestamp(end_date),

            "metrics":
                self._empty_metrics(),

            "trades":
                pd.DataFrame(),

            "equity_curve":
                pd.DataFrame(),
        }

    def _calculate_period_metrics(
        self,
        equity_curve: pd.DataFrame,
        trades: pd.DataFrame,
        initial_capital: float,
    ) -> dict:

        if equity_curve.empty:
            return self._empty_metrics()

        equity = (
            equity_curve["equity"]
            .astype(float)
            .copy()
        )

        final_equity = float(
            equity.iloc[-1]
        )

        total_return = (
            final_equity
            / initial_capital
            - 1.0
        )

        daily_returns = (
            equity
            .pct_change()
            .replace(
                [np.inf, -np.inf],
                np.nan,
            )
            .dropna()
        )

        if (
            len(daily_returns) > 1
            and daily_returns.std() > 0
        ):
            sharpe = (
                daily_returns.mean()
                / daily_returns.std()
                * np.sqrt(252)
            )
        else:
            sharpe = 0.0

        running_max = (
            equity.cummax()
        )

        drawdown = (
            equity
            / running_max
            - 1.0
        )

        max_drawdown = float(
            drawdown.min()
        )

        number_of_trades = (
            len(trades)
        )

        if number_of_trades > 0:
            winners = (
                trades[
                    trades[
                        "net_pnl"
                    ] > 0
                ]
            )

            losers = (
                trades[
                    trades[
                        "net_pnl"
                    ] < 0
                ]
            )

            win_rate = (
                len(winners)
                / number_of_trades
            )

            average_trade_return = float(
                trades[
                    "net_return"
                ].mean()
            )

            gross_profit = float(
                winners[
                    "net_pnl"
                ].sum()
            )

            gross_loss = abs(
                float(
                    losers[
                        "net_pnl"
                    ].sum()
                )
            )

            if gross_loss > 0:
                profit_factor = (
                    gross_profit
                    / gross_loss
                )

            elif gross_profit > 0:
                profit_factor = (
                    float("inf")
                )

            else:
                profit_factor = 0.0

        else:
            win_rate = 0.0
            average_trade_return = 0.0
            profit_factor = 0.0

        return {
            "initial_equity":
                round(
                    initial_capital,
                    2,
                ),

            "final_equity":
                round(
                    final_equity,
                    2,
                ),

            "total_return_pct":
                round(
                    total_return * 100,
                    2,
                ),

            "number_of_trades":
                number_of_trades,

            "win_rate_pct":
                round(
                    win_rate * 100,
                    2,
                ),

            "average_trade_return_pct":
                round(
                    average_trade_return
                    * 100,
                    3,
                ),

            "profit_factor":
                (
                    round(
                        profit_factor,
                        3,
                    )
                    if np.isfinite(
                        profit_factor
                    )
                    else "INF"
                ),

            "sharpe_ratio":
                round(
                    float(sharpe),
                    3,
                ),

            "max_drawdown_pct":
                round(
                    max_drawdown
                    * 100,
                    2,
                ),
        }

    # =========================================================
    # SINGLE PAIR
    # =========================================================

    def evaluate_pair(
        self,
        stock_a: str,
        stock_b: str,
    ) -> dict:

        research = self._run_period(
            stock_a,
            stock_b,
            self.config.research_start,
            self.config.research_end,
        )

        validation = self._run_period(
            stock_a,
            stock_b,
            self.config.validation_start,
            self.config.validation_end,
        )

        validation_metrics = (
            validation["metrics"]
        )

        selected = (
            validation_metrics[
                "number_of_trades"
            ]
            >= self.config
            .minimum_validation_trades

            and

            validation_metrics[
                "total_return_pct"
            ]
            > self.config
            .minimum_validation_return_pct

            and

            validation_metrics[
                "sharpe_ratio"
            ]
            > self.config
            .minimum_validation_sharpe
        )

        # We still compute OOS for research reporting,
        # but selection decision is already frozen above.
        test = self._run_period(
            stock_a,
            stock_b,
            self.config.test_start,
            self.config.test_end,
        )

        return {
            "pair":
                f"{stock_a}/{stock_b}",

            "research":
                research,

            "validation":
                validation,

            "selected":
                bool(selected),

            "test":
                test,
        }

    # =========================================================
    # UNIVERSE
    # =========================================================

    def run_universe(
        self,
        symbols: list[str],
        debug: bool = True,
    ) -> dict:

        pair_list = list(
            combinations(
                symbols,
                2,
            )
        )

        rows = []

        total_pairs = len(
            pair_list
        )

        for idx, (
            stock_a,
            stock_b,
        ) in enumerate(
            pair_list,
            start=1,
        ):

            result = (
                self.evaluate_pair(
                    stock_a,
                    stock_b,
                )
            )

            research = (
                result["research"][
                    "metrics"
                ]
            )

            validation = (
                result["validation"][
                    "metrics"
                ]
            )

            test = (
                result["test"][
                    "metrics"
                ]
            )

            rows.append(
                {
                    "pair":
                        result["pair"],

                    "selected":
                        result[
                            "selected"
                        ],

                    "research_trades":
                        research[
                            "number_of_trades"
                        ],

                    "research_return_pct":
                        research[
                            "total_return_pct"
                        ],

                    "research_sharpe":
                        research[
                            "sharpe_ratio"
                        ],

                    "validation_trades":
                        validation[
                            "number_of_trades"
                        ],

                    "validation_return_pct":
                        validation[
                            "total_return_pct"
                        ],

                    "validation_sharpe":
                        validation[
                            "sharpe_ratio"
                        ],

                    "validation_max_dd_pct":
                        validation[
                            "max_drawdown_pct"
                        ],

                    "test_trades":
                        test[
                            "number_of_trades"
                        ],

                    "test_return_pct":
                        test[
                            "total_return_pct"
                        ],

                    "test_sharpe":
                        test[
                            "sharpe_ratio"
                        ],

                    "test_max_dd_pct":
                        test[
                            "max_drawdown_pct"
                        ],
                }
            )

            if debug:
                status = (
                    "SELECT"
                    if result[
                        "selected"
                    ]
                    else "REJECT"
                )

                print(
                    f"[{idx}/{total_pairs}] "
                    f"{result['pair']:<12} | "
                    f"{status:<6} | "
                    f"VAL trades="
                    f"{validation['number_of_trades']:<2} | "
                    f"VAL ret="
                    f"{validation['total_return_pct']:>7.2f}% | "
                    f"VAL sharpe="
                    f"{validation['sharpe_ratio']:>6.2f} | "
                    f"OOS trades="
                    f"{test['number_of_trades']:<2} | "
                    f"OOS ret="
                    f"{test['total_return_pct']:>7.2f}%"
                )

        results_df = (
            pd.DataFrame(
                rows
            )
        )

        selected_df = (
            results_df[
                results_df[
                    "selected"
                ]
            ]
            .copy()
        )

        if not selected_df.empty:
            selected_df = (
                selected_df
                .sort_values(
                    [
                        "validation_sharpe",
                        "validation_return_pct",
                    ],
                    ascending=False,
                )
                .reset_index(
                    drop=True
                )
            )

        summary = (
            self._build_summary(
                results_df,
                selected_df,
            )
        )

        return {
            "results":
                results_df,

            "selected":
                selected_df,

            "summary":
                summary,
        }

    # =========================================================
    # SUMMARY
    # =========================================================

    @staticmethod
    def _build_summary(
        all_results: pd.DataFrame,
        selected_results: pd.DataFrame,
    ) -> dict:

        if selected_results.empty:
            return {
                "pairs_tested":
                    len(all_results),

                "pairs_selected":
                    0,

                "selected_with_oos_trades":
                    0,

                "oos_profitable_pairs":
                    0,

                "oos_profitable_pct":
                    0.0,
            }

        with_oos_trades = (
            selected_results[
                selected_results[
                    "test_trades"
                ] > 0
            ]
        )

        profitable = (
            with_oos_trades[
                with_oos_trades[
                    "test_return_pct"
                ] > 0
            ]
        )

        if len(
            with_oos_trades
        ) > 0:
            profitable_pct = (
                len(profitable)
                / len(
                    with_oos_trades
                )
                * 100
            )

            median_oos_return = float(
                with_oos_trades[
                    "test_return_pct"
                ].median()
            )

            median_oos_sharpe = float(
                with_oos_trades[
                    "test_sharpe"
                ].median()
            )

        else:
            profitable_pct = 0.0
            median_oos_return = 0.0
            median_oos_sharpe = 0.0

        return {
            "pairs_tested":
                len(all_results),

            "pairs_selected":
                len(selected_results),

            "selected_with_oos_trades":
                len(with_oos_trades),

            "selected_without_oos_trades":
                int(
                    (
                        selected_results[
                            "test_trades"
                        ] == 0
                    ).sum()
                ),

            "oos_profitable_pairs":
                len(profitable),

            "oos_profitable_pct":
                round(
                    profitable_pct,
                    2,
                ),

            "median_validation_return_pct":
                round(
                    float(
                        selected_results[
                            "validation_return_pct"
                        ].median()
                    ),
                    2,
                ),

            "median_validation_sharpe":
                round(
                    float(
                        selected_results[
                            "validation_sharpe"
                        ].median()
                    ),
                    3,
                ),

            "median_oos_return_pct":
                round(
                    median_oos_return,
                    2,
                ),

            "median_oos_sharpe":
                round(
                    median_oos_sharpe,
                    3,
                ),
        }


out_of_sample_tester = (
    OutOfSampleTester()
)