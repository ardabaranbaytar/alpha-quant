"""Chronological, fully collateralized multi-pair research ledger. No DB or bot imports."""

from collections import Counter
from dataclasses import dataclass

import numpy as np
import pandas as pd

from research.pair_accounting import (
    cash_interest,
    close_time,
    execution_time,
    liquidation_pnl,
    portfolio_metrics,
)
from strategies.kalman_pair import KalmanPairStrategy
from strategies.mean_reversion import estimate_hurst


@dataclass(frozen=True)
class PortfolioConfig:
    initial_equity: float = 100000.0
    max_concurrent_pairs: int = 8
    max_ticker_fraction: float = 0.35
    risk_free_rate: float = 0.045
    max_sector_fraction: float = 0.40

    def __post_init__(self):
        if not np.isfinite(self.initial_equity) or self.initial_equity <= 0:
            raise ValueError("Initial equity must be finite and positive")
        if type(self.max_concurrent_pairs) is not int or self.max_concurrent_pairs < 1:
            raise ValueError("Concurrent pair limit must be a positive integer")
        if not np.isfinite(self.max_ticker_fraction) or not 0 < self.max_ticker_fraction <= 1:
            raise ValueError("Ticker exposure fraction must lie in (0, 1]")
        if not np.isfinite(self.risk_free_rate) or self.risk_free_rate < 0:
            raise ValueError("Risk-free rate must be finite and nonnegative")
        if not np.isfinite(self.max_sector_fraction) or not 0 < self.max_sector_fraction <= 1:
            raise ValueError("Sector exposure fraction must lie in (0, 1]")


class PairContext:
    def __init__(self, pair, history, strategy, filters, schedule):
        self.pair, self.strategy, self.filters, self.schedule = pair, strategy, filters, schedule
        self.bars = pd.concat({"a": history[pair[0]], "b": history[pair[1]]}, axis=1, join="inner").dropna().sort_index()
        self.locations = {session: i for i, session in enumerate(self.bars.index)}
        self.dynamic = isinstance(strategy, KalmanPairStrategy)
        self.window = strategy.config.warmup_sessions if self.dynamic else strategy.config.window
        self.warmup = self.window + (strategy.config.burn_in if self.dynamic else 0)
        if filters is not None:
            self.warmup = max(self.warmup, filters.hurst_window + (strategy.config.burn_in if self.dynamic else 0))
        if len(self.bars) <= self.warmup + 1:
            raise ValueError(f"Insufficient synchronized history for {'/'.join(pair)}")
        self.observations = strategy.analyze(self.bars[("a", "Close")], self.bars[("b", "Close")]) if self.dynamic else None
        self.residuals = np.array([o.residual for o in self.observations]) if self.dynamic else None
        if schedule is not None and (
            not isinstance(schedule.index, pd.DatetimeIndex) or schedule.index.has_duplicates
            or schedule.index.hasnans or schedule.index.tz is not None
            or not schedule.index.equals(schedule.index.normalize())
            or not schedule.index.is_monotonic_increasing or schedule.dtype != bool
        ):
            raise ValueError("Entry schedule must be a sorted, unique boolean session series")

    def allows(self, session):
        if self.schedule is None:
            return True
        i = self.schedule.index.searchsorted(session, side="right") - 1
        return i >= 0 and bool(self.schedule.iloc[i])

    def entry(self, session):
        i = self.locations.get(session)
        if i is None or i < self.warmup or not self.allows(session):
            return None
        row = self.bars.iloc[i]
        if self.dynamic:
            model = self.observations[i]
            signal = self.strategy.entry_signal(model)
        else:
            prior = self.bars.iloc[i - self.window:i]
            model = self.strategy.fit(prior[("a", "Close")], prior[("b", "Close")])
            signal = self.strategy.entry_signal(model, row[("a", "Close")], row[("b", "Close")]) if model is not None else None
        if signal is None:
            return None
        if self.filters is not None:
            if self.dynamic:
                residuals = self.residuals[i - self.filters.hurst_window:i]
            else:
                prior = self.bars.iloc[i - self.filters.hurst_window:i]
                residuals = prior[("a", "Close")] - model.alpha - model.beta * prior[("b", "Close")]
            if not self.filters.permits_entry(estimate_hurst(residuals)):
                return None
        sigma = (float(np.std(self.residuals[i - self.window:i], ddof=1) * self.bars[("a", "Close")].iloc[0])
                 if self.dynamic else model.std)
        if not np.isfinite(sigma) or sigma < 1e-8:
            return None
        return {"direction": signal, "model": model, "sigma": sigma, "signal_session": session}

    def should_exit(self, session, position, timestamp, costs, risk):
        i = self.locations.get(session)
        if i is None:
            return False
        row = self.bars.iloc[i]
        market = [row[("a", "Close")], row[("b", "Close")]]
        z = self.observations[i].z if self.dynamic else position["model"].z_score(*market)
        held = i - position["entry_index"] + 1
        early_profit = (self.filters is not None and z is not None and abs(z) <= self.filters.early_profit_z
                        and self.filters.take_profit(z, liquidation_pnl(position, market, timestamp, costs)))
        return (held >= risk.max_holding_sessions or early_profit
                or z is not None and (self.strategy.should_close(position["direction"], z) or risk.should_close(z, held)))


@dataclass
class PortfolioResult:
    trades: list
    last_session: pd.Timestamp
    summary: dict
    equity_curve: list
    events: list


class PortfolioEngine:
    def __init__(self, history, strategies, costs, risk, sizing, config=None,
                 entry_filters=None, trade_start=None, entry_schedules=None, sector_of=None):
        self.config = config or PortfolioConfig()
        self.costs, self.risk, self.sizing = costs, risk, sizing
        if sector_of is not None and not isinstance(sector_of, dict):
            raise ValueError("sector_of must be a mapping of symbol to sector label")
        self.sector_of = sector_of
        self.history = history
        self.calendar = pd.DatetimeIndex([])
        for bars in history.values():
            index = bars.index
            if (not isinstance(index, pd.DatetimeIndex) or index.has_duplicates or index.hasnans
                    or index.tz is not None or not index.equals(index.normalize()) or not index.is_monotonic_increasing):
                raise ValueError("Expected unique sorted timezone-naive session dates")
            prices = bars.loc[:, ["Open", "Close"]].to_numpy(dtype=float)
            if not len(bars) or not np.isfinite(prices).all() or (prices <= 0).any():
                raise ValueError("Expected finite positive observed prices")
            self.calendar = self.calendar.union(index)
        self.calendar = self.calendar.sort_values()
        if trade_start is not None:
            self.calendar = self.calendar[self.calendar >= pd.Timestamp(trade_start)]
        if self.calendar.empty:
            raise ValueError("Portfolio requires evaluation sessions")
        pairs = list(strategies)
        if any(len(p) != 2 or p[0] == p[1] for p in pairs) or len({frozenset(p) for p in pairs}) != len(pairs):
            raise ValueError("Expected distinct non-repeated pairs")
        schedules = entry_schedules or {}
        if entry_schedules is not None and set(schedules) != set(pairs):
            raise ValueError("Every monitored pair must have an explicit entry schedule")
        self.contexts = {p: PairContext(p, history, strategies[p], entry_filters, schedules.get(p)) for p in sorted(pairs)}
        self.cash = self.config.initial_equity
        self.positions, self.pending_entries, self.pending_exits, self.marks = {}, {}, set(), {}
        self.trades, self.events, self.curve = [], [], []
        self.rejected = Counter()
        self.peak_concurrent = 0
        self.realized_pnl = 0.0
        self.total_commission = self.total_slippage = self.total_borrow = 0.0
        self.total_cash_yield = self.pending_cash_yield = 0.0
        self.interest_clock = execution_time(self.calendar[0])
        self._ran = False

    @property
    def reserved(self):
        return sum(p["margin"] for p in self.positions.values())

    @property
    def equity(self):
        return self.cash + self.reserved

    def _record(self, timestamp, phase):
        gross = sum(float(np.dot(np.abs(p["quantities"]), p["last_marks"])) for p in self.positions.values())
        self.curve.append({"time": timestamp, "phase": phase, "equity": self.equity,
                           "availableCash": self.cash, "reservedMargin": self.reserved,
                           "openPositions": len(self.positions), "grossExposure": gross})

    def _mark(self, session, column, timestamp):
        # Accrue BEFORE this timestamp changes the balance; released margin earns only future yield.
        self.pending_cash_yield += cash_interest(self.cash, self.config.risk_free_rate, self.interest_clock, timestamp)
        self.interest_clock = timestamp
        self.marks.update({symbol: float(bars.at[session, column]) for symbol, bars in self.history.items() if session in bars.index})
        for pair, position in self.positions.items():
            market = np.array([self.marks[s] for s in pair])
            self.cash += float(np.dot(position["quantities"], market - position["last_marks"]))
            days = (pd.Timestamp(timestamp) - pd.Timestamp(position["last_accrual"])).total_seconds() / 86400
            borrow = position["short_notional"] * self.costs.annual_borrow_rate * days / 365
            self.cash -= borrow
            self.total_borrow += borrow
            position["last_marks"], position["last_accrual"] = market, timestamp

    def _reject(self, pair, timestamp, reason):
        self.rejected[reason] += 1
        self.events.append({"time": timestamp, "event": "rejected", "pair": list(pair), "reason": reason})

    def _open(self, pair, order, session, timestamp):
        if len(self.positions) >= self.config.max_concurrent_pairs:
            self._reject(pair, timestamp, "concurrency")
            return
        model = order["model"]
        direction = 1 if order["direction"] == "LONG_SPREAD" else -1
        leg_units = direction * np.array([1.0, -model.beta])
        market = np.array([self.marks[s] for s in pair])
        fills = market * (1 + np.sign(leg_units) * self.costs.slippage_bps / 10000)
        desired = self.sizing.units(order["sigma"], fills, model.beta)
        if desired is None:
            self._reject(pair, timestamp, "volatility")
            return
        unit_gross = float(np.dot(np.abs(leg_units), fills))
        unit_fee = unit_gross * self.costs.commission_bps / 10000
        unit_slippage = float(np.dot(leg_units, fills - market))
        units = min(desired, max(self.cash, 0) / (unit_gross + unit_fee + unit_slippage))
        gross = units * unit_gross
        if gross < self.sizing.min_gross_notional - 1e-8 or self.equity <= 0:
            self._reject(pair, timestamp, "cash")
            return
        quantities = units * leg_units
        fee, slippage = units * unit_fee, units * unit_slippage
        cap = self.config.max_ticker_fraction * (self.equity - fee - slippage)
        exposure = Counter()
        for existing_pair, position in self.positions.items():
            for symbol, quantity in zip(existing_pair, position["quantities"]):
                exposure[symbol] += abs(quantity) * self.marks[symbol]
        for symbol, quantity, mark, fill in zip(pair, quantities, market, fills):
            exposure[symbol] += abs(quantity) * max(mark, fill)
        if any(value > cap + 1e-8 for value in exposure.values()):
            self._reject(pair, timestamp, "ticker_cap")
            return
        if self.sector_of is not None:
            sector_cap = self.config.max_sector_fraction * (self.equity - fee - slippage)
            sector_exposure = Counter()
            for existing_pair, position in self.positions.items():
                for symbol, quantity in zip(existing_pair, position["quantities"]):
                    sector_exposure[self.sector_of.get(symbol, symbol)] += abs(quantity) * self.marks[symbol]
            for symbol, quantity, mark, fill in zip(pair, quantities, market, fills):
                sector_exposure[self.sector_of.get(symbol, symbol)] += abs(quantity) * max(mark, fill)
            if any(value > sector_cap + 1e-8 for value in sector_exposure.values()):
                self._reject(pair, timestamp, "sector_cap")
                return
        self.cash -= gross + fee + slippage
        position = {"direction": order["direction"], "model": model, "quantities": quantities,
                    "prices": fills, "entryTime": timestamp, "entry_index": self.contexts[pair].locations[session],
                    "entry_fee": fee, "short_notional": float(np.dot(np.maximum(-quantities, 0), fills)),
                    "margin": gross, "last_marks": market, "last_accrual": timestamp}
        self.positions[pair] = position
        self.total_commission += fee
        self.total_slippage += slippage
        self.peak_concurrent = max(self.peak_concurrent, len(self.positions))
        self.events.append({"time": timestamp, "event": "entry", "pair": list(pair), "grossNotional": gross,
                            "quantities": quantities.tolist(), "fee": fee, "slippage": slippage,
                            "cashResized": units < desired - 1e-10})
        self._record(timestamp, "entry")

    def _close(self, pair, timestamp):
        position = self.positions[pair]
        market = np.array([self.marks[s] for s in pair])
        quantities = position["quantities"]
        fills = market * (1 - np.sign(quantities) * self.costs.slippage_bps / 10000)
        fee = float(np.dot(np.abs(quantities), fills) * self.costs.commission_bps / 10000)
        slippage = float(np.dot(quantities, market - fills))
        # Market PnL and borrow were already settled at this open; settle only exit costs here.
        self.cash += position["margin"] - fee - slippage
        net = liquidation_pnl(position, market, timestamp, self.costs)
        self.realized_pnl += net
        self.total_commission += fee
        self.total_slippage += slippage
        self.trades.append({"pair": " / ".join(pair), "status": "CLOSED", "entryTime": position["entryTime"],
                            "exitTime": timestamp, "netPnl": round(net, 2)})
        del self.positions[pair]
        self.events.append({"time": timestamp, "event": "exit", "pair": list(pair), "netPnl": net,
                            "fee": fee, "slippage": slippage})
        self._record(timestamp, "exit")

    def run(self):
        if self._ran:
            raise RuntimeError("Create a fresh engine for each simulation")
        self._ran = True
        self._record(execution_time(self.calendar[0]), "initial")
        for session in self.calendar:
            opening, closing = execution_time(session), close_time(session)
            self._mark(session, "Open", opening)
            self._record(opening, "open_mark")
            # Release all available exit collateral before admitting any new entry at this open.
            for pair in sorted(self.pending_exits):
                if session in self.contexts[pair].locations:
                    self._close(pair, opening)
                    self.pending_exits.remove(pair)
            for pair, order in sorted(self.pending_entries.items(), key=lambda item: (item[1]["signal_session"], item[0])):
                if not self.contexts[pair].allows(session):
                    self._reject(pair, opening, "inactive")
                    del self.pending_entries[pair]
                elif session in self.contexts[pair].locations:
                    self._open(pair, order, session, opening)
                    del self.pending_entries[pair]
            self._mark(session, "Close", closing)
            if self.pending_cash_yield:
                self.cash += self.pending_cash_yield
                self.total_cash_yield += self.pending_cash_yield
                self.events.append({"time": closing, "event": "cash_yield", "amount": self.pending_cash_yield})
                self.pending_cash_yield = 0.0
            self._record(closing, "close_mark")
            for pair, context in self.contexts.items():
                if pair in self.positions:
                    if context.should_exit(session, self.positions[pair], closing, self.costs, self.risk):
                        self.pending_exits.add(pair)
                elif pair not in self.pending_entries:
                    order = context.entry(session)
                    if order is not None:
                        self.pending_entries[pair] = order
        self.trades.sort(key=lambda t: (t["exitTime"], t["pair"], t["entryTime"]))
        values = np.array([row["equity"] for row in self.curve])
        peaks = np.maximum.accumulate(values)
        drawdowns = peaks - values
        wins = sum(t["netPnl"] > 0 for t in self.trades)
        pnl = self.equity - self.config.initial_equity
        summary = {
            "initialEquity": self.config.initial_equity, "finalEquity": self.equity,
            "portfolioPnl": pnl, "portfolioReturnPct": 100 * pnl / self.config.initial_equity,
            "maxDrawdown": float(drawdowns.max()), "maxDrawdownPct": float((100 * drawdowns / peaks).max()),
            "peakConcurrentPositions": self.peak_concurrent, "closedTrades": len(self.trades),
            "winRate": 100 * wins / len(self.trades) if self.trades else None,
            "realizedPnlUnrounded": self.realized_pnl,
            "openNetMarkedPnl": pnl - self.realized_pnl - self.total_cash_yield,
            "cashYield": self.total_cash_yield, "tradingPnl": pnl - self.total_cash_yield,
            "availableCash": self.cash, "reservedMargin": self.reserved, "openPositions": len(self.positions),
            "commission": self.total_commission, "slippage": self.total_slippage, "borrow": self.total_borrow,
            "rejectedEntries": dict(self.rejected),
        }
        summary.update(portfolio_metrics(self.curve, self.config.risk_free_rate, summary["maxDrawdownPct"] / 100))
        return PortfolioResult(self.trades, self.calendar[-1], summary, self.curve, self.events)
