import numpy as np
import pandas as pd


class TradeFeatureAnalyzer:
    ENTRY_FEATURES = [
        "entry_abs_z",
        "stability_pct",
        "beta",
        "half_life",
    ]

    # =========================================================
    # PREPARE
    # =========================================================

    def prepare_trades(
        self,
        trades: pd.DataFrame,
    ) -> pd.DataFrame:

        if trades is None or trades.empty:
            return pd.DataFrame()

        df = trades.copy()

        required = [
            "net_return",
            "entry_z",
            "stability_pct",
            "beta",
            "half_life",
        ]

        missing = [
            column
            for column in required
            if column not in df.columns
        ]

        if missing:
            raise ValueError(
                f"Missing trade columns: {missing}"
            )

        df["entry_abs_z"] = (
            df["entry_z"]
            .astype(float)
            .abs()
        )

        df["winner"] = (
            df["net_return"] > 0
        )

        df["loser"] = (
            df["net_return"] < 0
        )

        df["net_return_pct"] = (
            df["net_return"]
            .astype(float)
            * 100
        )

        if "entry_time" in df.columns:
            df["entry_time"] = pd.to_datetime(
                df["entry_time"]
            )

        return df

    # =========================================================
    # OVERALL SUMMARY
    # =========================================================

    def overall_summary(
        self,
        trades: pd.DataFrame,
    ) -> dict:

        df = self.prepare_trades(
            trades
        )

        if df.empty:
            return {}

        winners = df[
            df["winner"]
        ]

        losers = df[
            df["loser"]
        ]

        return {
            "trades":
                len(df),

            "winners":
                len(winners),

            "losers":
                len(losers),

            "win_rate_pct":
                round(
                    len(winners)
                    / len(df)
                    * 100,
                    2,
                ),

            "average_trade_pct":
                round(
                    float(
                        df[
                            "net_return_pct"
                        ].mean()
                    ),
                    3,
                ),

            "median_trade_pct":
                round(
                    float(
                        df[
                            "net_return_pct"
                        ].median()
                    ),
                    3,
                ),

            "average_winner_pct":
                round(
                    float(
                        winners[
                            "net_return_pct"
                        ].mean()
                    ),
                    3,
                )
                if not winners.empty
                else 0.0,

            "average_loser_pct":
                round(
                    float(
                        losers[
                            "net_return_pct"
                        ].mean()
                    ),
                    3,
                )
                if not losers.empty
                else 0.0,
        }

    # =========================================================
    # WINNER VS LOSER FEATURES
    # =========================================================

    def feature_comparison(
        self,
        trades: pd.DataFrame,
    ) -> pd.DataFrame:

        df = self.prepare_trades(
            trades
        )

        if df.empty:
            return pd.DataFrame()

        winners = df[
            df["winner"]
        ]

        losers = df[
            df["loser"]
        ]

        rows = []

        for feature in self.ENTRY_FEATURES:

            winner_mean = (
                float(
                    winners[
                        feature
                    ].mean()
                )
                if not winners.empty
                else np.nan
            )

            loser_mean = (
                float(
                    losers[
                        feature
                    ].mean()
                )
                if not losers.empty
                else np.nan
            )

            winner_median = (
                float(
                    winners[
                        feature
                    ].median()
                )
                if not winners.empty
                else np.nan
            )

            loser_median = (
                float(
                    losers[
                        feature
                    ].median()
                )
                if not losers.empty
                else np.nan
            )

            pooled_std = float(
                df[
                    feature
                ].std()
            )

            if (
                np.isfinite(
                    pooled_std
                )
                and pooled_std > 0
            ):
                standardized_difference = (
                    winner_mean
                    - loser_mean
                ) / pooled_std

            else:
                standardized_difference = (
                    np.nan
                )

            correlation = (
                df[
                    [
                        feature,
                        "net_return",
                    ]
                ]
                .corr()
                .iloc[0, 1]
            )

            rows.append(
                {
                    "feature":
                        feature,

                    "winner_mean":
                        winner_mean,

                    "loser_mean":
                        loser_mean,

                    "winner_median":
                        winner_median,

                    "loser_median":
                        loser_median,

                    "standardized_diff":
                        standardized_difference,

                    "return_correlation":
                        correlation,
                }
            )

        result = pd.DataFrame(
            rows
        )

        return (
            result
            .sort_values(
                "standardized_diff",
                key=lambda x: x.abs(),
                ascending=False,
            )
            .reset_index(
                drop=True
            )
        )

    # =========================================================
    # FEATURE QUANTILES
    # =========================================================

    def quantile_analysis(
        self,
        trades: pd.DataFrame,
        bins: int = 3,
    ) -> pd.DataFrame:

        df = self.prepare_trades(
            trades
        )

        if df.empty:
            return pd.DataFrame()

        rows = []

        for feature in self.ENTRY_FEATURES:

            clean = (
                df[
                    [
                        feature,
                        "net_return",
                    ]
                ]
                .dropna()
                .copy()
            )

            if (
                clean.empty
                or clean[
                    feature
                ].nunique()
                < 2
            ):
                continue

            try:
                clean[
                    "bucket"
                ] = pd.qcut(
                    clean[
                        feature
                    ],
                    q=min(
                        bins,
                        clean[
                            feature
                        ].nunique(),
                    ),
                    duplicates="drop",
                )

            except ValueError:
                continue

            grouped = (
                clean
                .groupby(
                    "bucket",
                    observed=True,
                )
            )

            for bucket, group in grouped:

                rows.append(
                    {
                        "feature":
                            feature,

                        "bucket":
                            str(bucket),

                        "trades":
                            len(group),

                        "win_rate_pct":
                            (
                                group[
                                    "net_return"
                                ]
                                .gt(0)
                                .mean()
                                * 100
                            ),

                        "avg_return_pct":
                            (
                                group[
                                    "net_return"
                                ]
                                .mean()
                                * 100
                            ),

                        "median_return_pct":
                            (
                                group[
                                    "net_return"
                                ]
                                .median()
                                * 100
                            ),

                        "worst_trade_pct":
                            (
                                group[
                                    "net_return"
                                ]
                                .min()
                                * 100
                            ),
                    }
                )

        return pd.DataFrame(
            rows
        )

    # =========================================================
    # STOP ANALYSIS
    # =========================================================

    def stop_feature_comparison(
        self,
        trades: pd.DataFrame,
    ) -> pd.DataFrame:

        df = self.prepare_trades(
            trades
        )

        if (
            df.empty
            or "exit_reason"
            not in df.columns
        ):
            return pd.DataFrame()

        df["stopped"] = (
            df[
                "exit_reason"
            ]
            .eq("STOP")
        )

        stopped = df[
            df["stopped"]
        ]

        not_stopped = df[
            ~df["stopped"]
        ]

        rows = []

        for feature in self.ENTRY_FEATURES:

            rows.append(
                {
                    "feature":
                        feature,

                    "stop_mean":
                        (
                            float(
                                stopped[
                                    feature
                                ].mean()
                            )
                            if not stopped.empty
                            else np.nan
                        ),

                    "non_stop_mean":
                        (
                            float(
                                not_stopped[
                                    feature
                                ].mean()
                            )
                            if not not_stopped.empty
                            else np.nan
                        ),

                    "stop_median":
                        (
                            float(
                                stopped[
                                    feature
                                ].median()
                            )
                            if not stopped.empty
                            else np.nan
                        ),

                    "non_stop_median":
                        (
                            float(
                                not_stopped[
                                    feature
                                ].median()
                            )
                            if not not_stopped.empty
                            else np.nan
                        ),
                }
            )

        return pd.DataFrame(
            rows
        )

    # =========================================================
    # COMPLETE REPORT
    # =========================================================

    def analyze(
        self,
        trades: pd.DataFrame,
    ) -> dict:

        return {
            "summary":
                self.overall_summary(
                    trades
                ),

            "feature_comparison":
                self.feature_comparison(
                    trades
                ),

            "quantiles":
                self.quantile_analysis(
                    trades
                ),

            "stop_comparison":
                self.stop_feature_comparison(
                    trades
                ),
        }


trade_feature_analyzer = (
    TradeFeatureAnalyzer()
)