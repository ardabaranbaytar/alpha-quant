# Kalman / OU pair model

```powershell
python -m research.run_backtest --model kalman
python -m research.run_backtest --model ols
python -m unittest discover -s tests -p 'test_*pair*.py' -v
node --test public_site/tests/results.test.cjs
```

The CLI now defaults to OLS and five-year sector screening; `--model kalman` selects this model. Both modes share screening, the past-only Hurst gate, cost-aware early profit-taking, next-open execution, sizing, costs, risk exits and closed-only export. The low-level `simulate_pair()` default remains OLS for compatibility; the CLI explicitly constructs the chosen strategy for every pair and enables the shared filters. Each CLI run replaces the dashboard snapshot with the selected model's results; it never mixes models in one ledger. The three-year, seven-pair results below describe the earlier configuration before sector screening and Hurst/early-profit filters.

## State-space relationship

The implementation uses Statsmodels `KalmanFilter`, not a custom filter or a Kalman smoother. Normalize price levels once using the first observed close in each series: `a[t] = A[t] / A[0]`, `b[t] = B[t] / B[0]`.

```text
theta[t] = [alpha[t], beta[t]]
theta[t] = I * theta[t-1] + eta[t], eta ~ N(0, Q)
a[t] = [1, b[t]] * theta[t] + epsilon[t], epsilon ~ N(0, R)
Q = diag(1e-7, 1e-6)
R = 1e-4
initial theta = [0, 1]
initial P = diag(0.01, 0.01)
```

These variances and the initial state are fixed before the backtest, not optimized on its results. Scaling uses only the first known observation. The forward filter processes every close, including when a position is open. No full-sample smoothing, parameter optimization or future-dependent normalization is performed.

The spread observation is the one-step prediction residual (innovation), computed before assimilating the current A close:

```text
e[t] = a[t] - alpha[t|t-1] - beta[t|t-1] * b[t]
raw-price hedge beta[t] = beta[t|t-1] * A[0] / B[0]
```

The current B close is an observed regressor, available at the signal close. The first 20 innovations are discarded from OU estimation as filter burn-in. Signals begin after 60 observed synchronized sessions. Batch evaluation calls only the forward filter; prefix-invariance tests check that adding or modifying later data cannot change an earlier prediction or signal.

## OU fit and half-life

Fit the one-session transition `e[t] = c + phi * e[t-1] + noise` with Statsmodels weighted least squares on *past* innovations after burn-in. The current innovation is excluded. Use the full available past with exponentially decreasing weights `0.98 ** age`, at least 30 residual observations; there is no fixed rolling cutoff. This is a weighted conditional AR(1) estimate interpreted as a locally stationary OU approximation, not a proof that the residual actually follows OU dynamics.

For one-session time step:

```text
kappa = -log(phi)
mean = c / (1 - phi)
half_life = log(2) / kappa
equilibrium_std = sqrt(innovation_variance / (1 - phi**2))
diffusion_sigma = equilibrium_std * sqrt(2 * kappa)
Z[t] = (e[t] - mean) / equilibrium_std
```

Residual innovation variance is estimated with exponential weights and an effective-sample-size degrees-of-freedom correction for the two fitted coefficients. Reject non-finite, zero-variance, nonstationary (`phi >= 1`) or oscillatory (`phi <= 0`) fits rather than clipping them to a valid OU process.

New entries require a fresh valid OU fit, positive raw-price beta and strictly `3 < half_life < 25`. Enter short spread for `Z > 2`, long spread for `Z < -2`; exact boundaries do not qualify. This replaces the Engle-Granger entry gate in Kalman mode, not an additional claim of cointegration.

## Dynamic exits and accounting

For an existing long spread, exit when dynamic Z reaches or exceeds zero; for an existing short spread, exit when it reaches or falls below zero. These exits use the current dynamic Kalman residual and past-only OU center/scale, not frozen entry-model coefficients. The 45-session and `abs(Z) > 3.5` risk exits continue to apply. Every exit fills at the following synchronized session's adjusted Open.

If the current OU fit is invalid, forbid new entries and retain the last valid OU center/scale for exit evaluation using the current Kalman residual. If a usable Z is unavailable, the time stop still functions. A valid half-life outside the entry interval does not disable exit checks or force immediate liquidation.

Quantities use the signal-close predicted beta at entry, and remain fixed until exit. Beta adaptation is statistical tracking, not free daily portfolio rebalancing. The backtest charges no fictitious rebalance costs and books no fictitious rebalance PnL. Dynamic mean crossings can arise from model adaptation, even if the held position lost money. This distinction matters when interpreting results.

Current shared accounting uses inverse residual-volatility sizing, a default 100 USD residual-standard-deviation exposure target and 10,000-25,000 USD desired gross bounds, subject to the shared portfolio cash and exposure limits. Kalman sizing uses the preceding 60 innovations converted from normalized units back to dollars. Costs remain 10 bps commission and 5 bps adverse slippage on each leg at each fill, and 3% annual short borrow on entry short notional. Adjusted-price synthetic share accounting, no enforced borrow availability, no forced final liquidation. See `PORTFOLIO_ENGINE.md` for the common collateral/cash ledger and current results, and `SECTOR_SCREENING.md` for quarterly selection and sizing details. Historical Kalman results below used fixed 10,000 USD sizing.

## Public and private data

The public snapshot schema and validator are unchanged. Metadata adds only `model: "kalman"` or `"ols"` to identify the method. Current beta, alpha, residuals, half-lives, signals, open positions and open-position counts are never exported. Local `research/artifacts/pair_backtest/run-summary.json` records the selected model and its configuration; normalized price inputs and hashes remain outside the public site.

## Generated three-year result

Executed `python -m research.run_backtest --model kalman` with requested dates 2023-09-08 to 2026-09-08 (exclusive). Actual price coverage: 2023-09-08 through 2026-09-04. Configuration and source hashes are recorded in the local run summary.

| Pair | Closed trades | Net PnL (USD) |
| --- | ---: | ---: |
| AAPL / MSFT | 9 | -802.89 |
| XOM / CVX | 7 | -274.07 |
| JPM / BAC | 4 | 221.11 |
| V / MA | 7 | -514.98 |
| GOOGL / META | 12 | 183.12 |
| KO / PEP | 10 | -353.31 |
| NVDA / AMD | 5 | -1,789.05 |
| Total | 54 | -3,330.07 |

| Metric | Previous OLS run | Kalman / OU run |
| --- | ---: | ---: |
| Completed trades | 31 | 54 |
| Net realized PnL (USD) | -2,701.44 | -3,330.07 |
| Winning trades | 11 | 25 |
| Losing trades | 20 | 29 |
| Win rate | 35.48% | 46.30% |
| Profit factor | 0.3284 | 0.5465 |
| Maximum realized drawdown (USD) | 3,281.42 | 3,870.23 |

The higher win rate did not improve total realized PnL or maximum realized drawdown. This is a comparison of these two historical runs on the same requested universe and period, not independent out-of-sample validation. Noise variances, OU decay and thresholds were not tuned after seeing these outcomes. Both runs exclude remaining open outcomes.

Verification: 25 Python tests and 10 JavaScript tests passed. Tests cover OU recovery on a known process, invalid OU coefficients, strictly enforced entry boundaries, forward-filter prefix invariance, current-residual exclusion from normalization, dynamic exit evaluation, unchanged holding quantities, and time exits when a usable Z is missing, in addition to the existing OLS/accounting/privacy checks.

## References

- [Statsmodels state-space methods](https://www.statsmodels.org/stable/statespace.html) and [KalmanFilter](https://www.statsmodels.org/stable/generated/statsmodels.tsa.statespace.kalman_filter.KalmanFilter.html) document transition, design, state and observation covariance matrices and forward filtering.
- [Avellaneda and Lee, Statistical Arbitrage in the U.S. Equities Market](https://math.nyu.edu/faculty/avellane/AvellanedaLeeStatArb20090616.pdf) describes OU-based residual standardization. This implementation is not a replication of that paper's universe or model.
