import numpy as np
import pandas as pd


class EntryQualityScorer:
    """
    Exploratory entry-quality score.

    IMPORTANT:
    This score is NOT trained on future returns.

    The transformations are based only on current trade features.
    A high score means the entry looks statistically/risk-wise cleaner.

    This version is for research diagnostics, not yet a production filter.
    """

    def __init__(self):
        self.feature_weights = {
            "entry_abs_z": 0.30,
            "beta_drift_pct": 0.30,
            "vol_regime_ratio": 0.20,
            "correlation_60d": 0.20,
        }

    # =========================================================
    # HELPERS
    # =========================================================

    @staticmethod
    def _safe_numeric(
        series: pd.Series,
    ) -> pd.Series:

        return pd.to_numeric(
            series,
            errors="coerce",
        )

    @staticmethod
    def _percentile_rank(
        series: pd.Series,
    ) -> pd.Series:

        numeric = pd.to_numeric(
            series,
            errors="coerce",
        )

        return numeric.rank(
            method="average",
            pct=True,
        )

    # =========================================================
    # SCORE
    # =========================================================

    def score(
        self,
        trades: pd.DataFrame,
    ) -> pd.DataFrame:

        if trades is None or trades.empty:
            return pd.DataFrame()

        df = trades.copy()

        required = [
            "entry_spread_z",
            "beta_drift_pct",
            "vol_regime_ratio",
            "correlation_60d",
        ]

        missing = [
            feature
            for feature in required
            if feature not in df.columns
        ]

        if missing:
            raise ValueError(
                f"Missing quality-score features: {missing}"
            )

        # -----------------------------------------
        # ABS Z
        # -----------------------------------------

        df["entry_abs_z"] = (
            self._safe_numeric(
                df["entry_spread_z"]
            ).abs()
        )

        # Lower is better
        z_rank = self._percentile_rank(
            df["entry_abs_z"]
        )

        df["quality_z_score"] = (
            1.0 - z_rank
        )

        # -----------------------------------------
        # BETA DRIFT
        # -----------------------------------------

        beta_drift_rank = (
            self._percentile_rank(
                df["beta_drift_pct"]
            )
        )

        # Lower is better
        df["quality_beta_score"] = (
            1.0 - beta_drift_rank
        )

        # -----------------------------------------
        # VOLATILITY REGIME
        # -----------------------------------------

        vol_rank = self._percentile_rank(
            df["vol_regime_ratio"]
        )

        # Lower recent volatility expansion
        # is considered cleaner
        df["quality_vol_score"] = (
            1.0 - vol_rank
        )

        # -----------------------------------------
        # CORRELATION
        # -----------------------------------------

        corr_rank = self._percentile_rank(
            df["correlation_60d"]
        )

        # Higher is better
        df["quality_corr_score"] = (
            corr_rank
        )

        # -----------------------------------------
        # COMBINED SCORE
        # -----------------------------------------

        df["entry_quality_score"] = (
            self.feature_weights[
                "entry_abs_z"
            ]
            * df["quality_z_score"]

            + self.feature_weights[
                "beta_drift_pct"
            ]
            * df["quality_beta_score"]

            + self.feature_weights[
                "vol_regime_ratio"
            ]
            * df["quality_vol_score"]

            + self.feature_weights[
                "correlation_60d"
            ]
            * df["quality_corr_score"]
        )

        df["entry_quality_score"] *= 100.0

        return df


entry_quality_scorer = (
    EntryQualityScorer()
)