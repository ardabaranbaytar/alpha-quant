import logging

import numpy as np
import pandas as pd
from statsmodels.api import OLS, add_constant
from statsmodels.tsa.stattools import adfuller, coint

from config.database import db

logger = logging.getLogger(__name__)


class EntryFeatureEnricher:
    """
    Existing trades are enriched with information that would have been
    available strictly before each trade entry.

    No post-entry observations are used.
    """

    def __init__(
        self,
        model_lookback: int = 300,
        short_vol_lookback: int = 20,
        long_vol_lookback: int = 60,
        beta_drift_lookback: int = 60,
    ):
        self.model_lookback = model_lookback
        self.short_vol_lookback = short_vol_lookback
        self.long_vol_lookback = long_vol_lookback
        self.beta_drift_lookback = beta_drift_lookback

        self._price_cache = {}

    # =========================================================
    # PRICE DATA
    # =========================================================

    def _load_prices(
        self,
        symbol: str,
    ) -> pd.Series:

        if symbol in self._price_cache:
            return self._price_cache[symbol]

        query = """
            SELECT
                date,
                close
            FROM stock_prices_daily
            WHERE symbol = :symbol
            ORDER BY date
        """

        rows = db.execute_query(
            query,
            {
                "symbol": symbol,
            },
        )

        if not rows:
            series = pd.Series(
                dtype=float,
                name=symbol,
            )

            self._price_cache[symbol] = series
            return series

        df = pd.DataFrame(
            rows,
            columns=[
                "date",
                "close",
            ],
        )

        df["date"] = pd.to_datetime(
            df["date"]
        )

        df["close"] = pd.to_numeric(
            df["close"],
            errors="coerce",
        )

        df = (
            df
            .dropna(
                subset=["date", "close"]
            )
            .drop_duplicates(
                subset=["date"],
                keep="last",
            )
            .sort_values("date")
        )

        series = (
            df
            .set_index("date")["close"]
            .astype(float)
        )

        series.name = symbol

        self._price_cache[symbol] = series

        return series

    def _get_pair_prices(
        self,
        stock_a: str,
        stock_b: str,
        entry_time,
    ) -> pd.DataFrame:

        prices_a = self._load_prices(
            stock_a
        )

        prices_b = self._load_prices(
            stock_b
        )

        if prices_a.empty or prices_b.empty:
            return pd.DataFrame()

        entry_date = pd.Timestamp(
            entry_time
        ).normalize()

        pair = pd.concat(
            [
                prices_a.rename("a"),
                prices_b.rename("b"),
            ],
            axis=1,
            join="inner",
        ).dropna()

        # CRITICAL:
        # only information strictly before entry date
        pair = pair[
            pair.index < entry_date
        ]

        return pair

    # =========================================================
    # TRADE IDENTIFICATION
    # =========================================================

    def _extract_symbols(
        self,
        row: pd.Series,
    ):

        if (
            "stock_a" in row.index
            and "stock_b" in row.index
            and pd.notna(row["stock_a"])
            and pd.notna(row["stock_b"])
        ):
            return (
                str(row["stock_a"]),
                str(row["stock_b"]),
            )

        if (
            "pair" in row.index
            and pd.notna(row["pair"])
        ):
            parts = str(
                row["pair"]
            ).split("/")

            if len(parts) == 2:
                return (
                    parts[0],
                    parts[1],
                )

        raise ValueError(
            "Trade row must contain either "
            "'stock_a'/'stock_b' or 'pair'."
        )

    def _extract_entry_time(
        self,
        row: pd.Series,
    ):

        candidates = [
            "entry_time",
            "entry_date",
            "entry_execution_date",
        ]

        for column in candidates:
            if (
                column in row.index
                and pd.notna(row[column])
            ):
                return pd.Timestamp(
                    row[column]
                )

        raise ValueError(
            "Could not find trade entry date. "
            "Expected one of: "
            "entry_time, entry_date, entry_execution_date"
        )

    # =========================================================
    # MODEL FEATURES
    # =========================================================

    def _calculate_model_features(
        self,
        pair_prices: pd.DataFrame,
    ) -> dict:

        result = {
            "entry_coint_pvalue": np.nan,
            "entry_adf_pvalue": np.nan,
            "entry_model_beta": np.nan,
            "entry_spread_std": np.nan,
            "entry_spread_z": np.nan,
        }

        if len(pair_prices) < self.model_lookback:
            return result

        window = pair_prices.tail(
            self.model_lookback
        ).copy()

        if (
            (window["a"] <= 0).any()
            or (window["b"] <= 0).any()
        ):
            return result

        log_a = np.log(
            window["a"].astype(float)
        )

        log_b = np.log(
            window["b"].astype(float)
        )

        try:
            X = add_constant(
                log_b
            )

            model = OLS(
                log_a,
                X,
            ).fit()

            alpha = float(
                model.params.iloc[0]
            )

            beta = float(
                model.params.iloc[1]
            )

            spread = (
                log_a
                - alpha
                - beta * log_b
            )

            spread_std = float(
                spread.std(ddof=1)
            )

            if (
                np.isfinite(spread_std)
                and spread_std > 0
            ):
                spread_z = float(
                    (
                        spread.iloc[-1]
                        - spread.mean()
                    )
                    / spread_std
                )
            else:
                spread_z = np.nan

            _, coint_pvalue, _ = coint(
                log_a,
                log_b,
            )

            adf_result = adfuller(
                spread.dropna(),
                autolag="AIC",
            )

            adf_pvalue = float(
                adf_result[1]
            )

            result.update(
                {
                    "entry_coint_pvalue":
                        float(coint_pvalue),

                    "entry_adf_pvalue":
                        adf_pvalue,

                    "entry_model_beta":
                        beta,

                    "entry_spread_std":
                        spread_std,

                    "entry_spread_z":
                        spread_z,
                }
            )

        except Exception:
            logger.debug("Entry feature enrichment failed", exc_info=True)

        return result

    # =========================================================
    # VOLATILITY FEATURES
    # =========================================================

    def _calculate_volatility_features(
        self,
        pair_prices: pd.DataFrame,
        beta: float,
    ) -> dict:

        result = {
            "pair_vol_20d": np.nan,
            "pair_vol_60d": np.nan,
            "vol_regime_ratio": np.nan,
            "correlation_60d": np.nan,
        }

        if len(pair_prices) < (
            self.long_vol_lookback + 1
        ):
            return result

        prices = pair_prices.tail(
            self.long_vol_lookback + 1
        )

        returns = (
            prices
            .pct_change()
            .dropna()
        )

        if returns.empty:
            return result

        if (
            not np.isfinite(beta)
            or beta <= 0
        ):
            beta = 1.0

        # Same normalized gross exposure concept
        # currently used in the pair portfolio framework.
        weight_a = (
            1.0
            / (1.0 + beta)
        )

        weight_b = (
            beta
            / (1.0 + beta)
        )

        pair_returns = (
            weight_a * returns["a"]
            - weight_b * returns["b"]
        )

        long_vol = float(
            pair_returns.tail(
                self.long_vol_lookback
            ).std(ddof=1)
        )

        short_vol = float(
            pair_returns.tail(
                self.short_vol_lookback
            ).std(ddof=1)
        )

        correlation = float(
            returns[
                ["a", "b"]
            ]
            .tail(
                self.long_vol_lookback
            )
            .corr()
            .iloc[0, 1]
        )

        if (
            np.isfinite(long_vol)
            and long_vol > 0
        ):
            vol_regime_ratio = (
                short_vol / long_vol
            )
        else:
            vol_regime_ratio = np.nan

        result.update(
            {
                "pair_vol_20d":
                    short_vol,

                "pair_vol_60d":
                    long_vol,

                "vol_regime_ratio":
                    vol_regime_ratio,

                "correlation_60d":
                    correlation,
            }
        )

        return result

    # =========================================================
    # BETA DRIFT
    # =========================================================

    def _estimate_beta(
        self,
        prices: pd.DataFrame,
    ):

        if len(prices) < 30:
            return np.nan

        if (
            (prices["a"] <= 0).any()
            or (prices["b"] <= 0).any()
        ):
            return np.nan

        try:
            log_a = np.log(
                prices["a"].astype(float)
            )

            log_b = np.log(
                prices["b"].astype(float)
            )

            X = add_constant(
                log_b
            )

            model = OLS(
                log_a,
                X,
            ).fit()

            return float(
                model.params.iloc[1]
            )

        except Exception:  # noqa: BLE001
            return np.nan

    def _calculate_beta_drift(
        self,
        pair_prices: pd.DataFrame,
    ) -> dict:

        result = {
            "beta_recent": np.nan,
            "beta_previous": np.nan,
            "beta_drift_abs": np.nan,
            "beta_drift_pct": np.nan,
        }

        lookback = (
            self.beta_drift_lookback
        )

        required = (
            lookback * 2
        )

        if len(pair_prices) < required:
            return result

        previous_window = (
            pair_prices
            .iloc[
                -required:-lookback
            ]
        )

        recent_window = (
            pair_prices
            .iloc[
                -lookback:
            ]
        )

        beta_previous = (
            self._estimate_beta(
                previous_window
            )
        )

        beta_recent = (
            self._estimate_beta(
                recent_window
            )
        )

        if (
            np.isfinite(beta_previous)
            and np.isfinite(beta_recent)
        ):
            beta_drift_abs = abs(
                beta_recent
                - beta_previous
            )

            if abs(beta_previous) > 1e-8:
                beta_drift_pct = (
                    beta_drift_abs
                    / abs(beta_previous)
                )
            else:
                beta_drift_pct = np.nan
        else:
            beta_drift_abs = np.nan
            beta_drift_pct = np.nan

        result.update(
            {
                "beta_recent":
                    beta_recent,

                "beta_previous":
                    beta_previous,

                "beta_drift_abs":
                    beta_drift_abs,

                "beta_drift_pct":
                    beta_drift_pct,
            }
        )

        return result

    # =========================================================
    # SINGLE TRADE
    # =========================================================

    def _enrich_single_trade(
        self,
        row: pd.Series,
    ) -> dict:

        stock_a, stock_b = (
            self._extract_symbols(
                row
            )
        )

        entry_time = (
            self._extract_entry_time(
                row
            )
        )

        pair_prices = (
            self._get_pair_prices(
                stock_a=stock_a,
                stock_b=stock_b,
                entry_time=entry_time,
            )
        )

        model_features = (
            self._calculate_model_features(
                pair_prices
            )
        )

        beta = (
            model_features[
                "entry_model_beta"
            ]
        )

        volatility_features = (
            self._calculate_volatility_features(
                pair_prices=pair_prices,
                beta=beta,
            )
        )

        beta_features = (
            self._calculate_beta_drift(
                pair_prices
            )
        )

        return {
            "feature_history_bars":
                len(pair_prices),

            **model_features,
            **volatility_features,
            **beta_features,
        }

    # =========================================================
    # ALL TRADES
    # =========================================================

    def enrich(
        self,
        trades: pd.DataFrame,
        debug: bool = False,
    ) -> pd.DataFrame:

        if trades is None or trades.empty:
            return pd.DataFrame()

        result = trades.copy()

        feature_rows = []

        total = len(result)

        for number, (_, row) in enumerate(
            result.iterrows(),
            start=1,
        ):
            try:
                features = (
                    self._enrich_single_trade(
                        row
                    )
                )

            except Exception as exc:  # noqa: BLE001
                if debug:
                    print(
                        f"[{number}/{total}] "
                        f"feature error: {exc}"
                    )

                features = {
                    "feature_history_bars":
                        np.nan,

                    "entry_coint_pvalue":
                        np.nan,

                    "entry_adf_pvalue":
                        np.nan,

                    "entry_model_beta":
                        np.nan,

                    "entry_spread_std":
                        np.nan,

                    "entry_spread_z":
                        np.nan,

                    "pair_vol_20d":
                        np.nan,

                    "pair_vol_60d":
                        np.nan,

                    "vol_regime_ratio":
                        np.nan,

                    "correlation_60d":
                        np.nan,

                    "beta_recent":
                        np.nan,

                    "beta_previous":
                        np.nan,

                    "beta_drift_abs":
                        np.nan,

                    "beta_drift_pct":
                        np.nan,
                }

            feature_rows.append(
                features
            )

            if debug:
                print(
                    f"[{number}/{total}] "
                    f"features calculated"
                )

        features_df = pd.DataFrame(
            feature_rows,
            index=result.index,
        )

        return pd.concat(
            [
                result,
                features_df,
            ],
            axis=1,
        )


entry_feature_enricher = (
    EntryFeatureEnricher()
)