"""Run a shared-capital quarterly walk-forward multi-pair portfolio backtest.

Usage: python -m research.run_backtest
No bot, database settings, or environment files are imported.

Canonical threshold values for this pipeline (single source of truth per value,
consolidated here to prevent drift across strategies/research modules):
  entry_z=2.0           strategies/pair_trading.py:PairTradingConfig,
                         strategies/kalman_pair.py:KalmanPairConfig
  stop_z=3.5             RiskConfig below (time/stop exit shared by both models)
  max_holding_sessions=45  RiskConfig below
  early_profit_z=0.5     strategies/mean_reversion.py:EntryFilterConfig (cost-aware:
                         also requires a positive estimated net PnL, not a bare z-gate)
  max_pvalue=0.05        strategies/pair_trading.py:PairTradingConfig,
                         research/pair_screener.py:ScreenerConfig
  max_hurst=0.45         research/pair_screener.py:ScreenerConfig,
                         strategies/mean_reversion.py:EntryFilterConfig
This pipeline never imports strategies/pairs_trading.py or backtesting/pairs_backtester.py
(the separate DB-backed live-scanner stack, z_entry=1.5/z_exit=0.5/window=300) — those
thresholds are intentionally independent and out of scope for this module.
"""

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from datetime import date
from importlib.metadata import version
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))

from research.pair_accounting import execution_time, liquidation_pnl
from research.pair_artifacts import load_available_history, load_history, save_history
from research.pair_screener import (
    YAHOO_SYMBOL_ALIASES,
    ScreenerConfig,
    download_research_history,
    download_universe,
    symbol_sectors,
    universe_symbols,
    walk_forward_screen,
)
from research.portfolio import PortfolioConfig, PortfolioEngine
from strategies.kalman_pair import KalmanPairConfig, KalmanPairStrategy
from strategies.mean_reversion import EntryFilterConfig, estimate_hurst
from strategies.pair_trading import PairTradingConfig, PairTradingStrategy

DEFAULT_PAIRS = ("AAPL/MSFT", "XOM/CVX", "JPM/BAC", "V/MA", "GOOGL/META", "KO/PEP", "NVDA/AMD")


@dataclass(frozen=True)
class RiskConfig:
    max_holding_sessions: int = 45
    stop_z: float = 3.5

    def __post_init__(self):
        if type(self.max_holding_sessions) is not int or self.max_holding_sessions < 1:
            raise ValueError("Holding limit must be a positive integer session count")
        if not np.isfinite(self.stop_z) or self.stop_z <= 0:
            raise ValueError("Stop z-score must be finite and positive")

    def should_close(self, z_score: float, held_sessions: int) -> bool:
        return abs(z_score) > self.stop_z or held_sessions >= self.max_holding_sessions


@dataclass(frozen=True)
class ExecutionConfig:
    gross_notional: float = 10000.0
    commission_bps: float = 10.0
    slippage_bps: float = 5.0
    annual_borrow_rate: float = 0.03

    def __post_init__(self):
        values = [self.gross_notional, self.commission_bps, self.slippage_bps, self.annual_borrow_rate]
        if not np.isfinite(values).all() or self.gross_notional <= 0 or min(values[1:]) < 0:
            raise ValueError("Invalid execution costs or gross notional")
        if self.slippage_bps >= 10000:
            raise ValueError("Slippage must be less than 100 percent")


@dataclass(frozen=True)
class VolatilitySizingConfig:
    target_spread_risk: float = 100.0
    min_gross_notional: float = 10000.0
    max_gross_notional: float = 25000.0

    def __post_init__(self):
        values = [self.target_spread_risk, self.min_gross_notional, self.max_gross_notional]
        if not np.isfinite(values).all() or min(values) <= 0 or self.min_gross_notional > self.max_gross_notional:
            raise ValueError("Sizing requires finite positive risk and ordered positive notional bounds")

    def units(self, residual_std, entry_prices, beta) -> float | None:
        """One spread unit holds one A share against beta B shares; sigma is USD/unit."""
        if residual_std is None or not np.isfinite(residual_std) or residual_std < 1e-8:
            return None
        prices = np.asarray(entry_prices, dtype=float)
        if prices.shape != (2,) or not np.isfinite(prices).all() or (prices <= 0).any() or not np.isfinite(beta) or beta <= 0:
            raise ValueError("Sizing requires two positive execution prices and a positive beta")
        unit_gross = float(prices[0] + beta * prices[1])
        if not np.isfinite(unit_gross):
            raise ValueError("Non-finite gross exposure per spread unit")
        # Normalize dollar residual volatility by gross dollars per hedged unit.
        raw_notional = self.target_spread_risk * (unit_gross / residual_std)
        notional = float(np.clip(raw_notional, self.min_gross_notional, self.max_gross_notional))
        return notional / unit_gross


def simulate_pair(pair, history, strategy=None, costs=None, risk=None, entry_filters=None,
                  trade_start=None, entry_schedule=None, sizing=None):
    """Legacy isolated reference for regression tests; the CLI uses PortfolioEngine."""
    strategy = strategy or PairTradingStrategy()
    costs = costs or ExecutionConfig()
    risk = risk or RiskConfig()
    a, b = pair
    bars = pd.concat({"a": history[a], "b": history[b]}, axis=1, join="inner").dropna().sort_index()
    if bars.index.has_duplicates or not isinstance(bars.index, pd.DatetimeIndex):
        raise ValueError("Expected unique daily timestamps")
    prices = bars.loc[:, [("a", "Open"), ("a", "Close"), ("b", "Open"), ("b", "Close")]].to_numpy(dtype=float)
    if not np.isfinite(prices).all() or (prices <= 0).any():
        raise ValueError("Invalid OHLC prices")
    dynamic_model = isinstance(strategy, KalmanPairStrategy)
    warmup = strategy.config.warmup_sessions if dynamic_model else strategy.config.window
    if sizing is not None and dynamic_model:
        warmup = max(warmup, strategy.config.warmup_sessions + strategy.config.burn_in)
    if entry_filters is not None:
        warmup = max(warmup, entry_filters.hurst_window + (strategy.config.burn_in if dynamic_model else 0))
    if len(bars) <= warmup + 1:
        raise ValueError(f"Insufficient synchronized history for {a}/{b}")
    observations = strategy.analyze(bars[("a", "Close")], bars[("b", "Close")]) if dynamic_model else None
    dynamic_residuals = np.array([observation.residual for observation in observations]) if dynamic_model else None
    trade_start = pd.Timestamp(trade_start) if trade_start is not None else bars.index[0]
    if entry_schedule is None:
        entry_allowed = np.ones(len(bars), dtype=bool)
    else:
        if (not isinstance(entry_schedule.index, pd.DatetimeIndex) or entry_schedule.index.has_duplicates
                or entry_schedule.index.hasnans or entry_schedule.index.tz is not None
                or not entry_schedule.index.equals(entry_schedule.index.normalize())
                or not entry_schedule.index.is_monotonic_increasing or entry_schedule.dtype != bool):
            raise ValueError("Entry schedule must be a sorted, unique boolean session series")
        # Only the most recent effective selection applies, never a future selection.
        entry_allowed = entry_schedule.reindex(bars.index, method="ffill", fill_value=False).to_numpy(dtype=bool)
    position = None
    pending = None
    closed = []
    commission = costs.commission_bps / 10000
    slippage = costs.slippage_bps / 10000
    for i in range(warmup, len(bars)):
        today = bars.iloc[i]
        market = np.array([today[("a", "Open")], today[("b", "Open")]], dtype=float)
        timestamp = execution_time(bars.index[i])
        if pending is not None:
            action, model, residual_std = pending
            pending = None
            if action == "CLOSE":
                net = liquidation_pnl(position, market, timestamp, costs)
                closed.append({"pair": f"{a} / {b}", "status": "CLOSED", "entryTime": position["entryTime"], "exitTime": timestamp, "netPnl": round(net, 2)})
                position = None
            elif entry_allowed[i] and bars.index[i] >= trade_start:
                direction = 1 if action == "LONG_SPREAD" else -1
                entry_prices = market * (1 + direction * np.array([1, -1]) * slippage)
                units = (sizing.units(residual_std, entry_prices, model.beta) if sizing is not None
                         else costs.gross_notional / (market[0] + model.beta * market[1]))
                if units is not None:
                    quantities = direction * np.array([units, -model.beta * units])
                    position = {"direction": action, "model": model, "quantities": quantities, "prices": entry_prices, "entryTime": timestamp, "entry_index": i, "entry_fee": float(np.dot(np.abs(quantities), entry_prices) * commission), "short_notional": float(np.dot(np.maximum(-quantities, 0), entry_prices))}
        close_a, close_b = today[("a", "Close")], today[("b", "Close")]
        if position is not None:
            z = observations[i].z if dynamic_model else position["model"].z_score(close_a, close_b)
            # Entry day counts as session one; every exit still fills next open.
            held_sessions = i - position["entry_index"] + 1
            take_profit = False
            if entry_filters is not None and z is not None and abs(z) <= entry_filters.early_profit_z:
                # Daily-close estimate only. The next open can still turn a planned profit into a loss.
                close_time = (bars.index[i] + pd.Timedelta(hours=16)).tz_localize("America/New_York").tz_convert("UTC")
                estimated_net = liquidation_pnl(position, [close_a, close_b], close_time, costs)
                take_profit = entry_filters.take_profit(z, estimated_net)
            if (held_sessions >= risk.max_holding_sessions
                    or take_profit
                    or z is not None and (strategy.should_close(position["direction"], z) or risk.should_close(z, held_sessions))):
                pending = ("CLOSE", None, None)
        else:
            if bars.index[i] < trade_start or not entry_allowed[i]:
                continue
            if dynamic_model:
                model = observations[i]
                signal = strategy.entry_signal(model)
            else:
                # The current close is excluded from OLS estimation; fills occur next session.
                training = bars.iloc[i - strategy.config.window:i]
                model = strategy.fit(training[("a", "Close")], training[("b", "Close")])
                signal = strategy.entry_signal(model, close_a, close_b) if model is not None else None
            if signal:
                if entry_filters is not None:
                    if dynamic_model:
                        residuals = dynamic_residuals[i - entry_filters.hurst_window:i]
                    else:
                        prior = bars.iloc[i - entry_filters.hurst_window:i]
                        residuals = prior[("a", "Close")] - model.alpha - model.beta * prior[("b", "Close")]
                    if not entry_filters.permits_entry(estimate_hurst(residuals)):
                        continue
                residual_std = None
                if sizing is not None:
                    if dynamic_model:
                        # Innovations are normalized by A's first close; convert back to USD/unit.
                        prior = dynamic_residuals[i - strategy.config.warmup_sessions:i]
                        residual_std = float(np.std(prior, ddof=1) * bars[("a", "Close")].iloc[0])
                    else:
                        residual_std = model.std
                    if not np.isfinite(residual_std) or residual_std < 1e-8:
                        continue
                # Freeze the past-only volatility estimate with the entry order until its next-open fill.
                pending = (signal, model, residual_std)
    # No forced liquidation: still-open positions and unfilled orders never enter the export.
    return closed, bars.index[-1]


def publish_snapshot(snapshot, metadata, output):
    output = Path(output).resolve()
    if output != ROOT / "public_site" / "demo-data.js":
        raise ValueError("Public output must be this copy's public_site/demo-data.js")
    serialized = json.dumps(snapshot, allow_nan=False, ensure_ascii=True, indent=2)
    # Reuse the exact browser contract before replacing the last valid public snapshot.
    subprocess.run(
        ["node", "-e", "const fs=require('node:fs');require('./public_site/results.js').validateSnapshot(JSON.parse(fs.readFileSync(0,'utf8')));"],
        input=serialized, text=True, cwd=ROOT, check=True, capture_output=True,
    )
    details = json.dumps(metadata, allow_nan=False, ensure_ascii=True, indent=2)
    content = '(function (root) {\n  "use strict";\n  // Generated historical backtest. Legacy demo schema; these are not fictional prices.\n  const data = ' + serialized + ';\n  const reportInfo = ' + details + ';\n  if (typeof module !== "undefined" && module.exports) module.exports = data;\n  else { root.ALPHA_DEMO_DATA = data; root.ALPHA_REPORT_INFO = reportInfo; }\n})(typeof globalThis !== "undefined" ? globalThis : this);\n'
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=output.parent, suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(content)
        temporary.replace(output)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--end", type=date.fromisoformat, default=pd.Timestamp.now(tz="UTC").date(), help="Exclusive end date; defaults to today UTC")
    parser.add_argument("--pairs", nargs="+", default=None, help="Manual pairs; otherwise rescreen sectors every 63 sessions")
    parser.add_argument("--years", type=int, default=5, help="Calendar years of history (default: 5)")
    parser.add_argument("--model", choices=("kalman", "ols"), default="ols", help="Signal model (default: ols)")
    parser.add_argument("--target-spread-risk", type=float, default=100.0, help="USD exposure to one residual standard deviation before notional bounds (default: 100)")
    parser.add_argument("--initial-equity", type=float, default=100000.0, help="Shared starting portfolio equity in USD (default: 100000)")
    parser.add_argument("--max-concurrent-pairs", type=int, default=8, help="Maximum simultaneous pair positions (default: 8)")
    parser.add_argument("--max-ticker-fraction", type=float, default=0.35, help="Single-ticker gross cap as fraction of marked equity (default: 0.35)")
    parser.add_argument("--max-sector-fraction", type=float, default=0.40, help="Same-sector gross cap as fraction of marked equity (default: 0.40)")
    parser.add_argument("--risk-free-rate", type=float, default=0.045, help="Annual ACT/365 yield on positive unreserved cash (default: 0.045)")
    parser.add_argument("--reuse-data", action="store_true", help="Use hash-verified saved prices for identical-input model comparison; no download")
    parser.add_argument("--no-publish", action="store_true", help="Archive private results without replacing the public snapshot")
    args = parser.parse_args(argv)
    try:
        sizing = VolatilitySizingConfig(target_spread_risk=args.target_spread_risk)
        portfolio_config = PortfolioConfig(args.initial_equity, args.max_concurrent_pairs, args.max_ticker_fraction,
                                           args.risk_free_rate, args.max_sector_fraction)
    except ValueError as exc:
        parser.error(str(exc))
    if args.years < 1:
        parser.error("Years must be a positive integer")
    if args.pairs is None and args.years < 2:
        parser.error("Automatic screening requires one training year plus evaluation data")
    pairs = []
    for value in args.pairs or []:
        if not re.fullmatch(r"[A-Z]{1,6}/[A-Z]{1,6}", value):
            parser.error("Pairs must use TICKER/TICKER")
        pair = tuple(value.split("/"))
        if pair[0] == pair[1] or any(set(pair) == set(existing) for existing in pairs):
            parser.error("Pairs must contain distinct symbols and cannot repeat")
        pairs.append(pair)
    end = pd.Timestamp(args.end)
    if end > pd.Timestamp.now(tz="UTC").tz_localize(None).normalize():
        parser.error("End cannot be in the future")
    start = end - pd.DateOffset(years=args.years)
    artifact_dir = ROOT / "research" / "artifacts" / "pair_backtest"
    screening = None
    screening_config = ScreenerConfig()
    evaluation_start = start
    symbols = universe_symbols() if args.pairs is None else list(dict.fromkeys(symbol for pair in pairs for symbol in pair))
    start_text, end_text = start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
    if args.reuse_data:
        # Offline runs use the requested CSVs directly and record the common
        # observed range rather than rejecting a valid, slightly older snapshot.
        history, hashes, start, end = load_available_history(artifact_dir, symbols)
    else:
        history = (download_universe(start_text, end_text, artifact_dir / "yfinance_cache") if args.pairs is None
                   else download_research_history(symbols, start_text, end_text, artifact_dir / "yfinance_cache"))
        symbols = list(history)
        if not symbols:
            raise RuntimeError("No Yahoo Finance history returned for the requested universe")
        if args.pairs is not None:
            unavailable_pairs = [pair for pair in pairs if any(symbol not in history for symbol in pair)]
            for pair in unavailable_pairs:
                print(f"Skipping manual pair with unavailable history: {' / '.join(pair)}", file=sys.stderr)
            pairs = [pair for pair in pairs if pair not in unavailable_pairs]
            if not pairs:
                raise RuntimeError("No manual pairs have complete Yahoo Finance history")
        save_history(history, artifact_dir, start_text, end_text)
        # Fresh downloads are reloaded to verify their exact serialized form.
        history, hashes = load_history(artifact_dir, symbols, start_text, end_text)
    evaluation_start = start
    if args.pairs is None:
        screening = walk_forward_screen(history, start, end, screening_config)
        evaluation_start = pd.Timestamp(screening.rebalances[0].effective_session)
        pairs = list(screening.pairs)
        print(json.dumps({"screening": {
            "rebalances": len(screening.rebalances),
            "candidateEvaluations": screening.to_report()["candidate_evaluations"],
            "uniqueSelectedPairs": len(screening.pairs),
        }}), flush=True)
    strategy_type = KalmanPairStrategy if args.model == "kalman" else PairTradingStrategy
    config = KalmanPairConfig() if args.model == "kalman" else PairTradingConfig()
    warmup = config.warmup_sessions if args.model == "kalman" else config.window
    costs, risk, entry_filters = ExecutionConfig(), RiskConfig(), EntryFilterConfig()
    schedules = {pair: screening.entry_schedule(pair) for pair in pairs} if screening is not None else None
    engine = PortfolioEngine(
        history, {pair: strategy_type(config) for pair in pairs}, costs, risk, sizing,
        portfolio_config, entry_filters, evaluation_start, schedules, sector_of=symbol_sectors(),
    )
    portfolio_result = engine.run()
    trades = portfolio_result.trades
    last_sessions = [context.bars.index[-1] for context in engine.contexts.values()]
    if not last_sessions:
        last_sessions = [bars.index[-1] for bars in history.values()]
    if len(set(last_sessions)) != 1:
        raise ValueError("Pairs have different final data sessions; refusing a partial snapshot")
    last_session = last_sessions[0]
    if last_session != portfolio_result.last_session:
        raise ValueError("Portfolio and pair calendars have different final sessions")
    if (end - last_session).days > 7:
        raise ValueError("Yahoo data is stale relative to the requested end date")
    as_of = (last_session + pd.Timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    trades.sort(key=lambda trade: (trade["exitTime"], trade["pair"], trade["entryTime"]))
    if len(trades) > 999:
        raise ValueError("Result exceeds the legacy three-digit ID schema")
    sectors = symbol_sectors()
    # Every screened pair is same-sector by construction; take the first leg's sector.
    trades = [{**trade, "sector": sectors.get(trade["pair"].split(" / ")[0], "Unclassified")} for trade in trades]
    trades = [{"id": f"DEMO-{i:03d}", **trade} for i, trade in enumerate(trades, 1)]
    snapshot = {"mode": "demo", "asOf": as_of, "currency": "USD", "trades": trades}
    metadata = {
        "kind": "historical_backtest", "source": "Yahoo Finance via yfinance",
        "requestedStart": start.strftime("%Y-%m-%d"), "requestedEndExclusive": end.strftime("%Y-%m-%d"),
        "firstSession": min(bars.index[0] for bars in history.values()).strftime("%Y-%m-%d"),
        "lastSession": last_session.strftime("%Y-%m-%d"),
        "generatedAt": pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%dT%H:%M:%SZ"),
        "execution": {key: value for key, value in asdict(costs).items() if key != "gross_notional"},
        "positionSizing": {"method": "inverse_residual_volatility", **asdict(sizing),
                           "volatilityBars": warmup, "notionalBasis": "Entry execution prices including slippage"},
        "trainingBars": warmup, "model": args.model,
        "portfolioConfig": {**asdict(portfolio_config), "marginPolicy": "100% entry gross reserved; no short-proceeds reuse",
                            "cashAccounting": "Open/close variation settlement; ACT/365 yield on time-held positive unreserved cash, credited at close; accrued borrow",
                            "entryPriority": "Oldest signal, then alphabetical pair; exits first",
                            "exposureBasis": "Gross legs without netting; post-entry marked equity",
                            "insufficientCashPolicy": "Resize to available cash including entry costs; reject below minimum"},
        "riskManagement": asdict(risk),
        "entryFilters": asdict(entry_filters),
        "evaluationStart": evaluation_start.strftime("%Y-%m-%d"),
        "selection": "quarterly_walk_forward_sector_screen" if screening is not None else "manual",
        "priceBasis": "Dividend- and split-adjusted daily OHLC; synthetic adjusted-share accounting",
        "executionTiming": "Signal at close, execution at next synchronized session open (09:30 America/New_York)",
    }
    if screening is not None:
        metadata["walkForward"] = {
            "trainingSessions": screening.training_sessions,
            "rebalanceSessions": screening.rebalance_sessions,
            "removedPairPolicy": "No new entries; existing positions follow normal exits",
        }
    wins = sum(trade["netPnl"] > 0 for trade in trades)
    win_rate = round(100 * wins / len(trades), 2) if trades else None
    closed_equity = np.r_[0.0, np.cumsum([trade["netPnl"] for trade in trades])]
    drawdown = round(float(np.max(np.maximum.accumulate(closed_equity) - closed_equity)), 2)
    audit = {**metadata, "pairs": pairs, "strategy": asdict(config), "dataSha256": hashes, "packages": {name: version(name) for name in ["yfinance", "pandas", "numpy", "statsmodels"]}, "closedTrades": len(trades), "netPnl": round(sum(t["netPnl"] for t in trades), 2), "winRate": win_rate, "maxRealizedDrawdown": drawdown}
    audit["portfolio"] = portfolio_result.summary
    audit["providerSymbolAliases"] = {symbol: YAHOO_SYMBOL_ALIASES[symbol] for symbol in symbols if symbol in YAHOO_SYMBOL_ALIASES}
    source_files = ["research/run_backtest.py", "research/portfolio.py", "research/pair_accounting.py",
                    "research/pair_screener.py", "research/pair_artifacts.py", "research/compare_pair_models.py",
                    "strategies/pair_trading.py", "strategies/kalman_pair.py", "strategies/mean_reversion.py",
                    "data_pipeline/yfinance_fetcher.py"]
    audit["sourceSha256"] = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in source_files}
    if screening is not None:
        audit["screening"] = screening.to_report()
        audit["screeningConfig"] = asdict(screening_config)
        (artifact_dir / "screening-report.json").write_text(json.dumps(audit["screening"], indent=2, allow_nan=False), encoding="utf-8")
    (artifact_dir / "run-summary.json").write_text(json.dumps(audit, indent=2, allow_nan=False), encoding="utf-8")
    portfolio_report = {"summary": portfolio_result.summary, "equityCurve": portfolio_result.equity_curve, "events": portfolio_result.events}
    (artifact_dir / "portfolio-report.json").write_text(json.dumps(portfolio_report, indent=2, allow_nan=False), encoding="utf-8")
    model_dir = artifact_dir / args.model
    model_dir.mkdir(parents=True, exist_ok=True)
    bundle = {"snapshot": snapshot, "metadata": metadata, "audit": audit, "portfolioReport": portfolio_report}
    (model_dir / "result.json").write_text(json.dumps(bundle, indent=2, allow_nan=False), encoding="utf-8")
    if not args.no_publish:
        publish_snapshot(snapshot, metadata, ROOT / "public_site" / "demo-data.js")
    print(json.dumps({"model": args.model, "dataStart": metadata["firstSession"], "evaluationStart": metadata["evaluationStart"], "dataEnd": metadata["lastSession"], "uniqueSelectedPairs": len(pairs), "rebalances": len(screening.rebalances) if screening is not None else 0, "closedTrades": len(trades), "netPnl": audit["netPnl"], "winRate": win_rate, "maxRealizedDrawdown": drawdown, "portfolio": portfolio_result.summary, "export": None if args.no_publish else "public_site/demo-data.js", "archive": str(model_dir.relative_to(ROOT) / "result.json")}))


if __name__ == "__main__":
    main()
