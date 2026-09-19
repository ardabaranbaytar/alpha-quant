from dataclasses import dataclass

import numpy as np
import pandas as pd

from backtesting.out_of_sample import OutOfSampleTester
from config.risk_config import risk_config
from research.walk_forward_selector import walk_forward_selector


@dataclass(frozen=True)
class RiskSizedPortfolioConfig:
    initial_capital: float = 10000.0

    max_pairs: int = 5
    max_pairs_per_asset: int = 2

    # Existing project risk rules
    max_risk_per_trade: float = risk_config.MAX_RISK_PER_TRADE
    max_asset_exposure: float = risk_config.MAX_ASSET_EXPOSURE
    max_strategy_exposure: float = risk_config.MAX_STRATEGY_EXPOSURE

    # Risk estimation
    volatility_lookback: int = 60

    # Estimated adverse move:
    # pair daily vol * sqrt(half-life) * sigma multiplier
    stop_sigma: float = 2.0

    # Avoid absurdly tiny / huge estimated stop distances
    min_estimated_stop_pct: float = 0.03
    max_estimated_stop_pct: float = 0.12

    # Half-life horizon bounds
    min_risk_horizon_days: float = 3.0
    max_risk_horizon_days: float = 20.0

    annualization_factor: int = 252


class RiskSizedWalkForwardPortfolioBacktester:
    def __init__(
        self,
        config: RiskSizedPortfolioConfig | None = None,
    ):
        self.config = (
            config
            or RiskSizedPortfolioConfig()
        )

        self.period_backtester = (
            OutOfSampleTester()
        )

    # =========================================================
    # DIVERSIFICATION
    # =========================================================

    def _diversify_selection(
        self,
        selections: pd.DataFrame,
    ) -> pd.DataFrame:

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

        rows = []
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

            rows.append(row)

            asset_counts[stock_a] = (
                count_a + 1
            )

            asset_counts[stock_b] = (
                count_b + 1
            )

            if (
                len(rows)
                >= self.config.max_pairs
            ):
                break

        if not rows:
            return pd.DataFrame(
                columns=selections.columns
            )

        return (
            pd.DataFrame(rows)
            .reset_index(drop=True)
        )

    # =========================================================
    # PAIR DAILY RISK
    # =========================================================

    def _estimate_pair_risk(
        self,
        price_matrix: pd.DataFrame,
        stock_a: str,
        stock_b: str,
        rebalance_date: pd.Timestamp,
        beta: float,
        half_life: float,
    ) -> dict:
        """
        Estimate pair risk using ONLY prices strictly
        before the rebalance date.

        Pair return:
            w_a * return_a - w_b * return_b

        where beta determines normalized leg weights.
        """

        history = (
            price_matrix.loc[
                price_matrix.index
                < rebalance_date,
                [stock_a, stock_b],
            ]
            .dropna()
            .tail(
                self.config.volatility_lookback
                + 1
            )
        )

        if len(history) < 20:
            return {
                "daily_volatility": np.nan,
                "risk_horizon_days": np.nan,
                "estimated_stop_pct": np.nan,
                "weight_a": np.nan,
                "weight_b": np.nan,
            }

        beta = float(beta)

        if (
            not np.isfinite(beta)
            or beta <= 0
        ):
            return {
                "daily_volatility": np.nan,
                "risk_horizon_days": np.nan,
                "estimated_stop_pct": np.nan,
                "weight_a": np.nan,
                "weight_b": np.nan,
            }

        weight_a = (
            1.0
            / (
                1.0 + beta
            )
        )

        weight_b = (
            beta
            / (
                1.0 + beta
            )
        )

        returns = (
            history
            .pct_change()
            .dropna()
        )

        pair_returns = (
            weight_a
            * returns[stock_a]
            -
            weight_b
            * returns[stock_b]
        )

        daily_volatility = float(
            pair_returns.std()
        )

        if not np.isfinite(
            daily_volatility
        ):
            return {
                "daily_volatility": np.nan,
                "risk_horizon_days": np.nan,
                "estimated_stop_pct": np.nan,
                "weight_a": weight_a,
                "weight_b": weight_b,
            }

        half_life = float(
            half_life
        )

        risk_horizon = float(
            np.clip(
                half_life,
                self.config
                .min_risk_horizon_days,
                self.config
                .max_risk_horizon_days,
            )
        )

        raw_stop = (
            daily_volatility
            * np.sqrt(
                risk_horizon
            )
            * self.config.stop_sigma
        )

        estimated_stop = float(
            np.clip(
                raw_stop,
                self.config
                .min_estimated_stop_pct,
                self.config
                .max_estimated_stop_pct,
            )
        )

        return {
            "daily_volatility":
                daily_volatility,

            "risk_horizon_days":
                risk_horizon,

            "estimated_stop_pct":
                estimated_stop,

            "weight_a":
                weight_a,

            "weight_b":
                weight_b,
        }

    # =========================================================
    # RISK ALLOCATION
    # =========================================================

    def _build_allocations(
        self,
        selections: pd.DataFrame,
        price_matrix: pd.DataFrame,
        rebalance_date: pd.Timestamp,
        capital: float,
    ) -> pd.DataFrame:
        """
        Calculate ex-ante pair allocations.

        Main rule:

            allowed_loss
            ----------------
            estimated_stop %

        Example:

            portfolio = 10,000
            max risk = 1% = 100
            estimated stop = 5%

            desired allocation = 100 / .05
                               = 2,000

        Additional constraints:
        - max strategy exposure
        - max asset exposure
        """

        if selections.empty:
            return pd.DataFrame()

        selections = (
            selections
            .sort_values(
                "selection_score",
                ascending=False,
            )
            .copy()
        )

        max_total_allocation = (
            capital
            * self.config
            .max_strategy_exposure
        )

        max_asset_allocation = (
            capital
            * self.config
            .max_asset_exposure
        )

        risk_budget = (
            capital
            * self.config
            .max_risk_per_trade
        )

        total_allocated = 0.0

        asset_exposure = {}

        allocation_rows = []

        for _, row in selections.iterrows():

            stock_a = row[
                "stock_a"
            ]

            stock_b = row[
                "stock_b"
            ]

            beta = row.get(
                "median_beta",
                np.nan,
            )

            half_life = row.get(
                "median_half_life",
                np.nan,
            )

            if (
                pd.isna(beta)
                or pd.isna(half_life)
            ):
                continue

            risk = (
                self._estimate_pair_risk(
                    price_matrix=
                        price_matrix,

                    stock_a=
                        stock_a,

                    stock_b=
                        stock_b,

                    rebalance_date=
                        rebalance_date,

                    beta=
                        beta,

                    half_life=
                        half_life,
                )
            )

            estimated_stop = (
                risk[
                    "estimated_stop_pct"
                ]
            )

            if (
                not np.isfinite(
                    estimated_stop
                )
                or estimated_stop <= 0
            ):
                continue

            weight_a = (
                risk["weight_a"]
            )

            weight_b = (
                risk["weight_b"]
            )

            # ---------------------------------------------
            # Risk-based desired sleeve
            # ---------------------------------------------

            desired_allocation = (
                risk_budget
                / estimated_stop
            )

            # ---------------------------------------------
            # Remaining strategy capacity
            # ---------------------------------------------

            strategy_remaining = (
                max_total_allocation
                - total_allocated
            )

            if strategy_remaining <= 0:
                break

            # ---------------------------------------------
            # Asset exposure capacity
            # ---------------------------------------------

            existing_a = (
                asset_exposure.get(
                    stock_a,
                    0.0,
                )
            )

            existing_b = (
                asset_exposure.get(
                    stock_b,
                    0.0,
                )
            )

            available_a = max(
                0.0,
                max_asset_allocation
                - existing_a,
            )

            available_b = max(
                0.0,
                max_asset_allocation
                - existing_b,
            )

            if weight_a > 0:
                allocation_from_a = (
                    available_a
                    / weight_a
                )
            else:
                allocation_from_a = 0.0

            if weight_b > 0:
                allocation_from_b = (
                    available_b
                    / weight_b
                )
            else:
                allocation_from_b = 0.0

            actual_allocation = min(
                desired_allocation,
                strategy_remaining,
                allocation_from_a,
                allocation_from_b,
            )

            if (
                actual_allocation
                <= 0
            ):
                continue

            exposure_a = (
                actual_allocation
                * weight_a
            )

            exposure_b = (
                actual_allocation
                * weight_b
            )

            asset_exposure[
                stock_a
            ] = (
                existing_a
                + exposure_a
            )

            asset_exposure[
                stock_b
            ] = (
                existing_b
                + exposure_b
            )

            total_allocated += (
                actual_allocation
            )

            estimated_loss = (
                actual_allocation
                * estimated_stop
            )

            allocation_rows.append(
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

                    "stability_pct":
                        row[
                            "stability_pct"
                        ],

                    "beta":
                        float(beta),

                    "half_life":
                        float(half_life),

                    "daily_volatility":
                        risk[
                            "daily_volatility"
                        ],

                    "estimated_stop_pct":
                        estimated_stop,

                    "weight_a":
                        weight_a,

                    "weight_b":
                        weight_b,

                    "allocation":
                        actual_allocation,

                    "allocation_pct":
                        (
                            actual_allocation
                            / capital
                            * 100
                        ),

                    "estimated_loss":
                        estimated_loss,

                    "estimated_loss_pct":
                        (
                            estimated_loss
                            / capital
                            * 100
                        ),
                }
            )

        if not allocation_rows:
            return pd.DataFrame()

        return pd.DataFrame(
            allocation_rows
        )

    # =========================================================
    # SINGLE PERIOD
    # =========================================================

    def _run_period(
        self,
        selections: pd.DataFrame,
        price_matrix: pd.DataFrame,
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

        allocations = (
            self._build_allocations(
                selections=
                    diversified,

                price_matrix=
                    price_matrix,

                rebalance_date=
                    start_date,

                capital=
                    starting_capital,
            )
        )

        # -----------------------------------------------------
        # Nothing allocated -> all cash
        # -----------------------------------------------------

        if allocations.empty:

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
                "selected_pairs": [],
                "allocations": pd.DataFrame(),
                "equity_curve": equity,
                "trades": pd.DataFrame(),
                "starting_capital": starting_capital,
                "final_capital": starting_capital,
                "period_return_pct": 0.0,
                "deployed_capital": 0.0,
                "cash_capital": starting_capital,
            }

        pair_results = []

        deployed_capital = float(
            allocations[
                "allocation"
            ].sum()
        )

        cash_capital = (
            starting_capital
            - deployed_capital
        )

        for _, allocation_row in (
            allocations.iterrows()
        ):

            stock_a = (
                allocation_row[
                    "stock_a"
                ]
            )

            stock_b = (
                allocation_row[
                    "stock_b"
                ]
            )

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
                        allocation_row[
                            "pair"
                        ],

                    "allocation":
                        float(
                            allocation_row[
                                "allocation"
                            ]
                        ),

                    "result":
                        result,

                    "risk":
                        allocation_row,
                }
            )

        # =====================================================
        # COMMON DATE INDEX
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
                    allocations[
                        "pair"
                    ].tolist(),

                "allocations":
                    allocations,

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

                "deployed_capital":
                    deployed_capital,

                "cash_capital":
                    cash_capital,
            }

        common_index = (
            pd.DatetimeIndex(
                sorted(
                    all_dates
                )
            )
        )

        # Cash never changes in this first model.
        portfolio_equity = pd.Series(
            cash_capital,
            index=common_index,
            dtype=float,
        )

        all_trades = []

        # =====================================================
        # SCALE PAIR CURVES TO RISK ALLOCATION
        # =====================================================

        for item in pair_results:

            result = (
                item["result"]
            )

            actual_allocation = (
                item["allocation"]
            )

            curve = (
                result[
                    "equity_curve"
                ]
            )

            if curve.empty:

                sleeve = pd.Series(
                    actual_allocation,
                    index=common_index,
                    dtype=float,
                )

            else:

                pair_equity = (
                    curve[
                        "equity"
                    ]
                    .astype(float)
                    .reindex(
                        common_index
                    )
                    .ffill()
                    .bfill()
                )

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
                    actual_allocation
                    * normalized_growth
                )

            portfolio_equity = (
                portfolio_equity
                + sleeve
            )

            trades = (
                result[
                    "trades"
                ]
            )

            if not trades.empty:

                trades = (
                    trades.copy()
                )

                risk_row = (
                    item["risk"]
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
                ] = actual_allocation

                trades[
                    "allocation_pct"
                ] = risk_row[
                    "allocation_pct"
                ]

                trades[
                    "estimated_stop_pct"
                ] = risk_row[
                    "estimated_stop_pct"
                ]

                trades[
                    "estimated_loss_pct"
                ] = risk_row[
                    "estimated_loss_pct"
                ]

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
                + "-" * 72
            )

            print(
                f"{start_date.date()} "
                f"-> "
                f"{end_date.date()}"
            )

            print(
                "Pairs:",
                ", ".join(
                    allocations[
                        "pair"
                    ].tolist()
                ),
            )

            print(
                f"Deployed: "
                f"{deployed_capital:,.2f} "
                f"({deployed_capital / starting_capital:.1%})"
            )

            print(
                f"Cash: "
                f"{cash_capital:,.2f}"
            )

            print(
                f"Capital: "
                f"{starting_capital:,.2f} "
                f"-> "
                f"{final_capital:,.2f}"
            )

            print(
                f"Period return: "
                f"{period_return:.2%}"
            )

            print(
                f"Trades: "
                f"{len(trades_df)}"
            )

            print("\nAllocations:")

            print(
                allocations[
                    [
                        "pair",
                        "allocation_pct",
                        "estimated_stop_pct",
                        "estimated_loss_pct",
                    ]
                ].to_string(
                    index=False
                )
            )

        return {
            "selected_pairs":
                allocations[
                    "pair"
                ].tolist(),

            "allocations":
                allocations,

            "equity_curve":
                portfolio_equity,

            "trades":
                trades_df,

            "starting_capital":
                starting_capital,

            "final_capital":
                final_capital,

            "period_return_pct":
                period_return
                * 100,

            "deployed_capital":
                deployed_capital,

            "cash_capital":
                cash_capital,
        }

    # =========================================================
    # METRICS
    # =========================================================

    def _calculate_metrics(
        self,
        equity_curve: pd.Series,
        trades: pd.DataFrame,
        periods: pd.DataFrame,
    ) -> dict:

        if equity_curve.empty:

            return {
                "initial_capital":
                    self.config
                    .initial_capital,

                "final_equity":
                    self.config
                    .initial_capital,

                "total_return_pct":
                    0.0,

                "cagr_pct":
                    0.0,

                "sharpe_ratio":
                    0.0,

                "annualized_volatility_pct":
                    0.0,

                "max_drawdown_pct":
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

            annualized_vol = (
                daily_returns.std()
                * np.sqrt(
                    self.config
                    .annualization_factor
                )
            )

        else:
            sharpe = 0.0
            annualized_vol = 0.0

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
                    annualized_vol
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
                (
                    0
                    if trades.empty
                    else len(trades)
                ),

            "rebalance_periods":
                len(periods),

            "average_deployed_pct":
                (
                    round(
                        float(
                            periods[
                                "deployed_pct"
                            ].mean()
                        ),
                        2,
                    )
                    if not periods.empty
                    else 0.0
                ),

            "max_deployed_pct":
                (
                    round(
                        float(
                            periods[
                                "deployed_pct"
                            ].max()
                        ),
                        2,
                    )
                    if not periods.empty
                    else 0.0
                ),
        }

    # =========================================================
    # FULL WALK FORWARD
    # =========================================================

    def run(
        self,
        symbols: list[str],
        start_date: str = "2022-01-01",
        end_date: str = "2026-08-20",
        debug: bool = True,
    ) -> dict:

        end = pd.Timestamp(
            end_date
        )

        selections = (
            walk_forward_selector.run(
                symbols=symbols,
                start_date=start_date,
                end_date=end_date,
                debug=False,
            )
        )

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
        all_allocations = []
        period_rows = []

        for i, period_start in enumerate(
            rebalance_dates
        ):

            if (
                i
                < len(rebalance_dates) - 1
            ):

                period_end = (
                    rebalance_dates[
                        i + 1
                    ]
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

            if selections.empty:

                period_selection = (
                    pd.DataFrame()
                )

            else:

                period_selection = (
                    selections[
                        selections[
                            "rebalance_date"
                        ]
                        == period_start
                    ]
                    .copy()
                )

            result = (
                self._run_period(
                    selections=
                        period_selection,

                    price_matrix=
                        price_matrix,

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

            curve = (
                result[
                    "equity_curve"
                ]
            )

            if not curve.empty:

                if portfolio_parts:

                    previous_last = (
                        portfolio_parts[
                            -1
                        ].index[-1]
                    )

                    curve = curve[
                        curve.index
                        > previous_last
                    ]

                portfolio_parts.append(
                    curve
                )

            if not result[
                "trades"
            ].empty:

                all_trades.append(
                    result["trades"]
                )

            allocations = (
                result[
                    "allocations"
                ]
            )

            if not allocations.empty:

                allocations = (
                    allocations.copy()
                )

                allocations[
                    "period_start"
                ] = period_start

                all_allocations.append(
                    allocations
                )

            deployed_pct = (
                result[
                    "deployed_capital"
                ]
                / result[
                    "starting_capital"
                ]
                * 100
                if result[
                    "starting_capital"
                ] > 0
                else 0.0
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

                    "deployed_capital":
                        round(
                            result[
                                "deployed_capital"
                            ],
                            2,
                        ),

                    "deployed_pct":
                        round(
                            deployed_pct,
                            2,
                        ),

                    "cash_capital":
                        round(
                            result[
                                "cash_capital"
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

        if portfolio_parts:

            equity_curve = (
                pd.concat(
                    portfolio_parts
                )
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

        allocations_df = (
            pd.concat(
                all_allocations,
                ignore_index=True,
            )
            if all_allocations
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

                periods=
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

            "allocations":
                allocations_df,

            "raw_selections":
                selections,
        }


risk_sized_walk_forward_portfolio = (
    RiskSizedWalkForwardPortfolioBacktester()
)