"""Compare archived identical-input OLS/Kalman runs and publish closed trades only."""

import json
import math

from research.run_backtest import ROOT, publish_snapshot


SELECTION_RULE = "Higher finite daily excess-return Sharpe; then lower portfolio drawdown; then OLS. Undefined Sharpe ranks last. Retrospective research selection, not held-out validation."


def compare_results(results):
    if set(results) != {"ols", "kalman"}:
        raise ValueError("Comparison requires exactly OLS and Kalman archives")
    common = ("requestedStart", "requestedEndExclusive", "firstSession", "lastSession", "evaluationStart",
              "dataSha256", "sourceSha256", "packages", "execution", "positionSizing", "portfolioConfig",
              "riskManagement", "entryFilters", "selection", "walkForward", "screeningConfig", "screening", "pairs", "providerSymbolAliases")
    first = results["ols"]["audit"]
    for name, bundle in results.items():
        audit = bundle["audit"]
        if any(key not in audit for key in common if key not in ("walkForward", "screeningConfig", "screening")):
            raise ValueError("Archive is missing comparison inputs")
        if any(audit.get(key) != value for key, value in bundle["metadata"].items()):
            raise ValueError("Public method metadata does not match the audited run")
        if audit["model"] != name or bundle["metadata"]["model"] != name:
            raise ValueError("Archive model mismatch")
        for key in common:
            if audit.get(key) != first.get(key):
                raise ValueError(f"Incomparable model inputs: {key}")
        if audit["portfolio"] != bundle["portfolioReport"]["summary"]:
            raise ValueError("Archive summary mismatch")
        if len(bundle["snapshot"]["trades"]) != audit["portfolio"]["closedTrades"]:
            raise ValueError("Archive closed-trade count mismatch")
        for key in ("maxDrawdownPct", "portfolioPnl"):
            if not math.isfinite(audit["portfolio"][key]):
                raise ValueError("Invalid portfolio ranking metric")
        sharpe = audit["portfolio"]["sharpeRatio"]
        if sharpe is not None and not math.isfinite(sharpe):
            raise ValueError("Invalid Sharpe ratio")

    def ranking(name):
        summary = results[name]["audit"]["portfolio"]
        sharpe = summary["sharpeRatio"]
        return (sharpe is not None, sharpe if sharpe is not None else 0,
                -summary["maxDrawdownPct"], name == "ols")

    winner = max(results, key=ranking)
    return {
        "winner": winner, "selectionRule": SELECTION_RULE,
        "requestedStart": first["requestedStart"], "requestedEndExclusive": first["requestedEndExclusive"],
        "evaluationStart": first["evaluationStart"], "lastSession": first["lastSession"],
        "dataSha256": first["dataSha256"],
        "models": {name: bundle["audit"]["portfolio"] for name, bundle in results.items()},
        "screening": first.get("screening"),
    }


def main():
    directory = ROOT / "research" / "artifacts" / "pair_backtest"
    results = {name: json.loads((directory / name / "result.json").read_text(encoding="utf-8")) for name in ("ols", "kalman")}
    report = compare_results(results)
    selected = results[report["winner"]]
    publish_snapshot(selected["snapshot"], selected["metadata"], ROOT / "public_site" / "demo-data.js")
    (directory / "comparison.json").write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key not in ("screening", "dataSha256")}, allow_nan=False))


if __name__ == "__main__":
    main()
