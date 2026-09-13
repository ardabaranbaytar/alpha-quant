# Quarterly walk-forward screening and inverse-volatility sizing

The CLI now runs these rules inside a single shared-capital chronological portfolio, with 100,000 USD default initial equity, eight simultaneous positions and a 35% gross ticker admission cap. See `PORTFOLIO_ENGINE.md` for current cash/margin rules, portfolio metrics and the latest run. Older independent-pair results below remain historical references.

```powershell
python -m research.run_backtest
python -m research.run_backtest --years 5 --model ols
python -m research.run_backtest --years 5 --model ols --target-spread-risk 100 --max-ticker-fraction 0.35
python -m research.run_backtest --years 5 --model kalman
python -m research.run_backtest --years 5 --pairs AAPL/MSFT KO/PEP
python -m unittest discover -s tests -p 'test_*pair*.py' -v
node --test public_site/tests/results.test.cjs
```

## Universe and training boundary

The expanded requested list contains 56 symbols. A separate Utilities group (NEE, DUK, SO, AEP) completes the 60-stock preset. This is a liquid-stock research candidate list, not a historical liquidity ranking or a historical constituent database.

| Group | Stocks | Candidate combinations |
| --- | --- | ---: |
| Financials | JPM, BAC, WFC, C, MS, GS, BLK, BK | 28 |
| Energy | XOM, CVX, COP, SLB, EOG, OXY | 15 |
| Consumer staples | KO, PEP, PG, CL, KMB, MDLZ | 15 |
| Healthcare | JNJ, PFE, MRK, ABBV, BMY, LLY, AMGN | 21 |
| Industrials / defense | CAT, DE, HON, MMM, GE, LMT, RTX, NOC | 28 |
| Technology / semiconductors | GOOGL, META, MSFT, AAPL, NVDA, AMD, QCOM, AVGO | 28 |
| Insurance / specialized finance | CB, PGR, TRV, ALL, MET | 10 |
| Communication / media | CMCSA, DIS, NFLX, T | 6 |
| Real estate / infrastructure | PLD, AMT, EQIX, CCI | 6 |
| Utilities | NEE, DUK, SO, AEP | 6 |
| Total | 60 unique stocks | 163 |

Industrials are not paired with technology stocks; insurance is separate from banks and diversified finance. These are the user's broad research groups rather than strict GICS membership. Candidate orientation follows the listed order; reverse regressions are not additionally searched for a better p-value.

BK is a stable research identity for the same security now fetched from Yahoo as BNY. The issuer announced the ticker change effective May 21, 2026, with unchanged CUSIPs/capital structure ([issuer release](https://www.prnewswire.com/news-releases/bny-announces-planned-change-of-stock-ticker-symbol-to-bny-302767757.html)). `providerSymbolAliases` in each private audit records this mapping. This is an identifier conversion, not replacement with a different stock; no future price information enters earlier signals. The supplied adjusted history is still not a point-in-time vendor database.

Download five calendar years through the exclusive UTC end date. Use the first 252 observed market sessions for training, then re-screen every 63 sessions using the trailing 252-session window, not an expanding sample. This is a session-count quarter, not a calendar-quarter boundary. The session calendar is the sorted union of dates observed for the preset universe; a missing quote for one stock does not delete a session for every other stock. Pair fitting still requires at least 200 aligned training closes, with no forward filling. If all stocks lack a date, it is not an observed session.

For effective session at index `i`, the only screening inputs are sessions `[i - 252, i)`. Training includes the previous session's close and excludes the effective session's prices. The selection becomes effective at that session's open and remains in force until the next rebalance. The final evaluation block can be shorter than 63 sessions. Initial training produces no trades, so five years of downloaded history yield approximately four years of out-of-training evaluation. Manual `--pairs` bypasses selection; its evaluation starts after the usual signal/filter warmup.

The backtest monitors each ever-selected pair inside a single daily portfolio loop; an entry-permission schedule gates both signal generation and pending entry fills. A newly selected pair can generate a signal at the effective session's close and fill on the following open. A removed pair's pending entry is canceled, but any pending exit still executes. Existing positions are neither force-liquidated nor reset at rebalance: their entry model, quantities, costs and holding clock survive until normal mean crossing, early profit-taking or a risk exit. An empty selection prohibits all new entries, and a later re-selection can restore entry permission. It does not open a duplicate position. The ten-pair cap applies to each monitoring universe; the separate portfolio concurrency, collateral and ticker limits determine which entries can actually execute.

Future periods are precomputed for reproducibility, but only the latest selection effective on or before the simulated session can enable entry. Regression tests perturb future prices, append future history and remove a single stock's quote, then verify that earlier selections do not change. They also check canceled pending entries, carried positions, pending exits, empty selections and the original 45-session clock. These checks establish algorithmic chronology on the supplied data, not point-in-time correctness of the vendor's adjusted history.

## Ranking and elimination

Within each group, evaluate every unordered combination using only aligned observed training closes, with at least 200 training sessions and no filled prices. Fit the existing positive-beta OLS price-level relationship and Statsmodels augmented Engle-Granger test. Keep p < 0.05, then an OU-compatible AR(1) residual half-life strictly between 0 and 20 sessions, then H < 0.45. Exactly p=0.05 fails. Invalid models and estimates fail the gate. The half-life calculation uses the existing exact AR(1)-to-OU mapping with equal training weights. The 5% level is the configured research significance threshold, not a guarantee of economic profitability.

Sort passing pairs by ascending p-value, then half-life, H and pair name for deterministic ties. Select at most ten. If only one, five or zero qualify, select that number rather than loosening criteria to reach eight. Failure counts are mutually exclusive: each candidate is counted at its first failing gate, with an additional rank-limit count for qualified pairs outside the cap. Selected count plus all rejection counts must equal 163 in the expanded universe. Historical runs below used the older 62-candidate universe.

These are raw p-values across many dependent candidate tests, not a multiple-testing-adjusted family-wise guarantee. The preset universe can introduce survivorship bias. Training selection and backtest evaluation are chronological, but Yahoo-adjusted history can be revised and is not a point-in-time corporate-action database.

## Hurst estimator

`strategies/mean_reversion.py` estimates generalized H(2) as half the regression slope of `log(mean((spread[t+lag] - spread[t])**2))` on `log(lag)`, with lags 2 through 20. It requires at least 100 finite observations and rejects constant series or estimates outside [0, 1] rather than clipping them. This is a finite-scale persistence diagnostic, not proof of stationarity or a precise long-memory parameter for an OU process.

Each rebalance uses its own full trailing training spread. At each prospective trade entry, both CLI models additionally require H < 0.45 on 120 observations preceding the current signal close. OLS uses those past prices with the current past-fitted model coefficients; Kalman uses prior one-step innovations, after its filter burn-in. The current residual cannot influence its own Hurst entry decision. H=0.45, missing H and invalid H block new entries but do not block existing-position exits.

The low-level `simulate_pair()` API accepts `entry_filters=None` for legacy isolated tests. The CLI uses `PortfolioEngine` with `EntryFilterConfig()` and thus enables both the Hurst and early-profit rules in either model.

## Inverse-volatility entry sizing

The CLI always enables `VolatilitySizingConfig`. The default risk target is 100 USD of exposure to a one-standard-deviation residual-level movement; it is configurable with `--target-spread-risk`. It is neither annualized/daily return volatility nor a maximum loss, VaR or account risk percentage. The low-level `simulate_pair(sizing=None)` retains the old fixed sizing only for compatibility with isolated tests.

For OLS, use the sample standard deviation (`ddof=1`) already estimated by the entry model from the previous 60 closes, excluding the signal close. At order creation, freeze this dollar residual standard deviation with the model. For optional Kalman runs, take the standard deviation of the previous 60 innovations after burn-in and multiply by A's first close to undo the filter's normalization. Current and future innovations cannot affect this estimate. Zero, non-finite or effectively degenerate volatility blocks entry instead of producing an oversized position.

One spread unit holds one A share against beta B shares. At the next synchronized open, first calculate the two direction-specific execution prices including adverse slippage. Let `G = fill_A + beta * fill_B`, the gross USD cost per unit, and `sigma` be the frozen residual standard deviation in USD/unit:

```text
normalized_volatility = sigma / G
raw_gross_notional = target_spread_risk / normalized_volatility
gross_notional = clip(raw_gross_notional, 10000, 25000)
units = gross_notional / G
quantities = direction * [units, -beta * units]
```

Without bounds, this is exactly `units = target_spread_risk / sigma`. Normalizing by the hedge-weighted gross cost makes the notional dimensionally consistent and avoids treating a dollar residual standard deviation as a return percentage. Re-scaling both price series and their residuals leaves gross exposure unchanged. At fixed prices/beta, more residual volatility reduces size. Bounds apply to executed gross notional including slippage, before commissions, even after an opening gap. They are entry bounds, not ongoing exposure caps. In particular, the requested 10,000 USD floor can exceed the risk target on a high-volatility pair.

Shares stay fixed until exit; quarterly re-selection and subsequent volatility changes do not resize a position. Entry/exit commissions use actual leg quantities and prices, and borrow uses actual entry short notional over elapsed calendar time. The early-profit estimate and realized exit share the same accounting. There is no change to the 10 bps commission, 5 bps slippage or 3% annual borrow assumptions.

## Cost-aware early profit-taking

At a completed daily close, request an exit when `abs(Z) <= 0.5` and estimated net liquidation PnL is strictly positive. Use both legs' current adjusted closes, adverse exit slippage, exit commission, already-paid entry commission and accrued borrow. The same liquidation calculation is used for actual next-open fills. Borrow estimation uses 16:00 America/New_York as the daily-close clock; exchange half-days are not separately modeled.

The decision executes at the next synchronized session open. That gap can turn estimated profit into an actual loss; zero or negative estimated net profit must not trigger early profit-taking. Mean crossing, the 45-session time stop and absolute Z > 3.5 risk stop still apply independently. An exit decided on the last available close is not exported until a future fill is available.

## Artifacts and privacy

The public `demo-data.js` contract is unchanged and includes only completed trades. Public method metadata records the model, filter settings, selection method and evaluation start. It does not contain screening candidates, scores, live signals, open positions or open-position counts.

Local `research/artifacts/pair_backtest/screening-report.json` contains each rebalance's effective session, last training session, selected training pairs, training scores, rejected counts and exclusive training cutoff, plus aggregate candidate-evaluation and unique-selection counts. `run-summary.json` also records parameters, data hashes, package versions, win rate and maximum realized drawdown. These artifacts and source CSVs remain outside the public site and are ignored by Git. Only method settings, including sizing bounds and risk target, not periodic selections or screening scores, are added to public metadata. A failed download or export validation leaves the previous public snapshot intact.

## Earlier independent quarterly inverse-volatility OLS reference

This run predates the shared-capital portfolio engine. Its drawdown is closed-equity drawdown, not the new marked portfolio drawdown.

Command: `python -m research.run_backtest --years 5 --model ols`, with the default 50 USD risk target. Requested history was `[2021-09-08, 2026-09-08)`; observed prices cover 2021-09-08 through 2026-09-04. Evaluation begins on 2022-09-08 after 252 initial training sessions. Screening uses p < 0.05, half-life < 20 and H < 0.45 throughout.

There were 16 screening rounds, each evaluating the same 62 candidates: 992 pair-window evaluations, 43 selected pair-period memberships and 26 unique selected pairs. Rejections totaled 848 cointegration, 94 invalid spread models and seven Hurst failures. These are mutually exclusive first-failure counts. No pair quota was forced.

| Effective session | Selected pairs |
| --- | --- |
| 2022-09-08 | JNJ/ABBV, JNJ/BMY |
| 2022-12-07 | JNJ/ABBV |
| 2023-03-10 | KO/CL, KO/PG, CVX/EOG |
| 2023-06-09 | PG/KMB, COP/EOG, KO/PG |
| 2023-09-11 | CL/KMB, COP/EOG |
| 2023-12-08 | BAC/GS |
| 2024-03-12 | JPM/WFC, SLB/EOG, BAC/GS |
| 2024-06-11 | JPM/C |
| 2024-09-11 | PG/CL |
| 2024-12-10 | CL/KMB |
| 2025-03-14 | JNJ/ABBV, BAC/MS, JPM/GS |
| 2025-06-13 | JNJ/ABBV, PG/CL, COP/SLB, PG/KMB |
| 2025-09-15 | BAC/MS |
| 2025-12-12 | C/GS, WFC/GS, WFC/MS, BAC/MS |
| 2026-03-17 | C/GS, DE/HON, JPM/BAC, JNJ/PFE |
| 2026-06-16 | JPM/BAC, PFE/MRK, JNJ/PFE, DE/HON, PFE/BMY, JPM/MS, MRK/BMY, JPM/GS, JPM/C |

| Closed-result metric | Value |
| --- | ---: |
| Completed simulations | 20 |
| Wins / losses | 12 / 8 |
| Net realized PnL | +344.28 USD |
| Win rate | 60.00% |
| Profit factor | 1.5189 |
| Maximum closed-equity drawdown | 420.60 USD |

These results include all modeled commission, slippage and short borrow on dynamically sized positions. Drawdown is measured on the cumulative closed-trade PnL curve, not intraday or mark-to-market portfolio equity. Approximately four of the five downloaded years are evaluation, and 20 trades remain a small research sample. Because screening frequency, significance and sizing changed together, the difference from earlier runs cannot be attributed to sizing alone.

Validation: 58 Python tests and all 10 dashboard tests passed. Sizing tests cover both trade directions, actual notional bounds after slippage and gaps, inverse-volatility monotonicity, price-scale invariance, past-only OLS/Kalman volatility, unchanged quantities across rebalance, and net liquidation costs. Python run-summary metrics independently reconcile with the JavaScript dashboard calculations. The public snapshot contains only closed trades; selections and per-pair scores stay in local research artifacts.

## Earlier annual fixed-size OLS reference

This superseded annual, p < 0.02, fixed-10,000-USD run is retained for research history, not as the current export. Command at that time: `python -m research.run_backtest --years 5 --model ols`. Requested history is `[2021-09-08, 2026-09-08)`; actual price coverage ends on 2026-09-04. The first evaluation session is 2022-09-08. Each row below uses exactly 252 preceding observed market sessions; its effective-session prices are excluded from screening.

| Effective session | Training first / last session | Candidates | Selected | Rejected |
| --- | --- | ---: | --- | --- |
| 2022-09-08 | 2021-09-08 / 2022-09-07 | 62 | JNJ/ABBV | 61 cointegration |
| 2023-09-11 | 2022-09-08 / 2023-09-08 | 62 | CL/KMB | 57 cointegration, 4 invalid spread model |
| 2024-09-11 | 2023-09-11 / 2024-09-10 | 62 | None | 55 cointegration, 7 invalid spread model |
| 2025-09-15 | 2024-09-11 / 2025-09-12 | 62 | None | 55 cointegration, 7 invalid spread model |

Total: 62 distinct candidate pairs, 248 pair-window evaluations, 246 rejections and two unique selected pairs. Invalid spread models include relationships incompatible with the existing positive-beta OLS model. All selected pairs passed p < 0.02, half-life < 20 and H < 0.45. JNJ/ABBV had p 0.0146436, half-life 6.8354, H 0.329985; CL/KMB had p 0.00894838, half-life 5.73219, H 0.420585. No thresholds were relaxed in empty years.

| Closed-result metric | Value |
| --- | ---: |
| Completed simulations | 3 |
| Wins / losses | 3 / 0 |
| Net realized PnL | +309.47 USD |
| Win rate | 100.00% |
| Profit factor | N/A (no realized losses) |
| Maximum closed-equity drawdown | 0.00 USD |

All three completed simulations were JNJ/ABBV; CL/KMB produced no completed simulations. The previously reported 2025 JNJ/ABBV losses cannot occur in this run because the pair was no longer eligible for new entries after 2023-09-11. This is the effect of the pre-specified selection policy, not evidence of durable predictive performance. The 100% win rate is based on only three trades; closed-equity drawdown does not measure interim unrealized losses. Costs and the 10,000 USD per-pair gross sizing rule are unchanged. There is no funded-account return calculation.

Validation: 49 Python tests, including 12 new walk-forward tests, and all 10 public dashboard tests passed. `node --check public_site/app.js` passed. The public export retains the same closed-only trade schema and does not expose annual selections, screening scores, pending orders or open positions.

## Earlier static OLS reference

Before annual re-screening was introduced, `python -m research.run_backtest` downloaded sessions from 2021-09-08 through 2026-09-04, with an exclusive requested end of 2026-09-08. Training was `[2021-09-08, 2022-09-08)`; evaluation began on 2022-09-08. The following results describe that superseded single-selection run, not the current public export.

Of 62 same-group candidates, 61 failed the cointegration gate. JNJ/ABBV was the only eligible pair: training p-value 0.0146436, half-life 6.8355 sessions, H 0.329985 and 252 aligned training bars. No thresholds were relaxed to fill the ten-pair limit.

| Closed-result metric | Value |
| --- | ---: |
| Completed simulations | 5 |
| Wins / losses | 3 / 2 |
| Net realized PnL | +78.19 USD |
| Win rate | 60.00% |
| Profit factor | 1.3381 |
| Maximum closed-equity drawdown | 231.28 USD |

These are cost-adjusted simulated results at 10,000 USD gross entry notional per pair, not a return on a funded account. Closed-equity drawdown excludes interim unrealized losses. Five trades are not enough to establish robustness. The recorded export passed 37 Python strategy/screener/backtest tests and all 10 public dashboard tests.

References: [Statsmodels coint](https://www.statsmodels.org/stable/generated/statsmodels.tsa.stattools.coint.html) documents the no-cointegration null. [NumPy std](https://numpy.org/doc/stable/reference/generated/numpy.std.html) documents the sample-standard-deviation convention used with `ddof=1`. The Hurst statistic is implemented as an explicitly defined lag-variogram estimate, so its value should only be compared with thresholds calibrated for that estimator and lag range.
