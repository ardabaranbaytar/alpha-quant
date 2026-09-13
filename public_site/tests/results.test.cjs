"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const { validateSnapshot, selectPeriod, summarize, filterTrades } = require("../results.js");
const data = require("./fixtures/demo-data.cjs");
const clone = () => structuredClone(data);

test("demo snapshot contains only closed, public fields and covers winning, losing, and flat results", () => {
  const snapshot = validateSnapshot(data);
  assert.equal(snapshot.trades.length, 6);
  const summary = summarize(snapshot.trades);
  assert.equal(summary.wins + summary.losses + summary.flat, 6);
  assert.equal(summary.flat, 1);
  assert.ok(summary.wins > 0 && summary.losses > 0);
  assert.equal(snapshot.mode, "demo");
});

test("rejects the whole snapshot if a private record or field is present", () => {
  for (const mutate of [
    snapshot => { snapshot.trades[0].status = "OPEN"; },
    snapshot => { snapshot.trades[0].signal = "BUY"; },
    snapshot => { snapshot.openPositionCount = 4; },
    snapshot => { snapshot.trades[0].entryZScore = 2.5; },
    snapshot => { snapshot.mode = "live"; }
  ]) {
    const snapshot = clone(); mutate(snapshot);
    assert.throws(() => validateSnapshot(snapshot));
  }
});

test("rejects missing, non-finite, out-of-order, future, and duplicate trade data", () => {
  for (const mutate of [
    snapshot => { snapshot.trades[0].netPnl = NaN; },
    snapshot => { snapshot.trades[0].netPnl = Infinity; },
    snapshot => { snapshot.trades[0].netPnl = "10"; },
    snapshot => { delete snapshot.trades[0].exitTime; },
    snapshot => { snapshot.trades[0].exitTime = "2026-01-01T00:00:00Z"; },
    snapshot => { snapshot.trades[0].exitTime = "2027-01-01T00:00:00Z"; },
    snapshot => { snapshot.trades[0].exitTime = "2026-02-30T00:00:00Z"; },
    snapshot => { snapshot.trades[0].pair = "<script> / MSFT"; },
    snapshot => { snapshot.trades[1].id = snapshot.trades[0].id; }
  ]) {
    const snapshot = clone(); mutate(snapshot);
    assert.throws(() => validateSnapshot(snapshot));
  }
});

test("rejects a sector outside the closed research-universe allowlist", () => {
  for (const mutate of [
    snapshot => { snapshot.trades[0].sector = "<script>Industrials</script>"; },
    snapshot => { snapshot.trades[0].sector = "Made Up Sector"; },
    snapshot => { snapshot.trades[0].sector = 42; },
    snapshot => { delete snapshot.trades[0].sector; },
    snapshot => { snapshot.trades[0].entryZScore = 2.1; } // a signal/threshold field must still never be accepted
  ]) {
    const snapshot = clone(); mutate(snapshot);
    assert.throws(() => validateSnapshot(snapshot));
  }
  const snapshot = clone();
  snapshot.trades[0].sector = "Unclassified";
  assert.equal(validateSnapshot(snapshot).trades.find(t => t.id === snapshot.trades[0].id).sector, "Unclassified");
});

test("maximum drawdown includes initial losses and subsequent peak declines", () => {
  const trades = validateSnapshot(data).trades.slice(0, 4).map((trade, i) => ({ ...trade, netPnl: [-100, 250, -220, 20][i] }));
  const summary = summarize(trades);
  assert.equal(summary.pnl, -50);
  assert.equal(summary.drawdown, 220);
  assert.equal(summary.winRate, 50);
  assert.equal(summary.profitFactor, 270 / 320);
  assert.deepEqual(summary.curve.map(point => point.pnl), [0, -100, 150, -70, -50]);
  assert.equal(summarize(trades.slice(0, 1)).drawdown, 100);
});

test("closed-trade tearsheet stats are derived only from the trade P&L sample, not a NAV series", () => {
  const trades = validateSnapshot(data).trades.slice(0, 4).map((trade, i) => ({ ...trade, netPnl: [-100, 250, -220, 20][i] }));
  const summary = summarize(trades);
  assert.ok(Number.isFinite(summary.closedTradeSharpe));
  assert.ok(Number.isFinite(summary.closedTradeSortino));
  assert.equal(summary.profitToDrawdown, summary.pnl / summary.drawdown);
  assert.equal(summary.riskRewardRatio, summary.averageWin / Math.abs(summary.averageLoss));
  assert.equal(summarize([]).closedTradeSharpe, null);
  assert.equal(summarize([]).profitToDrawdown, null);
  assert.equal(summarize([{ ...trades[0], netPnl: 100 }]).closedTradeSharpe, null); // a single sample has no sample deviation
});

test("sector and holding-duration sort orders are available alongside the existing ones", () => {
  const snapshot = validateSnapshot(data);
  const bySector = filterTrades(snapshot.trades, "", "all", "sector");
  assert.deepEqual(bySector.map(t => t.sector), [...bySector.map(t => t.sector)].sort());
  const longest = filterTrades(snapshot.trades, "", "all", "longest");
  const shortest = filterTrades(snapshot.trades, "", "all", "shortest");
  const holdMs = t => Date.parse(t.exitTime) - Date.parse(t.entryTime);
  assert.ok(holdMs(longest[0]) >= holdMs(longest.at(-1)));
  assert.ok(holdMs(shortest[0]) <= holdMs(shortest.at(-1)));
});

test("no losses, no wins, breakeven, and empty periods remain well-defined", () => {
  const trade = validateSnapshot(data).trades[0];
  assert.equal(summarize([{ ...trade, netPnl: 100 }]).profitFactor, null);
  assert.equal(summarize([{ ...trade, netPnl: -100 }]).profitFactor, 0);
  assert.equal(summarize([{ ...trade, netPnl: 0 }]).winRate, 0);
  assert.equal(summarize([]).winRate, null);
  assert.equal(summarize([]).best, null);
  assert.equal(summarize([]).averageHoldMs, null);
  assert.equal(summarize([]).pnl, 0);
  assert.equal(summarize([]).drawdown, 0);
});

test("reporting windows use the snapshot, closing timestamps, and an exclusive lower boundary", () => {
  const source = clone();
  const boundary = new Date(Date.parse(source.asOf) - 30 * 86400000).toISOString().replace(".000Z", "Z");
  source.trades = [{ ...source.trades[0], exitTime: boundary }, { ...source.trades[1], exitTime: source.asOf }];
  const snapshot = validateSnapshot(source);
  assert.deepEqual(selectPeriod(snapshot, "30").map(trade => trade.id), ["DEMO-002"]);
  assert.equal(selectPeriod(snapshot, "all").length, 2);
  assert.throws(() => selectPeriod(snapshot, "7"));
});

test("pair search, outcome filtering, and sorting compose without mutating the source", () => {
  const snapshot = validateSnapshot(data);
  const ids = snapshot.trades.map(trade => trade.id);
  const trades = filterTrades(snapshot.trades, " aapl ", "win", "best");
  assert.ok(trades.length > 1);
  assert.ok(trades.every(trade => trade.pair.includes("AAPL") && trade.netPnl > 0));
  assert.ok(trades.every((trade, index) => index === 0 || trades[index - 1].netPnl >= trade.netPnl));
  assert.equal(filterTrades(snapshot.trades, "no match", "all", "newest").length, 0);
  assert.equal(filterTrades(snapshot.trades, "", "flat", "newest").length, 1);
  assert.deepEqual(snapshot.trades.map(trade => trade.id), ids);
});

test("monthly results reconcile with the total and the final curve value", () => {
  const snapshot = validateSnapshot(data);
  for (const period of ["all", "90", "30"]) {
    const summary = summarize(selectPeriod(snapshot, period));
    const monthlyTotal = summary.months.reduce((sum, month) => sum + month.pnl, 0);
    assert.ok(Math.abs(monthlyTotal - summary.pnl) < 0.000001);
    assert.equal(summary.curve.at(-1).pnl, summary.pnl);
  }
});

test("generated market snapshot passes the unchanged public contract and reconciles", () => {
  const snapshot = validateSnapshot(require("../demo-data.js"));
  const summary = summarize(snapshot.trades);
  assert.equal(summary.count, snapshot.trades.length);
  assert.equal(summary.wins + summary.losses + summary.flat, summary.count);
  assert.ok(Number.isFinite(summary.pnl));
  assert.equal(summary.curve.at(-1).pnl, summary.pnl);
  assert.ok(snapshot.trades.every(trade => trade.status === "CLOSED"));
  assert.ok(snapshot.trades.every(trade => typeof trade.sector === "string" && trade.sector.length > 0));
});

test("browser export identifies historical prices without adding fields to the trade schema", () => {
  const fs = require("node:fs");
  const vm = require("node:vm");
  const sandbox = {};
  vm.runInNewContext(fs.readFileSync(require.resolve("../demo-data.js"), "utf8"), sandbox);
  validateSnapshot(sandbox.ALPHA_DEMO_DATA);
  assert.equal(sandbox.ALPHA_REPORT_INFO.kind, "historical_backtest");
  assert.equal(sandbox.ALPHA_REPORT_INFO.source, "Yahoo Finance via yfinance");
  assert.equal(sandbox.ALPHA_REPORT_INFO.trainingBars, 60);
  assert.ok(["kalman", "ols"].includes(sandbox.ALPHA_REPORT_INFO.model));
  assert.equal(sandbox.ALPHA_REPORT_INFO.riskManagement.max_holding_sessions, 45);
  assert.equal(sandbox.ALPHA_REPORT_INFO.riskManagement.stop_z, 3.5);
  assert.equal(sandbox.ALPHA_REPORT_INFO.entryFilters.max_hurst, 0.45);
  assert.equal(sandbox.ALPHA_REPORT_INFO.entryFilters.early_profit_z, 0.5);
  assert.ok(sandbox.ALPHA_DEMO_DATA.trades.every(trade => Date.parse(trade.entryTime) >= Date.parse(sandbox.ALPHA_REPORT_INFO.evaluationStart)));
  assert.equal(sandbox.ALPHA_REPORT_INFO.screening, undefined);
  assert.ok(["quarterly_walk_forward_sector_screen", "manual"].includes(sandbox.ALPHA_REPORT_INFO.selection));
  if (sandbox.ALPHA_REPORT_INFO.selection === "quarterly_walk_forward_sector_screen") {
    assert.equal(sandbox.ALPHA_REPORT_INFO.walkForward.trainingSessions, 252);
    assert.equal(sandbox.ALPHA_REPORT_INFO.walkForward.rebalanceSessions, 63);
    assert.deepEqual(Object.keys(sandbox.ALPHA_REPORT_INFO.walkForward).sort(), ["rebalanceSessions", "removedPairPolicy", "trainingSessions"]);
  }
  assert.equal(sandbox.ALPHA_REPORT_INFO.rebalances, undefined);
  assert.equal(sandbox.ALPHA_REPORT_INFO.pairs, undefined);
  assert.equal(sandbox.ALPHA_REPORT_INFO.positionSizing.method, "inverse_residual_volatility");
  assert.equal(sandbox.ALPHA_REPORT_INFO.positionSizing.target_spread_risk, 100);
  assert.equal(sandbox.ALPHA_REPORT_INFO.positionSizing.min_gross_notional, 10000);
  assert.equal(sandbox.ALPHA_REPORT_INFO.positionSizing.max_gross_notional, 25000);
  assert.equal(sandbox.ALPHA_REPORT_INFO.positionSizing.volatilityBars, 60);
  assert.equal(sandbox.ALPHA_REPORT_INFO.execution.gross_notional, undefined);
  assert.ok(sandbox.ALPHA_REPORT_INFO.portfolioConfig.initial_equity > 0);
  assert.ok(Number.isInteger(sandbox.ALPHA_REPORT_INFO.portfolioConfig.max_concurrent_pairs));
  assert.equal(sandbox.ALPHA_REPORT_INFO.portfolioConfig.max_ticker_fraction, 0.35);
  assert.equal(sandbox.ALPHA_REPORT_INFO.portfolioConfig.max_sector_fraction, 0.40);
  assert.ok(Number.isFinite(sandbox.ALPHA_REPORT_INFO.portfolioConfig.risk_free_rate));
  assert.ok(sandbox.ALPHA_REPORT_INFO.portfolioConfig.risk_free_rate >= 0);
  for (const key of ["portfolio", "equityCurve", "events", "peakConcurrentPositions", "finalEquity", "availableCash", "openPositions", "portfolioPnl", "portfolioReturnPct", "maxDrawdown", "cashYield", "grossExposure", "sharpeRatio", "sortinoRatio", "calmarRatio", "averageCapitalUtilizationPct", "comparison"]) {
    assert.equal(sandbox.ALPHA_REPORT_INFO[key], undefined);
    assert.equal(sandbox.ALPHA_REPORT_INFO.portfolioConfig[key], undefined);
  }
  assert.deepEqual(Object.keys(sandbox.ALPHA_REPORT_INFO.portfolioConfig).sort(), [
    "cashAccounting", "entryPriority", "exposureBasis", "initial_equity", "insufficientCashPolicy",
    "marginPolicy", "max_concurrent_pairs", "max_sector_fraction", "max_ticker_fraction", "risk_free_rate"
  ]);
  assert.ok(sandbox.ALPHA_REPORT_INFO.execution.commission_bps >= 0);
  assert.equal(sandbox.ALPHA_REPORT_INFO.openPositionCount, undefined);
  assert.equal(sandbox.ALPHA_REPORT_INFO.signals, undefined);
});
