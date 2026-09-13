from dataclasses import dataclass

import numpy as np
import pandas as pd

from backtesting.out_of_sample import OutOfSampleTester
from research.walk_forward_selector import walk_forward_selector


@dataclass(frozen=True)
class WalkForwardPortfolioConfig:
    initial_capital: float = 10000.0

    max_pairs: int = 5

    # Aynı hisse aynı çeyrekte en fazla
    # kaç pair içerisinde bulunabilir?
    max_pairs_per_asset: int = 2

    annualization_factor: int = 252


class WalkForwardPortfolioBacktester:
    def __init__(
        self,
        config: WalkForwardPortfolioConfig | None = None,
    ):
        self.config = (
            config
            or WalkForwardPortfolioConfig()
        )

        self.period_backtester = (
            OutOfSampleTester()
        )

    # =========================================================
    # DIVERSIFIED PAIR SELECTION
    # =========================================================

    def _diversify_selection(
        self,
        selections: pd.DataFrame,
    ) -> pd.DataFrame:
        """
        Greedy diversification filter.

        Highest selection_score pairs are considered first,
        but one asset cannot appear in more than
        max_pairs_per_asset selected pairs.
        """

        if selections.empty:
            return selections.copy()

        ranked = (
            selections
            .sort_values(
                [
                    "selection_score",
                    "stability_pct",
                ],
                ascending=False,
            )
            .copy()
        )

        selected_rows = []

        asset_counts = {}

        for _, row in ranked.iterrows():

            stock_a = row["stock_a"]
            stock_b = row["stock_b"]

            count_a = asset_counts.get(
                stock_a,
                0,
            )

            count_b = asset_counts.get(
                stock_b,
                0,
            )

            if (
                count_a
                >= self.config.max_pairs_per_asset
            ):
                continue

            if (
                count_b
                >= self.config.max_pairs_per_asset
            ):
                continue

            selected_rows.append(
                row
            )

            asset_counts[
                stock_a
            ] = count_a + 1

            asset_counts[
                stock_b
            ] = count_b + 1

            if (
                len(selected_rows)
                >= self.config.max_pairs
            ):
                break

        if not selected_rows:
            return pd.DataFrame(
                columns=selections.columns
            )

        return (
            pd.DataFrame(
                selected_rows
            )
            .reset_index(
                drop=True
            )
        )

    # =========================================================
    # SINGLE QUARTER / PERIOD
    # =========================================================

    def _run_period(
        self,
        selections: pd.DataFrame,
        start_date: pd.Timestamp,
        end_date: pd.Timestamp,
        starting_capital: float,
        debug: bool = False,
    ) -> dict:

        start_date = pd.Timestamp(
            start_date
        )

        end_date = pd.Timestamp(
            end_date
        )

        diversified = (
            self._diversify_selection(
                selections
            )
        )

        # -----------------------------------------------------
        # No eligible pairs -> stay fully in cash
        # -----------------------------------------------------

        if diversified.empty:

            index = pd.bdate_range(
                start=start_date,
                end=end_date,
            )

            equity = pd.Series(
                starting_capital,
                index=index,
                dtype=float,
            )

            return {
                "selected_pairs":
                    [],

                "equity_curve":
                    equity,

                "trades":
                    pd.DataFrame(),

                "starting_capital":
                    starting_capital,

                "final_capital":
                    starting_capital,

                "period_return_pct":
                    0.0,
            }

        pair_results = []

        # Each pair receives an equal sleeve.
        allocation_per_pair = (
            starting_capital
            / len(diversified)
        )

        for _, row in (
            diversified.iterrows()
        ):

            stock_a = row[
                "stock_a"
            ]

            stock_b = row[
                "stock_b"
            ]

            result = (
                self.period_backtester
                ._run_period(
                    stock_a=
                        stock_a,

                    stock_b=
                        stock_b,

                    start_date=
                        start_date.strftime(
                            "%Y-%m-%d"
                        ),

                    end_date=
                        end_date.strftime(
                            "%Y-%m-%d"
                        ),

                    debug=False,
                )
            )

            pair_results.append(
                {
                    "pair":
                        row["pair"],

                    "stock_a":
                        stock_a,

                    "stock_b":
                        stock_b,

                    "selection_score":
                        row[
                            "selection_score"
                        ],

                    "result":
                        result,
                }
            )

        # =====================================================
        # BUILD COMMON TRADING CALENDAR
        # =====================================================

        all_dates = set()

        for item in pair_results:

            curve = (
                item[
                    "result"
                ][
                    "equity_curve"
                ]
            )

            if not curve.empty:
                all_dates.update(
                    curve.index
                )

        if not all_dates:

            index = pd.bdate_range(
                start=start_date,
                end=end_date,
            )

            equity = pd.Series(
                starting_capital,
                index=index,
                dtype=float,
            )

            return {
                "selected_pairs":
                    diversified[
                        "pair"
                    ].tolist(),

                "equity_curve":
                    equity,

                "trades":
                    pd.DataFrame(),

                "starting_capital":
                    starting_capital,

                "final_capital":
                    starting_capital,

                "period_return_pct":
                    0.0,
            }

        common_index = pd.DatetimeIndex(
            sorted(
                all_dates
            )
        )

        portfolio_equity = pd.Series(
            0.0,
            index=common_index,
            dtype=float,
        )

        all_trades = []

        # =====================================================
        # NORMALIZE EACH PAIR SLEEVE
        # =====================================================

        for item in pair_results:

            result = item[
                "result"
            ]

            curve = result[
                "equity_curve"
            ]

            if curve.empty:

                sleeve = pd.Series(
                    allocation_per_pair,
                    index=common_index,
                )

            else:

                pair_equity = (
                    curve["equity"]
                    .astype(float)
                    .reindex(
                        common_index
                    )
                    .ffill()
                    .bfill()
                )

                # OutOfSampleTester period always
                # begins with its own initial capital.
                base_capital = float(
                    result[
                        "metrics"
                    ][
                        "initial_equity"
                    ]
                )

                normalized_growth = (
                    pair_equity
                    / base_capital
                )

                sleeve = (
                    allocation_per_pair
                    * normalized_growth
                )

            portfolio_equity = (
                portfolio_equity
                + sleeve
            )

            trades = result[
                "trades"
            ]

            if not trades.empty:

                trades = (
                    trades.copy()
                )

                trades[
                    "portfolio_pair"
                ] = item[
                    "pair"
                ]

                trades[
                    "rebalance_start"
                ] = start_date

                trades[
                    "rebalance_end"
                ] = end_date

                trades[
                    "portfolio_allocation"
                ] = allocation_per_pair

                all_trades.append(
                    trades
                )

        final_capital = float(
            portfolio_equity.iloc[-1]
        )

        period_return = (
            final_capital
            / starting_capital
            - 1.0
        )

        trades_df = (
            pd.concat(
                all_trades,
                ignore_index=True,
            )
            if all_trades
            else pd.DataFrame()
        )

        if debug:

            print(
                "\n"
                + "-" * 70
            )

            print(
                f"{start_date.date()} "
                f"-> {end_date.date()}"
            )

            print(
                "Selected:",
                ", ".join(
                    diversified[
                        "pair"
                    ].tolist()
                ),
            )

            print(
                f"Pairs: "
                f"{len(diversified)}"
            )

            print(
                f"Capital: "
                f"{starting_capital:,.2f} "
                f"-> "
                f"{final_capital:,.2f}"
            )

            print(
                f"Period return: "
                f"{period_return * 100:.2f}%"
            )

            print(
                f"Trades: "
                f"{len(trades_df)}"
            )

        return {
            "selected_pairs":
                diversified[
                    "pair"
                ].tolist(),

            "selection_table":
                diversified,

            "equity_curve":
                portfolio_equity,

            "trades":
                trades_df,

            "starting_capital":
                starting_capital,

            "final_capital":
                final_capital,

            "period_return_pct":
                period_return * 100,
        }

    # =========================================================
    # PORTFOLIO METRICS
    # =========================================================

    def _calculate_metrics(
        self,
        equity_curve: pd.Series,
        trades: pd.DataFrame,
        period_records: pd.DataFrame,
    ) -> dict:

        if equity_curve.empty:

            return {
                "initial_capital":
                    self.config.initial_capital,

                "final_equity":
                    self.config.initial_capital,

                "total_return_pct":
                    0.0,

                "cagr_pct":
                    0.0,

                "sharpe_ratio":
                    0.0,

                "max_drawdown_pct":
                    0.0,

                "annualized_volatility_pct":
                    0.0,

                "number_of_trades":
                    0,
            }

        equity = (
            equity_curve
            .astype(float)
            .sort_index()
        )

        initial = float(
            equity.iloc[0]
        )

        final = float(
            equity.iloc[-1]
        )

        total_return = (
            final
            / initial
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
                * np.sqrt(
                    self.config
                    .annualization_factor
                )
            )

            annualized_volatility = (
                daily_returns.std()
                * np.sqrt(
                    self.config
                    .annualization_factor
                )
            )

        else:

            sharpe = 0.0
            annualized_volatility = 0.0

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

        elapsed_days = (
            equity.index[-1]
            - equity.index[0]
        ).days

        years = (
            elapsed_days
            / 365.25
        )

        if (
            years > 0
            and initial > 0
            and final > 0
        ):

            cagr = (
                (
                    final
                    / initial
                )
                ** (
                    1.0
                    / years
                )
                - 1.0
            )

        else:
            cagr = 0.0

        total_trades = (
            0
            if trades.empty
            else len(trades)
        )

        active_periods = (
            0
            if period_records.empty
            else int(
                (
                    period_records[
                        "number_of_pairs"
                    ] > 0
                ).sum()
            )
        )

        cash_periods = (
            0
            if period_records.empty
            else int(
                (
                    period_records[
                        "number_of_pairs"
                    ] == 0
                ).sum()
            )
        )

        return {
            "initial_capital":
                round(
                    initial,
                    2,
                ),

            "final_equity":
                round(
                    final,
                    2,
                ),

            "total_return_pct":
                round(
                    total_return
                    * 100,
                    2,
                ),

            "cagr_pct":
                round(
                    cagr
                    * 100,
                    2,
                ),

            "sharpe_ratio":
                round(
                    float(sharpe),
                    3,
                ),

            "annualized_volatility_pct":
                round(
                    annualized_volatility
                    * 100,
                    2,
                ),

            "max_drawdown_pct":
                round(
                    max_drawdown
                    * 100,
                    2,
                ),

            "number_of_trades":
                total_trades,

            "rebalance_periods":
                len(
                    period_records
                ),

            "active_periods":
                active_periods,

            "cash_periods":
                cash_periods,

            "average_pairs_per_period":
                (
                    round(
                        float(
                            period_records[
                                "number_of_pairs"
                            ].mean()
                        ),
                        2,
                    )
                    if not period_records.empty
                    else 0.0
                ),
        }

    # =========================================================
    # FULL WALK-FORWARD PORTFOLIO
    # =========================================================

    def run(
        self,
        symbols: list[str],
        start_date: str = "2022-01-01",
        end_date: str = "2026-08-20",
        debug: bool = True,
    ) -> dict:

        start = pd.Timestamp(
            start_date
        )

        end = pd.Timestamp(
            end_date
        )

        # -----------------------------------------------------
        # Generate purely historical selections
        # -----------------------------------------------------

        selections = (
            walk_forward_selector.run(
                symbols=symbols,
                start_date=start_date,
                end_date=end_date,
                debug=False,
            )
        )

        # Need all quarter dates, including quarters
        # where selector found no pair.
        price_matrix = (
            walk_forward_selector
            .stability_analyzer
            ._load_prices(
                symbols
            )
        )

        rebalance_dates = (
            walk_forward_selector
            ._get_rebalance_dates(
                price_matrix=
                    price_matrix,

                start_date=
                    start_date,

                end_date=
                    end_date,
            )
        )

        if not rebalance_dates:

            raise ValueError(
                "No rebalance dates available."
            )

        current_capital = float(
            self.config.initial_capital
        )

        portfolio_parts = []

        all_trades = []

        period_rows = []

        for i, period_start in enumerate(
            rebalance_dates
        ):

            if (
                i
                < len(
                    rebalance_dates
                ) - 1
            ):

                next_rebalance = (
                    rebalance_dates[
                        i + 1
                    ]
                )

                period_end = (
                    next_rebalance
                    - pd.Timedelta(
                        days=1
                    )
                )

            else:

                period_end = end

            period_end = min(
                period_end,
                end,
            )

            if not selections.empty:

                period_selection = (
                    selections[
                        selections[
                            "rebalance_date"
                        ]
                        == period_start
                    ]
                    .copy()
                )

            else:

                period_selection = (
                    pd.DataFrame()
                )

            result = (
                self._run_period(
                    selections=
                        period_selection,

                    start_date=
                        period_start,

                    end_date=
                        period_end,

                    starting_capital=
                        current_capital,

                    debug=
                        debug,
                )
            )

            current_capital = (
                result[
                    "final_capital"
                ]
            )

            period_curve = (
                result[
                    "equity_curve"
                ]
            )

            if not period_curve.empty:

                # Avoid duplicate boundary dates
                if portfolio_parts:

                    previous_last = (
                        portfolio_parts[
                            -1
                        ].index[-1]
                    )

                    period_curve = (
                        period_curve[
                            period_curve.index
                            > previous_last
                        ]
                    )

                portfolio_parts.append(
                    period_curve
                )

            if not result[
                "trades"
            ].empty:

                all_trades.append(
                    result[
                        "trades"
                    ]
                )

            period_rows.append(
                {
                    "period_start":
                        period_start,

                    "period_end":
                        period_end,

                    "number_of_pairs":
                        len(
                            result[
                                "selected_pairs"
                            ]
                        ),

                    "selected_pairs":
                        ", ".join(
                            result[
                                "selected_pairs"
                            ]
                        ),

                    "starting_capital":
                        round(
                            result[
                                "starting_capital"
                            ],
                            2,
                        ),

                    "final_capital":
                        round(
                            result[
                                "final_capital"
                            ],
                            2,
                        ),

                    "period_return_pct":
                        round(
                            result[
                                "period_return_pct"
                            ],
                            2,
                        ),

                    "number_of_trades":
                        len(
                            result[
                                "trades"
                            ]
                        ),
                }
            )

        # =====================================================
        # FINAL OUTPUT
        # =====================================================

        if portfolio_parts:

            equity_curve = pd.concat(
                portfolio_parts
            )

            equity_curve = (
                equity_curve[
                    ~equity_curve
                    .index
                    .duplicated(
                        keep="last"
                    )
                ]
                .sort_index()
            )

        else:

            equity_curve = pd.Series(
                dtype=float
            )

        trades_df = (
            pd.concat(
                all_trades,
                ignore_index=True,
            )
            if all_trades
            else pd.DataFrame()
        )

        periods_df = pd.DataFrame(
            period_rows
        )

        metrics = (
            self._calculate_metrics(
                equity_curve=
                    equity_curve,

                trades=
                    trades_df,

                period_records=
                    periods_df,
            )
        )

        return {
            "metrics":
                metrics,

            "equity_curve":
                equity_curve,

            "trades":
                trades_df,

            "periods":
                periods_df,

            "raw_selections":
                selections,
        }


walk_forward_portfolio = (
    WalkForwardPortfolioBacktester()
)