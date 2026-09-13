# Shared-capital multi-pair portfolio engine

The latest 60-stock OLS/Kalman results, screening selections and interest-versus-trading attribution are recorded in [INSTITUTIONAL_COMPARISON.md](INSTITUTIONAL_COMPARISON.md). The older 30-stock run below remains a historical reference.

```powershell
python -m research.run_backtest --years 5 --model ols --initial-equity 100000
python -m research.run_backtest --max-concurrent-pairs 8 --max-ticker-fraction 0.35 --target-spread-risk 100
python -m unittest discover -s tests -p 'test_*pair*.py' -v
node --test public_site/tests/results.test.cjs
```

## Scope and components

`research/portfolio.py` owns the chronological simulation, pair contexts, collateral/cash ledger and private portfolio statistics. `research/pair_accounting.py` supplies the shared execution clocks and net liquidation calculation. `research/run_backtest.py` downloads adjusted history, builds the quarterly schedules, runs one portfolio engine and exports closed results. Its original `simulate_pair()` remains a historical regression oracle, not the CLI execution path. No existing database-backed backtester, bot, environment file or broker connection is used.

The existing 252-session training / 63-session screening cadence, p < 0.05, half-life < 20, H < 0.45 and top-ten screening limit are unchanged. All selected pairs are monitored, not just the maximum eight that can be open. OLS remains the default; the optional Kalman path uses the same portfolio ledger. Entry Hurst, inverse-volatility estimates, mean crossing, positive-net early profit at abs(Z) <= 0.5, abs(Z) > 3.5 and 45-session exits are preserved. See `SECTOR_SCREENING.md` for the statistical definitions and historical references.

## One chronological clock

The calendar is the union of observed universe session dates. Price and screening histories are never backward-filled. Pair signals require synchronized observed bars, while other pairs continue trading if one symbol lacks a quote.

For every evaluation session:

1. Mark existing legs using that session's available opens, or the last known mark for a missing quote. Settle the change in marked value and elapsed borrow into cash. Today's closes are not available at this stage.
2. Execute all previously requested exits with both fresh opening quotes, releasing their collateral before any new entry. A pending exit with missing quotes waits for the next synchronized open.
3. Cancel pending entries for removed pairs. Rank remaining orders by oldest signal session, then alphabetical pair name, independent of input iteration order. Entries without synchronized quotes wait; entries rejected for cash, concurrency or concentration are discarded. A later completed close can produce a new signal, but rejected orders are not queued indefinitely.
4. Size eligible entries using the frozen past volatility and current direction-specific opening fill prices. Enforce cash including entry costs, the eight-position limit and ticker concentration before admission.
5. Mark all open legs at available closes, settle market PnL and additional borrow, credit the accrued unreserved-cash yield, then generate next-open entry and exit decisions. The current signal close is excluded from OLS estimation, Hurst estimation and sizing volatility. Screening also excludes its effective session's prices.

A removed pair keeps its position, entry model, quantities, costs and holding clock until a normal exit. No quarterly liquidation or duplicate position is introduced. The 45-session clock still counts observed synchronized pair sessions, including its entry session. Future selections cannot enable past entries. End-of-sample positions are marked but not force-closed or exported as completed trades.

## Cash and margin convention

This is an explicitly synthetic, fully collateralized spread-account model, not a claim to reproduce a broker's regulatory margin rules. Initial equity defaults to 100,000 USD. Opening a position locks 100% of its gross entry fill notional as collateral. Short-sale proceeds do not become additional spendable cash. Collateral stays fixed until exit, even as current market exposure changes.

For position quantities q, opening market prices M, entry fills F and entry gross G:

```text
G = sum(abs(q) * F)
entry_commission = 0.001 * G
entry_slippage_loss = dot(q, F - M)
available_cash -= G + entry_commission + entry_slippage_loss
reserved_margin += G

at each open/close mark:
    available_cash += dot(q, new_mark - previous_mark)
    available_cash -= entry_short_notional * 0.03 * elapsed_calendar_days / 365

at exit, after the opening mark:
    available_cash += G - exit_commission - exit_slippage_loss
    reserved_margin -= G

portfolio_equity = available_cash + reserved_margin
```

Mark changes are settled as variation cash. Thus unrealized losses immediately reduce purchasing capacity, and marked gains can fund subsequent positions. Entry slippage is charged immediately, not delayed until the eventual trade closes. Exit fees/slippage are charged only at exit; borrow is accrued at every mark, including weekends and missing-quote intervals. The closed-trade liquidation formula independently recomputes net PnL from fills and lifetime borrow, but is not added to cash again. This avoids double-counting costs or already-settled market PnL.

Inverse-volatility sizing targets 100 USD per spread residual standard deviation by default and 10,000-25,000 USD gross entry fills. Cash caps the number of units after reserving both collateral and entry commission/slippage. An affordable size below 10,000 USD is rejected rather than clamped back upward. There is no admission that knowingly spends unavailable cash, and the default model has no leverage multiplier.

Subsequent losses or accrued borrow can make available cash negative even though an entry was affordable. This is a recorded collateral shortfall: no further entries can use that deficit, and existing positions retain normal exits. The engine does not assume a loan facility, fabricate cash or implement forced broker margin liquidation. Financing of a shortfall and exchange half-day closing clocks are not modeled. The daily close clock remains 16:00 America/New_York; opens are 09:30, with UTC daylight-saving conversion.

### Unreserved cash yield

`--risk-free-rate 0.045` defaults to a constant assumed 4.5% annual nominal ACT/365 rate, not a fetched historical Treasury curve. Interest starts at the first evaluation session's open, with no income during the preceding screening-training year. Before each open/close mark changes cash, accrue `max(previous_available_cash, 0) * rate * elapsed_calendar_days / 365`. The balance after that event applies only to subsequent elapsed time. Weekends, holidays, missing quotes and UTC daylight-saving offsets are included by elapsed seconds. Capital freed at Monday's open therefore cannot earn interest for the preceding weekend.

Unpaid accrual is accumulated separately and credited to spendable cash once at each session close, after variation settlement and borrow. It compounds from subsequent intervals only; it cannot fund an earlier entry. Locked gross margin, short proceeds and negative cash earn no interest. Positive settled variation cash can earn interest. The quoted rate is a research assumption, not an actual bank sweep facility. Setting the rate to zero reproduces the previous cash ledger.

The private reconciliation is `portfolioPnl = realizedPnlUnrounded + openNetMarkedPnl + cashYield`. `tradingPnl` excludes cash yield but includes marked open trading results and all already charged execution/borrow costs. Cash yield is never allocated to a trade or used to decide that trade's early profit exit.

## Exposure and admission limits

The default concurrency limit is eight pair positions. Each pair can have only one position. A screening pool may be larger, and positions removed from the pool still consume slots and collateral while awaiting exit.

The single-ticker limit is 35% of current marked equity after proposed entry costs, not 35% of original capital. Existing ticker exposure sums absolute quantities times the latest available market price, without netting opposing legs across pairs. New legs are conservatively valued at the higher of their opening mark and execution fill. Any concentration violation in the projected whole portfolio rejects the entry, including a pre-existing breach in an unrelated ticker. Price moves can push an already-held ticker above its admission cap; the engine does not forcibly resize that position, and new entries wait until a fresh signal can be admitted without a breach. This is an admission control, not a continuous intraday hedge or liquidation rule.

No entry ranking uses future returns, later closes or end-of-period profitability. Alphabetical tie-breaking is deterministic but is a research policy choice, not an optimal allocation claim. Repeated strategy experiments and the preset stock universe still introduce selection/survivorship risks; chronological code cannot make revised Yahoo-adjusted history a point-in-time corporate-action database.

## Metrics and publication boundary

The private `research/artifacts/pair_backtest/portfolio-report.json` contains the portfolio summary, chronological mark/execution equity samples and admission/rejection events. `run-summary.json` contains the same portfolio summary and the usual data hashes/configurations. Neither file belongs in `public_site/`.

Portfolio PnL is final marked equity minus initial equity, after accrued modeled costs. Portfolio return divides that PnL by initial equity. Maximum portfolio drawdown is the largest peak-to-trough loss in the open/close and execution-event equity samples, including the initial equity baseline; percentage drawdown uses each sample's running peak. These metrics include open positions, but not hypothetical future exit costs. They do not measure unobserved intraday extremes. Win rate counts positive-net closed trades only, over all completed trades.

The public snapshot retains exactly the existing closed-trade schema. It does not publish total marked equity, available cash, the equity curve, open positions/counts, peak concurrency, current exposures, pending signals or selection lists. Public metadata may show initial capital and configured limits because those are method settings, not current portfolio state. The dashboard's PnL/drawdown remain explicitly realized closed-trade metrics; they must not be mislabeled as total portfolio metrics. The local final research summary reports both measures separately.

Cash accounting uses unrounded values; each public closed trade is rounded to cents once. Small differences between the sum of rounded public trades and unrounded realized portfolio PnL are rounding, not missing fees.

### Daily risk metrics

Only one post-interest `close_mark` equity observation per session enters the ratios, with initial equity at the first evaluation open as the first return's baseline. Open marks and fills do not create extra daily observations. All idle sessions are included, including empty-universe quarters. Let `r[t] = equity[t] / equity[t-1] - 1`, `rf[t] = configured_rate * elapsed_calendar_days[t] / 365`, and `x[t] = r[t] - rf[t]`.

```text
annualized arithmetic excess return = 252 * mean(x)
annualized excess volatility = sqrt(252) * sample_std(x, ddof=1)
Sharpe = annualized arithmetic excess return / annualized excess volatility
downside deviation = sqrt(252 * mean(min(x, 0)^2))
Sortino = annualized arithmetic excess return / downside deviation
CAGR = (final_equity / initial_equity)^(365 / elapsed_calendar_days) - 1
Calmar = CAGR / maximum_portfolio_drawdown_fraction
average capital utilization = mean(daily_current_gross_open_exposure / daily_equity)
```

The Sharpe numerator is annualized arithmetic excess return, not five-year cumulative return or CAGR minus one annual rate. The daily risk-free benchmark matches the actual cash accrual clock, so weekends do not manufacture excess returns for an all-cash portfolio. This follows the differential-return formulation of [William Sharpe (1994)](https://web.stanford.edu/~wfsharpe/art/sr/SR.htm). Sortino uses the same risk-free minimum acceptable return and a lower partial second moment over **all** sessions, not the standard deviation of losses alone. The 252-session annualization is conventional, not a correction for serial correlation. `annualizedReturnPct` separately reports CAGR. Calmar preserves the existing more conservative open/close/execution-event maximum drawdown denominator; it is not based on closed-trade drawdown.

Utilization measures current gross marked legs without netting, not entry collateral or the count of open pairs. Zero denominators, fewer than two return observations for Sharpe/Sortino, and nonpositive equity produce `null` where a ratio cannot be defined, never infinity. `riskFreeBenchmarkPnl` reports the counterfactual all-cash portfolio using the identical ACT/365 schedule and close compounding. A positive total PnL may still underperform this benchmark.

### Reproducible model comparison

```powershell
python -m research.run_backtest --years 5 --model ols --initial-equity 100000 --end 2026-09-09 --no-publish
python -m research.run_backtest --years 5 --model kalman --initial-equity 100000 --end 2026-09-09 --reuse-data --no-publish
python -m research.compare_pair_models
```

The first run saves 17-significant-digit adjusted Open/Close CSVs and a `data-manifest.json`; both runs load this same round-trip representation. `--reuse-data` performs no download and rejects a mismatched date window, universe, hash or malformed history. Per-model private bundles live in `research/artifacts/pair_backtest/ols/result.json` and `kalman/result.json`, each containing audited configuration, screening history, source/data hashes, equity/events and a separate closed snapshot/public-method payload. The root run/portfolio reports continue to describe the last executed model, not necessarily the published winner.

`research.compare_pair_models` rejects different price/source hashes, package versions, evaluation boundaries, screening schedules and shared execution/risk/portfolio parameters. Before inspecting outcomes, the comparison rule was fixed to higher finite daily excess-return Sharpe, then lower portfolio drawdown, then OLS for an exact tie; undefined Sharpe ranks last. It writes a private `comparison.json` and publishes only the winner's closed snapshot and method parameters. Selecting an ex-post winner on the evaluated period is a retrospective comparison, **not** an unbiased holdout validation or evidence of future superiority. The signal path itself remains causal. The OLS/Kalman comparison includes their existing model-specific entry rules, including Kalman's OU half-life gate.

## Earlier 30-stock OLS run without cash yield

The final requested command completed successfully:

```powershell
python -m research.run_backtest --years 5 --model ols --initial-equity 100000
```

Its recorded requested history is `[2021-09-09, 2026-09-09)`, with observed prices through 2026-09-04 and evaluation beginning 2022-09-09 after the first 252 sessions. Pin the date to request the same window in later research runs:

```powershell
python -m research.run_backtest --years 5 --model ols --initial-equity 100000 --end 2026-09-09
```

There were 16 quarterly screens and 992 pair-window evaluations across 62 distinct candidates; 27 unique pairs entered the monitoring universe at least once. The UTC default end date rolled from September 8 to September 9 during verification. The final export uses the latter boundary, which shifts trailing screening windows by one session. Earlier progress numbers from the September 8 run are not the final export and should not be attributed to an allocation improvement. Yahoo can also revise adjusted data, so the stored CSVs and hashes are the reference for the exact run.

| Portfolio metric | Final value |
| --- | ---: |
| Initial equity | 100,000.00 USD |
| Final marked equity | 100,562.30 USD |
| Portfolio PnL after costs | +562.30 USD |
| Portfolio return | +0.5623% |
| Peak concurrent positions | 2 (configured limit: 8) |
| Closed simulations | 18 |
| Wins / losses | 12 / 6 |
| Win rate | 66.67% |
| Maximum marked portfolio drawdown | 331.57 USD |
| Maximum marked portfolio drawdown percent | 0.3302% |

All positions happened to be closed by the final session; no final liquidation was forced. No entries in this particular market run were rejected by admission limits. Synthetic tests separately exercise binding concurrency, cash and concentration constraints, so an unchanged admission set here is not proof that those limits are unnecessary.

Total modeled costs were 288.714189 USD commission, 144.357361 USD adverse slippage and 71.512479 USD accrued borrow. Portfolio PnL uses full-precision accounting (562.299745 USD); summing the individually rounded public closed trades gives 562.31 USD. The public closed-only maximum drawdown is 202.57 USD, whereas the 331.57 USD portfolio drawdown also captures open marked losses along the way. The public profit factor is 2.2625. Neither a low observed drawdown nor 18 trades establishes robustness.

Verification: 79 Python tests and all 10 public web tests passed. The new 21 portfolio tests cover concurrent admission, affordable resizing, gross ticker caps including opposite legs and pre-existing unrelated breaches, current-open versus future-close ordering, exits releasing margin first, input-order invariance, both-direction cost reconciliation, missing quotes/borrow accrual, prefix invariance, early-profit/Hurst gates, carried-position time stops, Kalman parity and closed-only reporting. Independent checks on the actual generated artifacts reconciled every equity sample's cash/collateral identity, marked drawdown, peak concurrency, entry eligibility, size bounds and public totals. `node --check public_site/app.js` also passed.
