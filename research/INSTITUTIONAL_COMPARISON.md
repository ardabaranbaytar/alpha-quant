# 60-stock portfolio research comparison

## Capital-utilization upgrade: 10k-25k sizing

The current recorded run uses a 100 USD residual-standard-deviation target, 10,000-25,000 USD gross entry bounds and a 35% ticker admission cap. It retains 100,000 USD starting equity, eight concurrent-pair slots, the 4.5% cash yield, the same 60-stock universe and the same hash-verified price snapshot. The preceding 5,000-15,000 USD / 50 USD / 25% run is the baseline below; do not mix its figures with the upgraded run.

| Metric | Prior OLS (5k-15k) | Upgraded OLS (10k-25k) | Prior Kalman (5k-15k) | Upgraded Kalman (10k-25k) |
| --- | ---: | ---: | ---: | ---: |
| Average capital utilization | 1.6137% | 3.2132% | 3.2309% | 6.4557% |
| Net trading PnL, USD | +484.22 | +971.21 | -763.13 | -1,470.47 |
| Cash yield, USD | +19,344.34 | +19,028.09 | +18,932.57 | +18,212.26 |
| Total portfolio PnL, USD | +19,828.56 | +19,999.30 | +18,169.44 | +16,741.79 |
| Portfolio return | +19.8286% | +19.9993% | +18.1694% | +16.7418% |
| Closed-trade win rate | 50.00% | 50.00% | 59.1549% | 59.1549% |
| Maximum marked drawdown, USD | 192.37 | 428.95 | 557.24 | 1,414.28 |
| Sharpe | 0.0955 | 0.0997 | -0.4515 | -0.4396 |
| Sortino | 0.1585 | 0.1658 | -0.6431 | -0.6276 |

OLS remains the retrospective winner under the predeclared Sharpe rule. Its trading PnL almost doubled, while its average utilization doubled and drawdown increased. Kalman's utilization also doubled, but the larger negative trading result outweighed the additional cash yield. The public snapshot was regenerated from upgraded OLS and contains closed trades only.

## Baseline: 5k-15k sizing

Recorded on 2026-09-09. This is a retrospective historical simulation, not a live fund, a point-in-time constituent study or held-out model validation. The bot, original project, environment files and real database were not used.

## Reproduction

```powershell
python -m research.run_backtest --years 5 --model ols --initial-equity 100000 --end 2026-09-09 --no-publish
python -m research.run_backtest --years 5 --model kalman --initial-equity 100000 --end 2026-09-09 --reuse-data --no-publish
python -m research.compare_pair_models
python -m unittest discover -s tests -p 'test_*pair*.py' -v
node --test public_site/tests/results.test.cjs
```

Both models consumed exactly the same hash-verified adjusted Open/Close CSVs, package versions, shared settings and source versions. Requested data: `[2021-09-09, 2026-09-09)`. Yahoo's observed final session was 2026-09-04; no missing final date was filled. Evaluation began at the 2022-09-09 open after 252 training sessions and included 1,001 evaluation closes. Five downloaded years therefore produce approximately four years of portfolio performance. Future downloads can revise adjusted prices; retain the private CSVs and manifest for exact price-input reproduction.

The user-listed nine groups contain 56 names. Utilities NEE, DUK, SO and AEP complete 60 names in ten research groups. BK remains the stable research symbol for the same security downloaded as BNY following the issuer's May 2026 ticker change; the private audits record the alias. See [sector definitions and source](SECTOR_SCREENING.md).

## Screening

There are **163 distinct same-group candidates per screen**, not 163 positions. Sixteen quarterly screens produced **2,608 pair-window evaluations**, 103 selected pair-period memberships and **65 unique pairs selected at least once**. Every screening input ended before its effective session. The 252-session training window, 63-session rebalance, p < 0.05, half-life < 20, H < 0.45 and top-ten cap are unchanged. All selected pairs are monitored concurrently, with at most eight positions admitted.

Mutually exclusive elimination counts: 2,158 cointegration failures, 330 invalid spread models, 11 Hurst failures, one half-life failure and five eligible pairs outside the ranking cap. Total exclusions: 2,505; adding 103 selected memberships reconciles to 2,608 evaluations. No pairs were silently dropped for missing downloads, and thresholds were not relaxed.

| Effective session | Selected monitoring pairs, in screening rank order |
| --- | --- |
| 2022-09-09 | GS/BLK, MSFT/AMD, JNJ/ABBV, JNJ/BMY, MSFT/NVDA, BAC/BK |
| 2022-12-08 | NVDA/AVGO, MSFT/AMD, MSFT/QCOM, CB/TRV, AMD/AVGO, MSFT/AVGO, JNJ/ABBV |
| 2023-03-13 | ALL/MET, KO/CL, KO/PG, MSFT/AMD, CVX/EOG, MSFT/QCOM, CB/TRV, HON/LMT |
| 2023-06-12 | PG/KMB, ALL/MET, COP/EOG, NEE/AEP, KO/PG, MSFT/AMD |
| 2023-09-12 | CL/KMB, MS/BK, MMM/NOC, COP/EOG, GOOGL/NVDA |
| 2023-12-11 | META/NVDA, GS/BK, AAPL/AMD |
| 2024-03-13 | BAC/BLK, SO/AEP, MSFT/AVGO, CB/PGR, BAC/GS, SLB/EOG |
| 2024-06-12 | JPM/C, MSFT/AVGO, JPM/WFC, CB/PGR, META/AVGO |
| 2024-09-12 | PG/CL, ALL/MET, NEE/SO, CB/MET |
| 2024-12-11 | PFE/AMGN, DUK/SO, CL/KMB, NVDA/AVGO, ALL/MET, CB/PGR |
| 2025-03-17 | TRV/ALL, MMM/RTX, BAC/MS, JNJ/ABBV, LLY/AMGN, AAPL/NVDA, JPM/GS, GE/RTX |
| 2025-06-16 | LLY/AMGN, JNJ/ABBV, TRV/ALL, PG/MDLZ, KMB/MDLZ, PG/CL, COP/SLB, DUK/SO, PG/KMB |
| 2025-09-16 | DUK/AEP, BAC/MS, ALL/MET, CB/PGR |
| 2025-12-15 | C/GS, WFC/GS, WFC/MS, BAC/MS, NVDA/AVGO, COP/OXY |
| 2026-03-18 | C/GS, CAT/RTX, C/BK, DE/HON, JNJ/PFE, JPM/BAC, MRK/AMGN, ALL/MET, MMM/GE, PFE/AMGN |
| 2026-06-17 | JPM/BAC, PFE/MRK, JNJ/PFE, DE/HON, PFE/AMGN, NVDA/AMD, PFE/BMY, C/BK, NVDA/AVGO, NVDA/QCOM |

## Portfolio results

Initial equity is 100,000 USD for each model. Trade costs remain 10 bps commission, 5 bps adverse slippage per leg at both entry and exit, and 3% annual borrow on actual entry short notional. Interest is an assumed constant 4.5% nominal ACT/365 on time-held positive unreserved cash, credited at each close; it is not a historical Treasury rate series. Baseline sizing used inverse past-residual volatility with 5,000-15,000 USD gross bounds, subject to shared available cash and admission limits.

| Metric | OLS | Kalman |
| --- | ---: | ---: |
| Closed trades | 38 | 71 |
| Peak concurrent positions | 3 | 5 |
| Final marked portfolio equity, USD | 119,828.56 | 118,169.44 |
| Net portfolio PnL including cash yield, USD | +19,828.56 | +18,169.44 |
| Portfolio return | +19.8286% | +18.1694% |
| Net trading PnL, including final open marks, USD | +484.22 | -763.13 |
| Net closed-trade PnL, unrounded ledger summed, USD | +484.22 | -322.07 |
| Net final open marked PnL, USD | 0.00 | -441.06 |
| Cash yield, USD | +19,344.34 | +18,932.57 |
| Closed-trade win rate | 50.00% | 59.1549% |
| Maximum marked portfolio drawdown, USD | 192.37 | 557.24 |
| Maximum marked portfolio drawdown | 0.1615% | 0.5494% |
| Annualized Sharpe | 0.0955 | -0.4515 |
| Annualized Sortino | 0.1585 | -0.6431 |
| Calmar | 28.7143 | 7.7776 |
| CAGR | 4.6382% | 4.2732% |
| Average capital utilization | 1.6137% | 3.2309% |
| Total commission, USD | 541.30 | 848.15 |
| Total adverse slippage, USD | 270.65 | 424.08 |
| Total accrued borrow, USD | 106.40 | 218.37 |

OLS had no final open positions; Kalman had two, which were marked but never force-closed or published. The portfolio statistics include paid open-position costs and accrued borrow, but not hypothetical future exit costs. Kalman canceled one pending entry at deselection. No actual entries hit the cash/concurrency/ticker admission caps in this run; synthetic tests exercise those binding cases.

The identical-schedule **all-cash benchmark earned 19,664.08 USD (+19.6641%)**. OLS exceeded it by only 164.48 USD; Kalman underperformed it by 1,494.64 USD. Most positive portfolio PnL came from idle-cash interest, not statistical-trading alpha. Low capital utilization and smooth cash income also help explain the small total-portfolio drawdowns and high Calmar ratios. They do not establish robust strategy performance.

Sharpe and Sortino use annualized arithmetic daily **excess** returns over the calendar-matched risk-free benchmark. Calmar uses CAGR divided by marked portfolio drawdown, preserving open/close/execution samples. Full equations, zero-denominator behavior and accrual ordering are in [portfolio methodology](PORTFOLIO_ENGINE.md). Changing the historical universe and comparing models after observing results introduces research selection risk even though individual signals, screening windows and allocations are causal. Raw pair p-values have no multiple-testing correction; Yahoo's revised adjusted history and the static present-day security list are additional limitations.

## Selection and public export

The rule was fixed before observing outcomes: higher finite Sharpe, then lower portfolio drawdown, then OLS for an exact tie; undefined Sharpe ranks last. **OLS wins this retrospective comparison.** This is not an independent holdout test of the selected model.

`public_site/demo-data.js` contains only OLS's 38 closed trades and public method settings. Its PnL is **484.21 USD**, the sum of trade-level cent-rounded values, versus 484.221697 USD from the unrounded ledger. The one-cent displayed difference is rounding. Cash yield, marked portfolio metrics, open positions/counts, pending signals, active selections and peak concurrency are absent from public assets. Private model bundles and `comparison.json` remain under `research/artifacts/pair_backtest/`. Root `run-summary.json` and `portfolio-report.json` describe Kalman, the last executed model; use the OLS archive or comparison report for the published winner's private metrics.

Verification: **97 Python tests passed; all 10 web tests passed.** Independent calculations on 2,079 OLS and 2,147 Kalman equity samples reconciled cash plus collateral, time-weighted interest credits, commissions/slippage, closed realization events, peak concurrency, marked drawdown, daily Sharpe/Sortino, average capital utilization, the all-cash benchmark and past-only screening boundaries. Deterministic tests also cover negative cash, reserved margin, Friday/Monday accrual, same-open collateral release, zero-variance metrics, invalid inputs, price snapshot round-trip integrity, incompatible model comparisons and future-price/prefix invariance.
