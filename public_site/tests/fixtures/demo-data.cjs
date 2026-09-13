"use strict";
// Stable synthetic unit fixtures, independent of the generated market-data snapshot.
module.exports = {
  mode: "demo", asOf: "2026-09-09T00:00:00Z", currency: "USD",
  trades: [
    ["AAPL / MSFT", "2026-01-09", -100, "Technology / semiconductors"],
    ["AAPL / MSFT", "2026-02-12", 300, "Technology / semiconductors"],
    ["KO / PEP", "2026-03-12", -50, "Consumer staples"],
    ["KO / PEP", "2026-04-15", 0, "Consumer staples"],
    ["AAPL / MSFT", "2026-08-15", 200, "Technology / semiconductors"],
    ["KO / PEP", "2026-09-08", 100, "Consumer staples"]
  ].map(([pair, exit, netPnl, sector], i) => ({ id: "DEMO-" + String(i + 1).padStart(3, "0"), pair, status: "CLOSED", entryTime: "2026-01-05T14:00:00Z", exitTime: exit + "T18:00:00Z", netPnl, sector }))
};
