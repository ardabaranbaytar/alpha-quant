# Isolated historical pair backtest

## Run

```powershell
python -m research.run_backtest
python -m research.run_backtest --model ols --end 2026-09-08 --pairs AAPL/MSFT KO/PEP
```

The script also supports direct execution as `python research/run_backtest.py`. It downloads five calendar years by default using `DataFetcher.download_daily_history()` in the existing `data_pipeline/yfinance_fetcher.py`. Use `--years N` to change the duration. The default universe is selected by the quarterly sector screener; `--pairs` bypasses screening with a manual list. The downloader does not import database settings, read an environment file, launch a scheduler or run the bot. The existing database-writing methods import their database dependency only when called.

Python dependencies come from the existing project requirements. Node is required to validate an export using the actual browser schema. No new package was installed for this implementation.

The current CLI defaults to `ols`, five years of downloaded history, quarterly walk-forward sector screening when `--pairs` is absent, and inverse-volatility sizing inside a shared-capital portfolio. See `PORTFOLIO_ENGINE.md` for the 100,000 USD initial equity, concurrency/cash/ticker limits and current portfolio results. See `SECTOR_SCREENING.md` for the past-only 252-session training / 63-session rebalance schedule, p < 0.05 threshold, Hurst filter, early profit rule and 10,000-25,000 USD desired sizing bounds. The original OLS mechanism and earlier fixed-size three-year baseline results below remain reference material. See `KALMAN_MODEL.md` for the optional dynamic state-space model. Both models share execution accounting and the closed-only export contract.

## OLS strategy and causal ordering

`strategies/pair_trading.py` is separate from the existing `strategies/pairs_trading.py` strategy.

1. Align both symbols on observed daily sessions. Do not forward/backward-fill prices. Use Yahoo's split- and dividend-adjusted Open and Close.
2. On each flat session, fit OLS on the preceding 60 synchronized closes, excluding the current close: `A = alpha + beta * B + spread`. Require a positive beta and a nondegenerate residual.
3. Apply Statsmodels' augmented Engle-Granger test (`coint`, constant trend, AIC lag selection). Entries require p < 0.05. The test assumes I(1) inputs; this prototype does not separately prove that assumption or correct for repeated rolling tests.
4. Standardize the current spread using the training residual mean and sample standard deviation. Z > 2 means short spread; Z < -2 means long spread. Exactly +2 or -2 is not an entry.
5. Execute a decision at the next synchronized session's adjusted Open. Timestamps represent 09:30 America/New_York converted to UTC, including daylight saving time.
6. Freeze alpha, beta, mean, standard deviation and position quantities at entry. Close a short spread when its close-derived Z crosses to <= 0, and a long spread at >= 0. Independently trigger a risk exit when `abs(Z) > 3.5`, or after 45 completed synchronized trading sessions including the entry session. All exit decisions execute at the following session's Open with normal slippage, commission and borrow costs. Exactly 3.5 does not trigger the Z stop. If entry is at session index E, the time stop is decided at the close of E + 44 and filled at the open of E + 45. Holidays and absent bars do not count as observed sessions.
7. Do not force liquidation at the sample boundary. Unfilled orders and positions without an executed exit produce no public record.

Pair eligibility is set by a past-only quarterly screening schedule, not future trade outcomes. The default automatic run reserves the first 252 sessions for initial pair selection; manual lists use the model and Hurst warmup. Only eligible pairs can generate or fill new entry orders. Existing positions keep normal exits after a pair is removed. Risk checks run at the daily close, not intraday; overnight gaps can exceed the intended stop. A newly opened position can trigger a risk exit at its first close. There is no cooldown after an executed exit.

## Execution accounting

For opening prices A and B, hedge ratio beta, past-only residual standard deviation sigma, and direction s = +1 for long spread or -1 for short spread, the current CLI uses:

```text
entryA = A * (1 + s * 0.0005)
entryB = B * (1 - s * 0.0005)
unit_gross = entryA + beta * entryB
G = clip(target_spread_risk / (sigma / unit_gross), 10000, 25000)
units = G / unit_gross
qA = s * units
qB = -s * beta * units
entry fill = market open * (1 + sign(quantity) * 0.0005)
exit fill  = market open * (1 - sign(quantity) * 0.0005)
gross PnL = qA * (exitA - entryA) + qB * (exitB - entryB)
commission = 0.001 * sum(abs(quantity) * fill price at entry and exit)
borrow = entry short notional * 0.03 * elapsed calendar days / 365
net PnL = gross PnL - commission - borrow
```

Net PnL is rounded to cents once per completed trade. Short spread sells A and buys beta units of B; long spread takes the opposite legs. Both slippage and commission apply to both legs and both fills.

The risk target defaults to 100 USD per residual standard deviation, not a daily volatility target or loss limit. See `SECTOR_SCREENING.md` for units, timing and the minimum-notional risk caveat. The historical baseline table below used fixed 10,000 USD sizing instead.

This is synthetic adjusted-share accounting, not a broker ledger. Dividends and splits are represented by Yahoo-adjusted prices rather than explicit cash flows. The current portfolio engine enforces a common collateral/cash budget and position/ticker admission limits; marked gains and losses affect available cash. It does not constrain market liquidity, check borrow availability, vary borrow rates or simulate forced broker margin calls. Completed-trade PnL is not total portfolio performance, since unrealized outcomes are excluded from the public snapshot. The private portfolio report includes marked outcomes and accrued costs.

## Export and reproducibility

- The public snapshot keeps exactly `mode`, `asOf`, `currency`, and `trades`. Each trade keeps exactly `id`, `pair`, `status`, `entryTime`, `exitTime`, and `netPnl`.
- Legacy `mode: "demo"` and `DEMO-NNN` IDs remain necessary to pass the unchanged validator. Separate public metadata identifies a historical backtest; the UI no longer describes generated prices as fictional.
- `asOf` is UTC midnight following the last completed data session. Requested dates and actual data coverage are separately recorded.
- Every pair must have the same final session. Data more than seven calendar days behind the requested end is rejected.
- Download failures or validation failures leave the previous public snapshot intact. The writer uses a temporary sibling file and atomic replacement only after the Node validator succeeds.
- `research/artifacts/pair_backtest/` holds the exact normalized CSV inputs (10 decimal places), SHA-256 hashes, package versions, parameters and run summary. These artifacts are ignored by Git and are not public assets. Yahoo can revise adjusted history; rerunning the same requested dates is not guaranteed to reproduce an earlier download.

## Expanded three-year OLS baseline run

Executed before the Kalman integration with the then-default OLS model (now selected with `--model ols`), exclusive end date 2026-09-08. Requested history begins 2023-09-08; actual data covers 2023-09-08 through 2026-09-04. The fixed seven-pair universe and 45-session / absolute Z > 3.5 risk exits produced the following completed outcomes after commission, slippage and borrow:

| Pair | Closed simulations | Net PnL (USD) |
| --- | ---: | ---: |
| AAPL / MSFT | 2 | -313.62 |
| XOM / CVX | 5 | -32.51 |
| JPM / BAC | 9 | 461.86 |
| V / MA | 5 | -54.38 |
| GOOGL / META | 3 | -996.02 |
| KO / PEP | 3 | -533.42 |
| NVDA / AMD | 4 | -1,233.35 |
| Total | 31 | -2,701.44 |

There were 11 profitable and 20 losing completed trades: win rate 35.48%, profit factor 0.3284, maximum realized drawdown USD 3,281.42. These are closed-only simulated outcomes, not live returns or total portfolio performance. Both the 15 Python tests and 10 JavaScript tests passed against this implementation/export.

## Earlier one-year run (before risk exits)

Executed with exclusive end date 2026-09-08 (the host's UTC date): requested 2025-09-08 through 2026-09-07. Yahoo supplied 251 synchronized daily sessions per pair, 2025-09-08 through 2026-09-04.

| Pair | Closed simulations |
| --- | ---: |
| AAPL / MSFT | 0 |
| KO / PEP | 0 |

That earlier snapshot had no closed records and USD 0.00 realized PnL. It has been superseded by the expanded run. A zero closed-only outcome does not imply zero strategy risk. The public site supports no-closed-results periods and marks undefined metrics N/A.

## Verification

```powershell
python -m unittest discover -s tests -p 'test_*pair*.py' -v
node --test public_site/tests/results.test.cjs
```

Python tests cover the statistical model, strict thresholds, mean crossing, next-open execution, frozen parameters, both spread directions, costs, daylight saving, future-data independence, open-position exclusion and import isolation. They also verify the three-year/seven-pair defaults, the strict Z-stop boundary, 45-session counting with missing sessions and the exclusion of risk exits that cannot yet fill. JavaScript tests use stable unit fixtures and separately validate the generated public snapshot, including the legitimate empty-result case.

References: [Statsmodels coint](https://www.statsmodels.org/stable/generated/statsmodels.tsa.stattools.coint.html) documents the no-cointegration null and I(1) assumption; [yfinance download](https://ranaroussi.github.io/yfinance/reference/api/yfinance.download.html) documents adjusted OHLC and the exclusive end date.
