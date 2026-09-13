from dataclasses import dataclass

import numpy as np
import pandas as pd
import statsmodels.api as sm
from sqlalchemy import text
from statsmodels.tsa.stattools import adfuller, coint

from config.database import db
from config.risk_config import risk_config


@dataclass(frozen=True)
class PairsBacktestConfig:
    # =========================================================
    # CORE MODEL
    # =========================================================

    window: int = 300

    z_entry: float = 1.5
    z_exit: float = 0.5
    z_stop: float = 3.5

    max_cointegration_pvalue: float = 0.05
    max_adf_pvalue: float = 0.05
    max_half_life: float = 100.0

    max_holding_bars: int = 100

    # =========================================================
    # ROLLING STABILITY FILTER
    # =========================================================

    use_stability_filter: bool = True

    # Number of historical validation windows
    stability_lookback_windows: int = 12

    # Distance between validation windows.
    # 21 trading days ~= 1 month.
    stability_step: int = 21

    # At least 10% of previous validation windows
    # must satisfy the statistical conditions.
    min_stability_pct: float = 10.0

    # =========================================================
    # CAPITAL / COSTS
    # =========================================================

    initial_capital: float = 10_000.0

    transaction_cost_rate: float = (
        risk_config.TRANSACTION_COST_RATE
    )


class PairsBacktester:
    def __init__(
        self,
        config: PairsBacktestConfig | None = None,
    ):
        self.config = config or PairsBacktestConfig()

    # =========================================================
    # DATA
    # =========================================================

    def _load_daily_pair(
        self,
        stock_a: str,
        stock_b: str,
    ) -> pd.DataFrame:

        query = text(
            """
            SELECT
                symbol,
                date,
                open,
                close
            FROM stock_prices_daily
            WHERE symbol IN (:stock_a, :stock_b)
            ORDER BY date
            """
        )

        with db.engine.connect() as conn:
            df = pd.read_sql(
                query,
                conn,
                params={
                    "stock_a": stock_a,
                    "stock_b": stock_b,
                },
            )

        if df.empty:
            raise ValueError(
                "No daily data found for requested pair."
            )

        df["date"] = pd.to_datetime(
            df["date"]
        )

        df["open"] = pd.to_numeric(
            df["open"],
            errors="coerce",
        )

        df["close"] = pd.to_numeric(
            df["close"],
            errors="coerce",
        )

        data_a = (
            df[df["symbol"] == stock_a]
            [["date", "open", "close"]]
            .copy()
        )

        data_b = (
            df[df["symbol"] == stock_b]
            [["date", "open", "close"]]
            .copy()
        )

        if data_a.empty:
            raise ValueError(
                f"{stock_a} not found in stock_prices_daily."
            )

        if data_b.empty:
            raise ValueError(
                f"{stock_b} not found in stock_prices_daily."
            )

        data_a = data_a.rename(
            columns={
                "open": "open_a",
                "close": "close_a",
            }
        )

        data_b = data_b.rename(
            columns={
                "open": "open_b",
                "close": "close_b",
            }
        )

        data = pd.merge(
            data_a,
            data_b,
            on="date",
            how="inner",
        )

        data = (
            data
            .dropna()
            .drop_duplicates(
                subset=["date"]
            )
            .sort_values("date")
            .set_index("date")
        )

        return data

    # =========================================================
    # STATISTICAL MODEL
    # =========================================================

    def _fit_model(
        self,
        price_a: pd.Series,
        price_b: pd.Series,
    ) -> dict | None:

        if len(price_a) < self.config.window:
            return None

        if len(price_b) < self.config.window:
            return None

        price_a = price_a.astype(float)
        price_b = price_b.astype(float)

        if (price_a <= 0).any():
            return None

        if (price_b <= 0).any():
            return None

        log_a = np.log(
            price_a
        )

        log_b = np.log(
            price_b
        )

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
        # OLS
        #
        # log(A) = alpha + beta * log(B)
        # -----------------------------------------------------

        x = sm.add_constant(
            log_b
        )

        try:
            regression = sm.OLS(
                log_a,
                x,
            ).fit()

        except Exception:
            return None

        alpha = float(
            regression.params.iloc[0]
        )

        beta = float(
            regression.params.iloc[1]
        )

        if beta <= 0:
            return None

        fitted = regression.predict(
            x
        )

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

        spread_mean = float(
            spread.mean()
        )

        spread_std = float(
            spread.std(ddof=1)
        )

        if (
            not np.isfinite(spread_std)
            or spread_std <= 0
        ):
            return None

        half_life = (
            self._calculate_half_life(
                spread
            )
        )

        return {
            "alpha": alpha,
            "beta": beta,

            "cointegration_pvalue":
                float(coint_pvalue),

            "adf_pvalue":
                adf_pvalue,

            "spread_mean":
                spread_mean,

            "spread_std":
                spread_std,

            "half_life":
                half_life,
        }

    # =========================================================
    # MODEL VALIDATION
    # =========================================================

    def _model_is_valid(
        self,
        model: dict | None,
    ) -> bool:

        if model is None:
            return False

        if (
            model["cointegration_pvalue"]
            > self.config.max_cointegration_pvalue
        ):
            return False

        if (
            model["adf_pvalue"]
            > self.config.max_adf_pvalue
        ):
            return False

        if (
            not np.isfinite(
                model["half_life"]
            )
        ):
            return False

        if (
            model["half_life"]
            > self.config.max_half_life
        ):
            return False

        if model["beta"] <= 0:
            return False

        return True

    # =========================================================
    # HALF-LIFE
    # =========================================================

    def _calculate_half_life(
        self,
        spread: pd.Series,
    ) -> float:

        lagged = (
            spread.shift(1)
        )

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

        y = regression_data[
            "delta"
        ]

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
            -np.log(2)
            / beta
        )

        if (
            not np.isfinite(half_life)
            or half_life <= 0
        ):
            return float("inf")

        return float(
            half_life
        )

    # =========================================================
    # ROLLING STABILITY
    # =========================================================

    def _calculate_stability(
        self,
        close_a: pd.Series,
        close_b: pd.Series,
    ) -> dict:
        """
        Calculate stability using ONLY historical data.

        Example:

        12 historical validation windows
        4 are statistically valid

        stability = 4 / 12 = 33.3%
        """

        required_history = (
            self.config.window
            + (
                self.config.stability_lookback_windows
                - 1
            )
            * self.config.stability_step
        )

        if (
            len(close_a)
            < required_history
        ):
            return {
                "windows_tested": 0,
                "valid_windows": 0,
                "stability_pct": 0.0,
            }

        valid_windows = 0
        windows_tested = 0

        current_end = len(
            close_a
        )

        # Walk backwards through historical windows.
        for offset in range(
            self.config.stability_lookback_windows
        ):
            end = (
                current_end
                - offset
                * self.config.stability_step
            )

            start = (
                end
                - self.config.window
            )

            if start < 0:
                break

            window_a = (
                close_a.iloc[
                    start:end
                ]
            )

            window_b = (
                close_b.iloc[
                    start:end
                ]
            )

            model = self._fit_model(
                window_a,
                window_b,
            )

            windows_tested += 1

            if self._model_is_valid(
                model
            ):
                valid_windows += 1

        if windows_tested == 0:
            stability_pct = 0.0

        else:
            stability_pct = (
                valid_windows
                / windows_tested
                * 100
            )

        return {
            "windows_tested":
                windows_tested,

            "valid_windows":
                valid_windows,

            "stability_pct":
                float(stability_pct),
        }

    # =========================================================
    # Z SCORE
    # =========================================================

    @staticmethod
    def _calculate_z_score(
        price_a: float,
        price_b: float,
        model: dict,
    ) -> float:

        spread = (
            np.log(price_a)
            - (
                model["alpha"]
                + model["beta"]
                * np.log(price_b)
            )
        )

        z_score = (
            spread
            - model["spread_mean"]
        ) / model["spread_std"]

        return float(
            z_score
        )

    # =========================================================
    # POSITION RETURN
    # =========================================================

    @staticmethod
    def _calculate_position_return(
        side: int,
        beta: float,
        entry_a: float,
        entry_b: float,
        current_a: float,
        current_b: float,
    ) -> float:

        gross_weight = (
            1.0 + abs(beta)
        )

        weight_a = (
            side
            / gross_weight
        )

        weight_b = (
            -side
            * beta
            / gross_weight
        )

        return_a = (
            current_a
            / entry_a
            - 1.0
        )

        return_b = (
            current_b
            / entry_b
            - 1.0
        )

        pair_return = (
            weight_a
            * return_a
            + weight_b
            * return_b
        )

        return float(
            pair_return
        )

    # =========================================================
    # BACKTEST
    # =========================================================

    def run(
        self,
        stock_a: str,
        stock_b: str,
        debug: bool = False,
    ) -> dict:

        data = self._load_daily_pair(
            stock_a,
            stock_b,
        )

        minimum_required = (
            self.config.window
            + (
                self.config.stability_lookback_windows
                - 1
            )
            * self.config.stability_step
            + 1
        )

        if (
            len(data)
            <= minimum_required
        ):
            raise ValueError(
                "Not enough historical data "
                "for stability-filtered backtest."
            )

        capital = float(
            self.config.initial_capital
        )

        trades = []
        equity_records = []

        position = None

        stability_checks = 0
        stability_passes = 0
        stability_rejections = 0

        start_index = (
            self.config.window
            - 1
        )

        # =====================================================
        # WALK FORWARD
        # =====================================================

        for i in range(
            start_index,
            len(data) - 1,
        ):
            signal_date = (
                data.index[i]
            )

            next_date = (
                data.index[i + 1]
            )

            close_a = float(
                data.iloc[i][
                    "close_a"
                ]
            )

            close_b = float(
                data.iloc[i][
                    "close_b"
                ]
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

            # =================================================
            # NO OPEN POSITION
            # =================================================

            if position is None:

                historical = (
                    data.iloc[
                        :i + 1
                    ]
                )

                current_window = (
                    historical.iloc[
                        -self.config.window:
                    ]
                )

                model = self._fit_model(
                    current_window[
                        "close_a"
                    ],
                    current_window[
                        "close_b"
                    ],
                )

                if not self._model_is_valid(
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

                # =============================================
                # STABILITY FILTER
                # =============================================

                stability = {
                    "windows_tested": 0,
                    "valid_windows": 0,
                    "stability_pct": 0.0,
                }

                if (
                    self.config
                    .use_stability_filter
                ):
                    stability_checks += 1

                    stability = (
                        self._calculate_stability(
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
                        < self.config
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
                        < self.config
                        .min_stability_pct
                    ):
                        stability_rejections += 1

                        if debug:
                            print(
                                f"STABILITY REJECT "
                                f"{signal_date.date()} | "
                                f"{stock_a}/{stock_b} | "
                                f"{stability['stability_pct']:.2f}%"
                            )

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

                # =============================================
                # SIGNAL
                # =============================================

                signal_z = (
                    self._calculate_z_score(
                        close_a,
                        close_b,
                        model,
                    )
                )

                side = 0

                if (
                    -self.config.z_stop
                    < signal_z
                    < -self.config.z_entry
                ):
                    side = 1

                elif (
                    self.config.z_entry
                    < signal_z
                    < self.config.z_stop
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

                # =============================================
                # ENTRY NEXT DAY OPEN
                # =============================================

                entry_cost = (
                    capital
                    * self.config
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
                        model[
                            "spread_mean"
                        ],

                    "spread_std":
                        model[
                            "spread_std"
                        ],

                    "half_life":
                        model[
                            "half_life"
                        ],

                    "cointegration_pvalue":
                        model[
                            "cointegration_pvalue"
                        ],

                    "adf_pvalue":
                        model[
                            "adf_pvalue"
                        ],

                    "stability_pct":
                        stability[
                            "stability_pct"
                        ],

                    "stability_valid_windows":
                        stability[
                            "valid_windows"
                        ],

                    "stability_windows_tested":
                        stability[
                            "windows_tested"
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
                    direction = (
                        f"LONG {stock_a} / "
                        f"SHORT {stock_b}"
                        if side == 1
                        else
                        f"SHORT {stock_a} / "
                        f"LONG {stock_b}"
                    )

                    print(
                        f"\nSIGNAL "
                        f"{signal_date.date()}"
                    )

                    print(
                        f"ENTRY  "
                        f"{next_date.date()}"
                    )

                    print(
                        direction
                    )

                    print(
                        f"z="
                        f"{signal_z:.3f} | "
                        f"beta="
                        f"{model['beta']:.3f} | "
                        f"stability="
                        f"{stability['stability_pct']:.2f}% | "
                        f"coint="
                        f"{model['cointegration_pvalue']:.4f} | "
                        f"ADF="
                        f"{model['adf_pvalue']:.4f}"
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

            position[
                "bars_held"
            ] += 1

            frozen_model = {
                "alpha":
                    position[
                        "alpha"
                    ],

                "beta":
                    position[
                        "beta"
                    ],

                "spread_mean":
                    position[
                        "spread_mean"
                    ],

                "spread_std":
                    position[
                        "spread_std"
                    ],
            }

            current_z = (
                self._calculate_z_score(
                    close_a,
                    close_b,
                    frozen_model,
                )
            )

            mark_to_market_return = (
                self._calculate_position_return(
                    side=
                        position[
                            "side"
                        ],

                    beta=
                        position[
                            "beta"
                        ],

                    entry_a=
                        position[
                            "entry_a"
                        ],

                    entry_b=
                        position[
                            "entry_b"
                        ],

                    current_a=
                        close_a,

                    current_b=
                        close_b,
                )
            )

            mark_to_market_equity = (
                position[
                    "capital_at_entry"
                ]
                * (
                    1.0
                    + mark_to_market_return
                )
            )

            exit_reason = None

            # Mean reversion
            if (
                abs(current_z)
                <= self.config.z_exit
            ):
                exit_reason = (
                    "MEAN_REVERSION"
                )

            # Stop
            elif (
                abs(current_z)
                >= self.config.z_stop
            ):
                exit_reason = (
                    "STOP"
                )

            # Time exit
            elif (
                position[
                    "bars_held"
                ]
                >= self.config
                .max_holding_bars
            ):
                exit_reason = (
                    "TIME_EXIT"
                )

            if (
                exit_reason
                is None
            ):
                equity_records.append(
                    {
                        "date":
                            signal_date,

                        "equity":
                            mark_to_market_equity,
                    }
                )

                continue

            # =================================================
            # EXIT NEXT DAY OPEN
            # =================================================

            exit_return = (
                self._calculate_position_return(
                    side=
                        position[
                            "side"
                        ],

                    beta=
                        position[
                            "beta"
                        ],

                    entry_a=
                        position[
                            "entry_a"
                        ],

                    entry_b=
                        position[
                            "entry_b"
                        ],

                    current_a=
                        next_open_a,

                    current_b=
                        next_open_b,
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
                * self.config
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
                        next_date,

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

                    "stability_valid_windows":
                        position[
                            "stability_valid_windows"
                        ],

                    "stability_windows_tested":
                        position[
                            "stability_windows_tested"
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
                    f"EXIT SIGNAL "
                    f"{signal_date.date()}"
                )

                print(
                    f"EXIT       "
                    f"{next_date.date()}"
                )

                print(
                    f"reason="
                    f"{exit_reason} | "
                    f"z="
                    f"{current_z:.3f} | "
                    f"net="
                    f"{net_return:.2%}"
                )

            position = None

            equity_records.append(
                {
                    "date":
                        next_date,

                    "equity":
                        capital,
                }
            )

        # =====================================================
        # CLOSE OPEN POSITION AT END
        # =====================================================

        if position is not None:

            final_date = (
                data.index[-1]
            )

            final_close_a = float(
                data.iloc[-1][
                    "close_a"
                ]
            )

            final_close_b = float(
                data.iloc[-1][
                    "close_b"
                ]
            )

            frozen_model = {
                "alpha":
                    position[
                        "alpha"
                    ],

                "beta":
                    position[
                        "beta"
                    ],

                "spread_mean":
                    position[
                        "spread_mean"
                    ],

                "spread_std":
                    position[
                        "spread_std"
                    ],
            }

            final_z = (
                self._calculate_z_score(
                    final_close_a,
                    final_close_b,
                    frozen_model,
                )
            )

            final_return = (
                self._calculate_position_return(
                    side=
                        position[
                            "side"
                        ],

                    beta=
                        position[
                            "beta"
                        ],

                    entry_a=
                        position[
                            "entry_a"
                        ],

                    entry_b=
                        position[
                            "entry_b"
                        ],

                    current_a=
                        final_close_a,

                    current_b=
                        final_close_b,
                )
            )

            gross_equity = (
                position[
                    "capital_at_entry"
                ]
                * (
                    1.0
                    + final_return
                )
            )

            exit_cost = (
                gross_equity
                * self.config
                .transaction_cost_rate
            )

            capital = (
                gross_equity
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
                        final_date,

                    "exit_time":
                        final_date,

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
                        final_z,

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

                    "stability_valid_windows":
                        position[
                            "stability_valid_windows"
                        ],

                    "stability_windows_tested":
                        position[
                            "stability_windows_tested"
                        ],

                    "bars_held":
                        position[
                            "bars_held"
                        ],

                    "gross_return":
                        final_return,

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
                        "END_OF_DATA",
                }
            )

            equity_records.append(
                {
                    "date":
                        final_date,

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
            self._calculate_metrics(
                equity_curve,
                trades_df,
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
                data.index[0],

            "end_date":
                data.index[-1],

            "bars":
                len(data),

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

    def _calculate_metrics(
        self,
        equity_curve: pd.DataFrame,
        trades: pd.DataFrame,
    ) -> dict:

        if equity_curve.empty:
            return {}

        initial_capital = float(
            self.config.initial_capital
        )

        final_equity = float(
            equity_curve[
                "equity"
            ].iloc[-1]
        )

        total_return = (
            final_equity
            / initial_capital
            - 1.0
        )

        daily_returns = (
            equity_curve[
                "equity"
            ]
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
            equity_curve[
                "equity"
            ]
            .cummax()
        )

        drawdown = (
            equity_curve[
                "equity"
            ]
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

            winners = trades[
                trades[
                    "net_pnl"
                ] > 0
            ]

            losers = trades[
                trades[
                    "net_pnl"
                ] < 0
            ]

            win_rate = (
                len(winners)
                / number_of_trades
            )

            average_trade_return = float(
                trades[
                    "net_return"
                ].mean()
            )

            median_trade_return = float(
                trades[
                    "net_return"
                ].median()
            )

            average_holding = float(
                trades[
                    "bars_held"
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

            transaction_costs = float(
                (
                    trades[
                        "entry_cost"
                    ]
                    + trades[
                        "exit_cost"
                    ]
                ).sum()
            )

        else:
            win_rate = 0.0
            average_trade_return = 0.0
            median_trade_return = 0.0
            average_holding = 0.0
            profit_factor = 0.0
            transaction_costs = 0.0

        return {
            "initial_capital":
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

            "median_trade_return_pct":
                round(
                    median_trade_return
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
                    max_drawdown * 100,
                    2,
                ),

            "average_holding_days":
                round(
                    average_holding,
                    2,
                ),

            "transaction_costs":
                round(
                    transaction_costs,
                    2,
                ),
        }


pairs_backtester = PairsBacktester()