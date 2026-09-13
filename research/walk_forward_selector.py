from dataclasses import dataclass
from itertools import combinations

import numpy as np
import pandas as pd

from research.pair_stability import PairStabilityAnalyzer


@dataclass(frozen=True)
class WalkForwardSelectionConfig:
    # Pair seçiminde yalnızca geçmiş yaklaşık 3 yıllık veri
    lookback_bars: int = 756

    # Her çeyrekte yeniden seçim
    rebalance_frequency: str = "QS"

    # Portföye en fazla kaç pair alınacak?
    top_n_pairs: int = 5

    # Minimum stability
    min_stability_pct: float = 10.0

    # En az kaç rolling pencere incelenmiş olmalı?
    min_windows_tested: int = 20

    # Beta aşırı dengesiz olmasın
    max_beta_cv: float = 1.5

    # Half-life çok uzun olmasın
    max_median_half_life: float = 100.0


class WalkForwardPairSelector:
    def __init__(
        self,
        config: WalkForwardSelectionConfig | None = None,
    ):
        self.config = (
            config
            or WalkForwardSelectionConfig()
        )

        self.stability_analyzer = (
            PairStabilityAnalyzer(
                window=300,
                step=21,
                max_coint_pvalue=0.05,
                max_adf_pvalue=0.05,
                max_half_life=100.0,
            )
        )

    # =========================================================
    # SINGLE REBALANCE DATE
    # =========================================================

    def select_for_date(
        self,
        price_matrix: pd.DataFrame,
        symbols: list[str],
        rebalance_date: pd.Timestamp,
        debug: bool = False,
    ) -> pd.DataFrame:
        """
        Select pairs using ONLY data strictly before
        rebalance_date.

        No future observations are used.
        """

        rebalance_date = pd.Timestamp(
            rebalance_date
        )

        historical = (
            price_matrix.loc[
                price_matrix.index
                < rebalance_date
            ]
            .copy()
        )

        if len(historical) == 0:
            return pd.DataFrame()

        # Use only trailing history
        historical = (
            historical.iloc[
                -self.config.lookback_bars:
            ]
        )

        rows = []

        pair_list = list(
            combinations(
                symbols,
                2,
            )
        )

        for stock_a, stock_b in pair_list:

            if (
                stock_a
                not in historical.columns
                or stock_b
                not in historical.columns
            ):
                continue

            pair_data = (
                historical[
                    [stock_a, stock_b]
                ]
                .dropna()
            )

            if len(pair_data) < 300:
                continue

            result = (
                self.stability_analyzer
                .analyze_pair(
                    stock_a,
                    stock_b,
                    pair_data,
                )
            )

            if (
                result.get(
                    "windows_tested",
                    0,
                )
                < self.config
                .min_windows_tested
            ):
                continue

            stability_pct = float(
                result.get(
                    "stability_pct",
                    0.0,
                )
            )

            beta_cv = result.get(
                "beta_cv",
                np.nan,
            )

            median_half_life = (
                result.get(
                    "median_half_life",
                    np.nan,
                )
            )

            latest_valid = bool(
                result.get(
                    "latest_valid",
                    False,
                )
            )

            # ---------------------------------------------
            # HARD FILTERS
            # ---------------------------------------------

            if (
                stability_pct
                < self.config
                .min_stability_pct
            ):
                continue

            if not latest_valid:
                continue

            if (
                pd.isna(beta_cv)
                or beta_cv
                > self.config.max_beta_cv
            ):
                continue

            if (
                pd.isna(
                    median_half_life
                )
                or median_half_life
                > self.config
                .max_median_half_life
            ):
                continue

            # ---------------------------------------------
            # SIMPLE RESEARCH SCORE
            # ---------------------------------------------
            #
            # Higher stability = better
            # Lower beta CV = better
            # Lower p-values = better
            #
            # This is deliberately simple for now.
            # ---------------------------------------------

            median_coint = float(
                result.get(
                    "median_coint_pvalue",
                    1.0,
                )
            )

            median_adf = float(
                result.get(
                    "median_adf_pvalue",
                    1.0,
                )
            )

            stability_component = (
                stability_pct
            )

            beta_component = (
                max(
                    0.0,
                    20.0
                    * (
                        1.0
                        - min(
                            beta_cv,
                            1.0,
                        )
                    ),
                )
            )

            coint_component = (
                max(
                    0.0,
                    20.0
                    * (
                        1.0
                        - min(
                            median_coint
                            / 0.05,
                            1.0,
                        )
                    ),
                )
            )

            adf_component = (
                max(
                    0.0,
                    20.0
                    * (
                        1.0
                        - min(
                            median_adf
                            / 0.05,
                            1.0,
                        )
                    ),
                )
            )

            selection_score = (
                stability_component
                + beta_component
                + coint_component
                + adf_component
            )

            rows.append(
                {
                    "rebalance_date":
                        rebalance_date,

                    "pair":
                        f"{stock_a}/{stock_b}",

                    "stock_a":
                        stock_a,

                    "stock_b":
                        stock_b,

                    "selection_score":
                        round(
                            selection_score,
                            3,
                        ),

                    "stability_pct":
                        stability_pct,

                    "windows_tested":
                        result[
                            "windows_tested"
                        ],

                    "valid_windows":
                        result[
                            "valid_windows"
                        ],

                    "median_coint_pvalue":
                        median_coint,

                    "median_adf_pvalue":
                        median_adf,

                    "median_beta":
                        result.get(
                            "median_beta",
                            np.nan,
                        ),

                    "beta_cv":
                        beta_cv,

                    "median_half_life":
                        median_half_life,

                    "latest_valid":
                        latest_valid,
                }
            )

        result_df = pd.DataFrame(
            rows
        )

        if result_df.empty:
            if debug:
                print(
                    f"{rebalance_date.date()} | "
                    "No eligible pairs."
                )

            return result_df

        result_df = (
            result_df
            .sort_values(
                [
                    "selection_score",
                    "stability_pct",
                ],
                ascending=False,
            )
            .head(
                self.config.top_n_pairs
            )
            .reset_index(
                drop=True
            )
        )

        if debug:
            print(
                f"\nREBALANCE "
                f"{rebalance_date.date()}"
            )

            print(
                result_df[
                    [
                        "pair",
                        "selection_score",
                        "stability_pct",
                        "beta_cv",
                        "median_half_life",
                    ]
                ].to_string(
                    index=False
                )
            )

        return result_df

    # =========================================================
    # CREATE REBALANCE DATES
    # =========================================================

    @staticmethod
    def _get_rebalance_dates(
        price_matrix: pd.DataFrame,
        start_date: str,
        end_date: str,
    ) -> list[pd.Timestamp]:

        start = pd.Timestamp(
            start_date
        )

        end = pd.Timestamp(
            end_date
        )

        quarter_starts = (
            pd.date_range(
                start=start,
                end=end,
                freq="QS",
            )
        )

        trading_dates = (
            price_matrix.index
        )

        rebalance_dates = []

        for quarter_date in (
            quarter_starts
        ):
            available_dates = (
                trading_dates[
                    trading_dates
                    >= quarter_date
                ]
            )

            if len(
                available_dates
            ) == 0:
                continue

            actual_date = (
                available_dates[0]
            )

            if actual_date > end:
                continue

            rebalance_dates.append(
                actual_date
            )

        return sorted(
            set(
                rebalance_dates
            )
        )

    # =========================================================
    # FULL WALK-FORWARD SELECTION
    # =========================================================

    def run(
        self,
        symbols: list[str],
        start_date: str = "2022-01-01",
        end_date: str = "2026-08-20",
        debug: bool = True,
    ) -> pd.DataFrame:
        """
        Perform quarterly walk-forward pair selection.

        Each rebalance date uses ONLY data available
        before that date.
        """

        price_matrix = (
            self.stability_analyzer
            ._load_prices(
                symbols
            )
        )

        rebalance_dates = (
            self._get_rebalance_dates(
                price_matrix,
                start_date,
                end_date,
            )
        )

        all_selections = []

        for idx, date in enumerate(
            rebalance_dates,
            start=1,
        ):
            if debug:
                print(
                    "\n"
                    + "=" * 70
                )

                print(
                    f"[{idx}/"
                    f"{len(rebalance_dates)}]"
                )

            selected = (
                self.select_for_date(
                    price_matrix=
                        price_matrix,

                    symbols=
                        symbols,

                    rebalance_date=
                        date,

                    debug=
                        debug,
                )
            )

            if not selected.empty:
                all_selections.append(
                    selected
                )

        if not all_selections:
            return pd.DataFrame()

        selections = pd.concat(
            all_selections,
            ignore_index=True,
        )

        return selections


walk_forward_selector = (
    WalkForwardPairSelector()
)