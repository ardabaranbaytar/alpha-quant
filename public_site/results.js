(function (root) {
  "use strict";
  const fields = ["id", "pair", "status", "entryTime", "exitTime", "netPnl", "sector"];
  // Same ten research groups as research/pair_screener.py:SECTOR_UNIVERSE, plus the
  // fallback label for any pair outside that preset universe. A closed allowlist, not
  // a free-text field: sector is public industry classification, never a signal value.
  const SECTORS = new Set([
    "Financials", "Energy", "Consumer staples", "Healthcare", "Industrials / defense",
    "Technology / semiconductors", "Insurance / specialized finance", "Communication / media",
    "Real estate / infrastructure", "Utilities", "Unclassified",
  ]);
  const isTime = value => typeof value === "string" && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/.test(value) && Number.isFinite(Date.parse(value)) && new Date(value).toISOString().replace(".000Z", "Z") === value;

  function validateSnapshot(source) {
    // Fail closed: a public snapshot must already exclude private records and fields.
    if (!source || Object.keys(source).some(key => !["mode", "asOf", "currency", "trades"].includes(key)) || source.mode !== "demo" || source.currency !== "USD" || !isTime(source.asOf) || !Array.isArray(source.trades)) throw new Error("Invalid public snapshot");
    const ids = new Set();
    const trades = source.trades.map(trade => {
      if (!trade || Object.keys(trade).length !== fields.length || fields.some(key => !Object.hasOwn(trade, key))) throw new Error("Unexpected trade fields");
      if (typeof trade.id !== "string" || !/^DEMO-\d{3}$/.test(trade.id) || ids.has(trade.id)) throw new Error("Invalid trade identity");
      if (trade.status !== "CLOSED" || !isTime(trade.entryTime) || !isTime(trade.exitTime) || Date.parse(trade.exitTime) < Date.parse(trade.entryTime) || Date.parse(trade.exitTime) > Date.parse(source.asOf)) throw new Error("Trade is not a verified closed result");
      if (typeof trade.pair !== "string" || !/^[A-Z]{1,6} \/ [A-Z]{1,6}$/.test(trade.pair) || typeof trade.netPnl !== "number" || !Number.isFinite(trade.netPnl)) throw new Error("Invalid trade result");
      if (typeof trade.sector !== "string" || !SECTORS.has(trade.sector)) throw new Error("Invalid or unknown sector classification");
      ids.add(trade.id);
      return Object.freeze(Object.fromEntries(fields.map(key => [key, trade[key]])));
    });
    trades.sort((a, b) => a.exitTime.localeCompare(b.exitTime) || a.id.localeCompare(b.id));
    return Object.freeze({ mode: source.mode, asOf: source.asOf, currency: source.currency, trades: Object.freeze(trades) });
  }

  function selectPeriod(snapshot, period) {
    if (!["all", "90", "30"].includes(period)) throw new Error("Invalid period");
    const end = Date.parse(snapshot.asOf);
    const start = period === "all" ? -Infinity : end - Number(period) * 86400000;
    return snapshot.trades.filter(trade => Date.parse(trade.exitTime) > start && Date.parse(trade.exitTime) <= end);
  }

  function summarize(trades) {
    let pnl = 0, peak = 0, drawdown = 0, profit = 0, loss = 0, wins = 0, losses = 0, holdMs = 0;
    const ordered = [...trades].sort((a, b) => a.exitTime.localeCompare(b.exitTime) || a.id.localeCompare(b.id));
    const curve = [{ time: null, pnl: 0 }];
    const months = new Map();
    for (const trade of ordered) {
      pnl += trade.netPnl;
      if (trade.netPnl > 0) { profit += trade.netPnl; wins++; }
      if (trade.netPnl < 0) { loss -= trade.netPnl; losses++; }
      peak = Math.max(peak, pnl);
      drawdown = Math.max(drawdown, peak - pnl);
      holdMs += Date.parse(trade.exitTime) - Date.parse(trade.entryTime);
      curve.push({ time: trade.exitTime, pnl });
      const month = trade.exitTime.slice(0, 7);
      months.set(month, (months.get(month) || 0) + trade.netPnl);
    }
    // Closed-trade tearsheet stats: computed only from this public net-P&L sample (N = ordered.length),
    // not from the private daily mark-to-market equity curve. Deliberately not labeled with the
    // standard annualized-NAV Sharpe/Sortino/Calmar formulas, so the methodology difference is explicit.
    const mean = ordered.length ? pnl / ordered.length : null;
    const variance = ordered.length > 1 ? ordered.reduce((sum, t) => sum + (t.netPnl - mean) ** 2, 0) / (ordered.length - 1) : 0;
    const sampleStdDev = Math.sqrt(variance);
    const closedTradeSharpe = mean !== null && sampleStdDev > 0 ? mean / sampleStdDev : null;
    const downsideVariance = ordered.length ? ordered.reduce((sum, t) => sum + Math.min(t.netPnl, 0) ** 2, 0) / ordered.length : 0;
    const downsideDeviation = Math.sqrt(downsideVariance);
    const closedTradeSortino = mean !== null && downsideDeviation > 0 ? mean / downsideDeviation : null;
    const profitToDrawdown = drawdown > 0 ? pnl / drawdown : null;
    const averageWin = wins ? profit / wins : null, averageLoss = losses ? -loss / losses : null;
    const riskRewardRatio = averageWin !== null && averageLoss ? averageWin / Math.abs(averageLoss) : null;
    return { count: ordered.length, pnl, wins, losses, flat: ordered.length - wins - losses, winRate: ordered.length ? wins / ordered.length * 100 : null, profitFactor: loss ? profit / loss : null, drawdown, averageWin, averageLoss, averageHoldMs: ordered.length ? holdMs / ordered.length : null, best: ordered.length ? Math.max(...ordered.map(t => t.netPnl)) : null, worst: ordered.length ? Math.min(...ordered.map(t => t.netPnl)) : null, curve, months: [...months].map(([month, value]) => ({ month, pnl: value })), closedTradeSharpe, closedTradeSortino, profitToDrawdown, riskRewardRatio };
  }

  function filterTrades(trades, query, outcome, sort) {
    const term = query.trim().toUpperCase();
    const filtered = trades.filter(trade => trade.pair.includes(term) && (outcome === "all" || outcome === "win" && trade.netPnl > 0 || outcome === "loss" && trade.netPnl < 0 || outcome === "flat" && trade.netPnl === 0));
    const holdMs = trade => Date.parse(trade.exitTime) - Date.parse(trade.entryTime);
    const compare = {
      newest: (a, b) => b.exitTime.localeCompare(a.exitTime), oldest: (a, b) => a.exitTime.localeCompare(b.exitTime),
      best: (a, b) => b.netPnl - a.netPnl, worst: (a, b) => a.netPnl - b.netPnl,
      sector: (a, b) => a.sector.localeCompare(b.sector) || b.exitTime.localeCompare(a.exitTime),
      longest: (a, b) => holdMs(b) - holdMs(a), shortest: (a, b) => holdMs(a) - holdMs(b),
    }[sort];
    return filtered.sort(compare || ((a, b) => b.exitTime.localeCompare(a.exitTime)));
  }

  const api = { validateSnapshot, selectPeriod, summarize, filterTrades };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.AlphaResults = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
