# Alpha Quant public results preview

Open `index.html` in a browser. No server, dependency installation, network connection, Python import, environment file, or database connection is required. The existing bot entry point is not used.

This local English-language dashboard displays the latest generated historical backtest on real Yahoo Finance prices. Execution is simulated; it is not live bot performance. Nothing has been deployed.

## Files

- `index.html`, `styles.css`, `app.js`: overview, closed-trade journal, methodology, responsive charts and filters.
- `demo-data.js`: generated closed-only snapshot and public source/cost metadata.
- `results.js`: strict snapshot validation and pure performance calculations.
- `tests/results.test.cjs`: data privacy contract and calculation tests.
- `tests/fixtures/demo-data.cjs`: stable fictional test cases, independent of market results.

Run the isolated tests with `node --test public_site/tests/results.test.cjs` from the repository root. This command uses only the Node standard library and the static site's modules.

## Publication boundary

Every file delivered to a browser is public. Client-side validation is a second check, not an access control boundary. Never place raw position exports, signals, open-position records or counts, logs, credentials, or environment files in this directory. Never host the repository root as static content.

The legacy contract still requires `mode: "demo"` and `DEMO-NNN` identifiers. These values are preserved for schema compatibility, not used as a claim that prices are fictional. The generated file exposes separate `ALPHA_REPORT_INFO` browser metadata with `kind: "historical_backtest"`. The UI uses it to identify the source, coverage and costs. No extra fields were added to the validated snapshot or trades.

`research/run_backtest.py` constructs records only when a simulated exit executes. It validates the entire snapshot with the unchanged `results.js` contract before atomically replacing `demo-data.js`. Still-open positions, pending orders, signals and entry thresholds are not serialized. Raw price CSVs, hashes, configuration and the run summary stay in `research/artifacts/pair_backtest/`, outside the site. Validation or download failures do not replace the last valid public snapshot.

Current allowed trade fields: `id`, `pair`, `status`, `entryTime`, `exitTime`, `netPnl`. `status` must be `CLOSED`; closing time must fall between entry time and the snapshot timestamp. Extra fields, duplicate IDs, malformed records, or an open record cause the entire snapshot to be rejected.

## Metric definitions

- Reporting windows are `(snapshot time - N days, snapshot time]`, evaluated by closing timestamp in UTC. All time includes every snapshot trade.
- Net realized P&L sums cost-adjusted closed outcomes. The backtest applies 10 bps commission and 5 bps adverse slippage per leg per fill, plus 3% annual borrow on entry short notional, prorated by elapsed calendar days.
- Win rate includes all closed trades in the denominator. Breakeven is not a win or loss.
- Profit factor is positive P&L divided by absolute negative P&L; no losses means undefined, displayed as N/A.
- The realized P&L curve starts at zero in each selected period. It changes only when trades close. It is not mark-to-market equity or an investment return percentage.
- Maximum realized drawdown includes the initial zero baseline and is reported in USD.
- Holding times are elapsed calendar time. Missing statistics are N/A rather than fabricated results.

## Refresh the backtest

From this copy's repository root:

```powershell
python -m research.run_backtest
node --test public_site/tests/results.test.cjs
python -m unittest discover -s tests -p 'test_*pair*.py' -v
```

The runner downloads five calendar years ending at today's UTC date (exclusive). Without `--pairs`, it screens 60 preset stocks in ten separate groups (163 within-group combinations) after the first 252 observed market sessions, then repeats every 63 sessions using only the preceding 252 sessions. The requested list had 56 names; NEE, DUK, SO and AEP form a separate Utilities group to reach 60. Each selection becomes effective at the next session open after the training window. Screening requires p < 0.05, 0 < half-life < 20 and H < 0.45, with at most ten selected pairs. It never fills a target quota by relaxing thresholds. Removed pairs cannot open new positions, including pending entries; existing positions continue with unchanged exit rules, costs and holding clocks. Empty selections disable all new entries. The download spans five years; out-of-training performance spans approximately four years. It never imports bot settings or connects to the real database. `python research/run_backtest.py` is also supported. Use `--years N`, `--end YYYY-MM-DD`, or `--pairs AAPL/MSFT KO/PEP` for an explicit manual list that bypasses screening. Refresh the browser after export.

CLI entries use inverse residual-volatility sizing with a default 100 USD one-standard-deviation exposure target (`--target-spread-risk 100`). The previous 60 residual observations exclude the signal close. Gross exposure at execution prices including slippage is constrained to 10,000-25,000 USD, and position quantities then stay fixed. This residual-level risk proxy is not daily volatility or a loss limit; the floor can exceed the target risk. All commissions and borrow charges scale with actual quantities and prices. Public method metadata describes these assumptions, without per-position quantities or volatility estimates.

The CLI runs one chronological portfolio with `--initial-equity 100000`, `--max-concurrent-pairs 8` and `--max-ticker-fraction 0.35`. Entry gross collateral and costs share one cash pool; short proceeds cannot be reused. Cash is marked at opens/closes and borrow accrues over elapsed time. Entries are reduced to affordable size or rejected below 10,000 USD, at the position limit, or for ticker concentration. Same-open exits release collateral before entries. `../research/PORTFOLIO_ENGINE.md` defines this synthetic cash/margin convention and its limitations.

Positive unreserved cash earns the assumed `--risk-free-rate 0.045` on an ACT/365 basis for the actual time each balance was held, credited once per session close. Reserved collateral and negative cash earn nothing. The private portfolio summary separates cash yield from trading PnL and includes daily excess-return Sharpe/Sortino, CAGR-based Calmar and average marked gross capital utilization. None of these marked portfolio outcomes are browser data. The public closed-trade PnL does not include cash interest.

For a same-input comparison, run OLS with a pinned `--end YYYY-MM-DD --no-publish`, then Kalman with the same arguments plus `--reuse-data`. Both read identical hash-verified price snapshots. `python -m research.compare_pair_models` compares the two private model archives and publishes the higher-Sharpe model's closed snapshot only (drawdown then OLS break ties; undefined Sharpe ranks last). This is retrospective research selection, not holdout validation. Full definitions, source-identity checks and the BK-to-BNY provider symbol mapping are documented under `../research/`.

Public PnL, drawdown and trade counts remain closed-only. Full marked portfolio PnL/return/drawdown, peak concurrency, cash and the equity curve stay in the private local `research/artifacts/pair_backtest/portfolio-report.json`, not browser assets. Public `portfolioConfig` metadata contains method parameters only. The dashboard must not describe its realized curve as a marked portfolio curve.

In addition to mean reversion, risk exits trigger after 45 completed synchronized sessions (entry session included), or when absolute closing Z-score exceeds 3.5. All exits execute at the following session open and incur the same costs. Unexecuted exits at the end of the sample remain excluded. These are daily-close risk checks, not intraday stop orders or a guaranteed dollar loss cap.

The default signal model is `--model ols`, following the selected baseline. `--model kalman` remains available with its dynamic alpha/beta and OU gate. Both CLI modes enforce H < 0.45 on the 120 previous residuals at entry and request early profit-taking at absolute Z <= 0.5 only when estimated net liquidation PnL at the close is positive. Actual exits still fill at the next open, so estimated profit is not guaranteed. Public metadata identifies the model and evaluation start without exposing current estimates or signals. Entry quantities remain fixed even when Kalman estimates evolve. See `../research/SECTOR_SCREENING.md` for screening and Hurst definitions and `../research/KALMAN_MODEL.md` for the Kalman equations.

For full strategy details and run results, see `../research/PAIR_BACKTEST.md`.
