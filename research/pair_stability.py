import itertools

import numpy as np
import pandas as pd
import statsmodels.api as sm
from sqlalchemy import text
from statsmodels.tsa.stattools import adfuller, coint

from config.database import db


class PairStabilityAnalyzer:
    def __init__(
        self,
        window: int = 300,
        step: int = 21,
        max_coint_pvalue: float = 0.05,
        max_adf_pvalue: float = 0.05,
        max_half_life: float = 100.0,
    ):
        self.window = window
        self.step = step

        self.max_coint_pvalue = (
            max_coint_pvalue
        )

        self.max_adf_pvalue = (
            max_adf_pvalue
        )

        self.max_half_life = (
            max_half_life
        )

    # =========================================================
    # DATA
    # =========================================================

    def _load_prices(
        self,
        symbols: list[str],
    ) -> pd.DataFrame:
        """
        Load daily close prices for all requested symbols
        with a single database query.
        """

        placeholders = ", ".join(
            f":symbol_{i}"
            for i in range(len(symbols))
        )

        query = text(
            f"""
            SELECT
                date,
                symbol,
                close
            FROM stock_prices_daily
            WHERE symbol IN ({placeholders})
            ORDER BY date
            """
        )

        params = {
            f"symbol_{i}": symbol
            for i, symbol in enumerate(symbols)
        }

        with db.engine.connect() as conn:
            df = pd.read_sql(
                query,
                conn,
                params=params,
            )

        if df.empty:
            raise ValueError(
                "No daily price data found."
            )

        df["date"] = pd.to_datetime(
            df["date"]
        )

        df["close"] = pd.to_numeric(
            df["close"],
            errors="coerce",
        )

        price_matrix = (
            df
            .pivot(
                index="date",
                columns="symbol",
                values="close",
            )
            .sort_index()
        )

        return price_matrix

    # =========================================================
    # HALF LIFE
    # =========================================================

    @staticmethod
    def _calculate_half_life(
        spread: pd.Series,
    ) -> float:
        lagged = spread.shift(1)

        delta = (
            spread - lagged
        )

        regression_data = pd.DataFrame(
            {
                "lagged": lagged,
                "delta": delta,
            }
        ).dropna()

        if len(regression_data) < 20:
            return float("inf")

        x = sm.add_constant(
            regression_data["lagged"]
        )

        y = regression_data["delta"]

        try:
            model = sm.OLS(
                y,
                x,
            ).fit()

            beta = float(
                model.params["lagged"]
            )

        except Exception:
            return float("inf")

        if beta >= 0:
            return float("inf")

        half_life = (
            -np.log(2) / beta
        )

        if (
            not np.isfinite(half_life)
            or half_life <= 0
        ):
            return float("inf")

        return float(half_life)

    # =========================================================
    # SINGLE WINDOW
    # =========================================================

    def _analyze_window(
        self,
        price_a: pd.Series,
        price_b: pd.Series,
    ) -> dict | None:

        data = pd.concat(
            [price_a, price_b],
            axis=1,
        ).dropna()

        if len(data) < self.window:
            return None

        a = data.iloc[:, 0].astype(float)
        b = data.iloc[:, 1].astype(float)

        if (
            (a <= 0).any()
            or (b <= 0).any()
        ):
            return None

        log_a = np.log(a)
        log_b = np.log(b)

        # -----------------------------------------------------
        # Cointegration
        # -----------------------------------------------------

        try:
            _, coint_pvalue, _ = coint(
                log_a,
                log_b,
            )

        except Exception:
            return None

        # -----------------------------------------------------
        # Hedge ratio
        # -----------------------------------------------------

        x = sm.add_constant(
            log_b
        )

        try:
            model = sm.OLS(
                log_a,
                x,
            ).fit()

        except Exception:
            return None

        beta = float(
            model.params.iloc[1]
        )

        alpha = float(
            model.params.iloc[0]
        )

        fitted = model.predict(x)

        spread = (
            log_a - fitted
        ).dropna()

        if len(spread) < 30:
            return None

        # -----------------------------------------------------
        # ADF
        # -----------------------------------------------------

        try:
            adf_pvalue = float(
                adfuller(
                    spread,
                    autolag="AIC",
                )[1]
            )

        except Exception:
            return None

        half_life = (
            self._calculate_half_life(
                spread
            )
        )

        valid = (
            coint_pvalue
            <= self.max_coint_pvalue
            and adf_pvalue
            <= self.max_adf_pvalue
            and beta > 0
            and np.isfinite(half_life)
            and half_life
            <= self.max_half_life
        )

        return {
            "alpha": alpha,
            "beta": beta,

            "coint_pvalue":
                float(coint_pvalue),

            "adf_pvalue":
                float(adf_pvalue),

            "half_life":
                float(half_life),

            "valid":
                bool(valid),
        }

    # =========================================================
    # SINGLE PAIR
    # =========================================================

    def analyze_pair(
        self,
        stock_a: str,
        stock_b: str,
        price_matrix: pd.DataFrame,
    ) -> dict:

        pair_data = (
            price_matrix[
                [stock_a, stock_b]
            ]
            .dropna()
        )

        if len(pair_data) < self.window:
            return {
                "pair":
                    f"{stock_a}/{stock_b}",

                "windows_tested":
                    0,

                "valid_windows":
                    0,

                "stability_pct":
                    0.0,
            }

        windows = []

        # We evaluate approximately once per month
        # instead of re-running expensive tests every day.
        for end in range(
            self.window,
            len(pair_data) + 1,
            self.step,
        ):
            window_data = (
                pair_data.iloc[
                    end - self.window:end
                ]
            )

            result = (
                self._analyze_window(
                    window_data[stock_a],
                    window_data[stock_b],
                )
            )

            if result is None:
                continue

            result["date"] = (
                window_data.index[-1]
            )

            windows.append(
                result
            )

        if not windows:
            return {
                "pair":
                    f"{stock_a}/{stock_b}",

                "windows_tested":
                    0,

                "valid_windows":
                    0,

                "stability_pct":
                    0.0,
            }

        windows_df = pd.DataFrame(
            windows
        )

        windows_tested = len(
            windows_df
        )

        valid_windows = int(
            windows_df[
                "valid"
            ].sum()
        )

        stability_pct = (
            valid_windows
            / windows_tested
            * 100
        )

        positive_betas = (
            windows_df[
                windows_df["beta"] > 0
            ]["beta"]
        )

        if len(positive_betas) > 1:
            beta_mean = float(
                positive_betas.mean()
            )

            beta_std = float(
                positive_betas.std()
            )

            if beta_mean != 0:
                beta_cv = (
                    beta_std
                    / abs(beta_mean)
                )
            else:
                beta_cv = float("inf")

        else:
            beta_cv = float("inf")

        finite_half_lives = (
            windows_df[
                np.isfinite(
                    windows_df[
                        "half_life"
                    ]
                )
            ]["half_life"]
        )

        latest = (
            windows_df.iloc[-1]
        )

        return {
            "pair":
                f"{stock_a}/{stock_b}",

            "stock_a":
                stock_a,

            "stock_b":
                stock_b,

            "windows_tested":
                windows_tested,

            "valid_windows":
                valid_windows,

            "stability_pct":
                round(
                    stability_pct,
                    2,
                ),

            "median_coint_pvalue":
                round(
                    float(
                        windows_df[
                            "coint_pvalue"
                        ].median()
                    ),
                    4,
                ),

            "median_adf_pvalue":
                round(
                    float(
                        windows_df[
                            "adf_pvalue"
                        ].median()
                    ),
                    4,
                ),

            "median_beta":
                round(
                    float(
                        positive_betas.median()
                    ),
                    4,
                )
                if not positive_betas.empty
                else np.nan,

            "beta_cv":
                round(
                    float(beta_cv),
                    4,
                )
                if np.isfinite(beta_cv)
                else np.nan,

            "median_half_life":
                round(
                    float(
                        finite_half_lives.median()
                    ),
                    2,
                )
                if not finite_half_lives.empty
                else np.nan,

            "latest_valid":
                bool(
                    latest["valid"]
                ),

            "latest_coint_pvalue":
                round(
                    float(
                        latest[
                            "coint_pvalue"
                        ]
                    ),
                    4,
                ),

            "latest_adf_pvalue":
                round(
                    float(
                        latest[
                            "adf_pvalue"
                        ]
                    ),
                    4,
                ),

            "latest_beta":
                round(
                    float(
                        latest["beta"]
                    ),
                    4,
                ),

            "latest_half_life":
                (
                    round(
                        float(
                            latest[
                                "half_life"
                            ]
                        ),
                        2,
                    )
                    if np.isfinite(
                        latest[
                            "half_life"
                        ]
                    )
                    else np.nan
                ),
        }

    # =========================================================
    # FULL UNIVERSE
    # =========================================================

    def run(
        self,
        symbols: list[str],
        debug: bool = True,
    ) -> pd.DataFrame:

        price_matrix = (
            self._load_prices(
                symbols
            )
        )

        pairs = list(
            itertools.combinations(
                symbols,
                2,
            )
        )

        results = []

        total_pairs = len(
            pairs
        )

        for idx, (
            stock_a,
            stock_b,
        ) in enumerate(
            pairs,
            start=1,
        ):
            result = (
                self.analyze_pair(
                    stock_a,
                    stock_b,
                    price_matrix,
                )
            )

            results.append(
                result
            )

            if debug:
                print(
                    f"[{idx}/{total_pairs}] "
                    f"{result['pair']:<12} | "
                    f"windows="
                    f"{result['windows_tested']:<3} | "
                    f"valid="
                    f"{result['valid_windows']:<3} | "
                    f"stability="
                    f"{result['stability_pct']:>6.2f}%"
                )

        results_df = pd.DataFrame(
            results
        )

        if not results_df.empty:
            results_df = (
                results_df
                .sort_values(
                    [
                        "stability_pct",
                        "valid_windows",
                    ],
                    ascending=False,
                )
                .reset_index(
                    drop=True
                )
            )

        return results_df


pair_stability_analyzer = (
    PairStabilityAnalyzer()
)