from typing import ClassVar

import numpy as np
import pandas as pd


class EntryFeatureDiagnostics:
    FEATURES: ClassVar[list[str]] = [
        "entry_coint_pvalue",
        "entry_adf_pvalue",
        "entry_spread_z",
        "pair_vol_20d",
        "pair_vol_60d",
        "vol_regime_ratio",
        "correlation_60d",
        "beta_drift_pct",
    ]

    def _prepare(
        self,
        trades: pd.DataFrame,
    ) -> pd.DataFrame:

        if trades is None or trades.empty:
            return pd.DataFrame()

        df = trades.copy()

        df["entry_abs_z"] = (
            pd.to_numeric(
                df["entry_spread_z"],
                errors="coerce",
            ).abs()
        )

        df["winner"] = (
            pd.to_numeric(
                df["net_return"],
                errors="coerce",
            ) > 0
        )

        df["stopped"] = (
            df["exit_reason"] == "STOP"
        )

        return df

    def _standardized_difference(
        self,
        group_a: pd.Series,
        group_b: pd.Series,
    ) -> float:

        group_a = pd.to_numeric(
            group_a,
            errors="coerce",
        ).dropna()

        group_b = pd.to_numeric(
            group_b,
            errors="coerce",
        ).dropna()

        if len(group_a) < 2 or len(group_b) < 2:
            return np.nan

        mean_a = group_a.mean()
        mean_b = group_b.mean()

        var_a = group_a.var(ddof=1)
        var_b = group_b.var(ddof=1)

        pooled_var = (
            (
                (len(group_a) - 1) * var_a
                + (len(group_b) - 1) * var_b
            )
            / (
                len(group_a)
                + len(group_b)
                - 2
            )
        )

        if (
            not np.isfinite(pooled_var)
            or pooled_var <= 0
        ):
            return np.nan

        pooled_std = np.sqrt(
            pooled_var
        )

        return float(
            (
                mean_a
                - mean_b
            )
            / pooled_std
        )

    def _group_comparison(
        self,
        df: pd.DataFrame,
        mask_a: pd.Series,
        mask_b: pd.Series,
        label_a: str,
        label_b: str,
    ) -> pd.DataFrame:

        features = [
            "entry_abs_z",
            "entry_coint_pvalue",
            "entry_adf_pvalue",
            "pair_vol_20d",
            "pair_vol_60d",
            "vol_regime_ratio",
            "correlation_60d",
            "beta_drift_pct",
        ]

        rows = []

        for feature in features:

            a = pd.to_numeric(
                df.loc[
                    mask_a,
                    feature,
                ],
                errors="coerce",
            ).dropna()

            b = pd.to_numeric(
                df.loc[
                    mask_b,
                    feature,
                ],
                errors="coerce",
            ).dropna()

            rows.append(
                {
                    "feature":
                        feature,

                    f"{label_a}_count":
                        len(a),

                    f"{label_b}_count":
                        len(b),

                    f"{label_a}_mean":
                        a.mean()
                        if len(a)
                        else np.nan,

                    f"{label_b}_mean":
                        b.mean()
                        if len(b)
                        else np.nan,

                    f"{label_a}_median":
                        a.median()
                        if len(a)
                        else np.nan,

                    f"{label_b}_median":
                        b.median()
                        if len(b)
                        else np.nan,

                    "standardized_diff":
                        self._standardized_difference(
                            a,
                            b,
                        ),
                }
            )

        result = pd.DataFrame(
            rows
        )

        result["abs_standardized_diff"] = (
            result[
                "standardized_diff"
            ].abs()
        )

        return (
            result
            .sort_values(
                "abs_standardized_diff",
                ascending=False,
            )
            .reset_index(
                drop=True
            )
        )

    def winner_vs_loser(
        self,
        trades: pd.DataFrame,
    ) -> pd.DataFrame:

        df = self._prepare(
            trades
        )

        if df.empty:
            return pd.DataFrame()

        return self._group_comparison(
            df=df,
            mask_a=df["winner"],
            mask_b=~df["winner"],
            label_a="winner",
            label_b="loser",
        )

    def stop_vs_non_stop(
        self,
        trades: pd.DataFrame,
    ) -> pd.DataFrame:

        df = self._prepare(
            trades
        )

        if df.empty:
            return pd.DataFrame()

        return self._group_comparison(
            df=df,
            mask_a=df["stopped"],
            mask_b=~df["stopped"],
            label_a="stop",
            label_b="non_stop",
        )

    def return_correlations(
        self,
        trades: pd.DataFrame,
    ) -> pd.DataFrame:

        df = self._prepare(
            trades
        )

        if df.empty:
            return pd.DataFrame()

        df["net_return"] = pd.to_numeric(
            df["net_return"],
            errors="coerce",
        )

        features = [
            "entry_abs_z",
            "entry_coint_pvalue",
            "entry_adf_pvalue",
            "pair_vol_20d",
            "pair_vol_60d",
            "vol_regime_ratio",
            "correlation_60d",
            "beta_drift_pct",
        ]

        rows = []

        for feature in features:

            clean = (
                df[
                    [
                        feature,
                        "net_return",
                    ]
                ]
                .apply(
                    pd.to_numeric,
                    errors="coerce",
                )
                .dropna()
            )

            if len(clean) < 3:
                corr = np.nan
            else:
                corr = clean.corr().iloc[
                    0,
                    1,
                ]

            rows.append(
                {
                    "feature":
                        feature,

                    "correlation_with_return":
                        corr,
                }
            )

        result = pd.DataFrame(
            rows
        )

        result["abs_correlation"] = (
            result[
                "correlation_with_return"
            ].abs()
        )

        return (
            result
            .sort_values(
                "abs_correlation",
                ascending=False,
            )
            .reset_index(
                drop=True
            )
        )

    def quantile_analysis(
        self,
        trades: pd.DataFrame,
        bins: int = 3,
    ) -> pd.DataFrame:

        df = self._prepare(
            trades
        )

        if df.empty:
            return pd.DataFrame()

        df["net_return"] = pd.to_numeric(
            df["net_return"],
            errors="coerce",
        )

        features = [
            "entry_abs_z",
            "entry_coint_pvalue",
            "entry_adf_pvalue",
            "pair_vol_20d",
            "pair_vol_60d",
            "vol_regime_ratio",
            "correlation_60d",
            "beta_drift_pct",
        ]

        rows = []

        for feature in features:

            clean = (
                df[
                    [
                        feature,
                        "net_return",
                        "exit_reason",
                    ]
                ]
                .copy()
            )

            clean[feature] = pd.to_numeric(
                clean[feature],
                errors="coerce",
            )

            clean["net_return"] = pd.to_numeric(
                clean["net_return"],
                errors="coerce",
            )

            clean = clean.dropna(
                subset=[
                    feature,
                    "net_return",
                ]
            )

            if (
                clean.empty
                or clean[feature].nunique() < 2
            ):
                continue

            try:
                clean["bucket"] = pd.qcut(
                    clean[feature],
                    q=min(
                        bins,
                        clean[feature].nunique(),
                    ),
                    duplicates="drop",
                )
            except ValueError:
                continue

            for bucket, group in clean.groupby(
                "bucket",
                observed=True,
            ):

                rows.append(
                    {
                        "feature":
                            feature,

                        "bucket":
                            str(bucket),

                        "trades":
                            len(group),

                        "win_rate_pct":
                            group[
                                "net_return"
                            ].gt(0).mean() * 100,

                        "stop_rate_pct":
                            group[
                                "exit_reason"
                            ].eq("STOP").mean() * 100,

                        "avg_return_pct":
                            group[
                                "net_return"
                            ].mean() * 100,

                        "median_return_pct":
                            group[
                                "net_return"
                            ].median() * 100,

                        "worst_trade_pct":
                            group[
                                "net_return"
                            ].min() * 100,
                    }
                )

        return pd.DataFrame(
            rows
        )

    def analyze(
        self,
        trades: pd.DataFrame,
    ) -> dict:

        return {
            "winner_vs_loser":
                self.winner_vs_loser(
                    trades
                ),

            "stop_vs_non_stop":
                self.stop_vs_non_stop(
                    trades
                ),

            "return_correlations":
                self.return_correlations(
                    trades
                ),

            "quantiles":
                self.quantile_analysis(
                    trades
                ),
        }


entry_feature_diagnostics = (
    EntryFeatureDiagnostics()
)