"""Shared execution clocks and two-leg net liquidation accounting, without I/O."""

import numpy as np
import pandas as pd


def execution_time(session) -> str:
    timestamp = (pd.Timestamp(session).normalize() + pd.Timedelta(hours=9, minutes=30)).tz_localize("America/New_York")
    return timestamp.tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")


def close_time(session) -> str:
    timestamp = (pd.Timestamp(session).normalize() + pd.Timedelta(hours=16)).tz_localize("America/New_York")
    return timestamp.tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")


def liquidation_pnl(position, market, timestamp, costs) -> float:
    """Net hypothetical liquidation, including paid entry costs and accrued borrow."""
    quantities = position["quantities"]
    exit_prices = np.asarray(market, dtype=float) * (1 - np.sign(quantities) * costs.slippage_bps / 10000)
    gross_pnl = float(np.dot(quantities, exit_prices - position["prices"]))
    exit_fee = float(np.dot(np.abs(quantities), exit_prices) * costs.commission_bps / 10000)
    days = (pd.Timestamp(timestamp) - pd.Timestamp(position["entryTime"])).total_seconds() / 86400
    borrow = position["short_notional"] * costs.annual_borrow_rate * days / 365
    return gross_pnl - position["entry_fee"] - exit_fee - borrow


def cash_interest(available_cash, annual_rate, start, end) -> float:
    """ACT/365 simple accrual on the balance held throughout [start, end)."""
    days = (pd.Timestamp(end) - pd.Timestamp(start)).total_seconds() / 86400
    if not np.isfinite([available_cash, annual_rate, days]).all() or annual_rate < 0 or days < 0:
        raise ValueError("Interest requires finite cash, a nonnegative rate and ordered timestamps")
    return float(max(available_cash, 0) * annual_rate * days / 365)


def portfolio_metrics(curve, annual_rate, max_drawdown_fraction):
    """Daily close excess-return ratios; intraday samples never become daily returns."""
    initial = curve[0]
    daily = [row for row in curve if row["phase"] == "close_mark"]
    times = pd.DatetimeIndex([initial["time"], *[row["time"] for row in daily]])
    values = np.array([initial["equity"], *[row["equity"] for row in daily]], dtype=float)
    if (not daily or not np.isfinite(values).all() or not times.is_monotonic_increasing
            or times.has_duplicates or annual_rate < 0 or not np.isfinite(annual_rate)
            or not np.isfinite(max_drawdown_fraction) or max_drawdown_fraction < 0):
        raise ValueError("Metrics require finite daily equity, ordered timestamps and valid rates")
    days = np.diff(times.asi8) / (86400 * 1e9)
    benchmark_returns = annual_rate * days / 365
    benchmark_return = float(np.expm1(np.log1p(benchmark_returns).sum()))
    result = {
        "dailyObservations": len(daily), "riskFreeRate": annual_rate,
        "annualizedReturnPct": None, "annualizedMeanReturnPct": None,
        "annualizedExcessReturnPct": None, "annualizedVolatilityPct": None,
        "annualizedDownsideDeviationPct": None, "sharpeRatio": None,
        "sortinoRatio": None, "calmarRatio": None, "averageCapitalUtilizationPct": None,
        "riskFreeBenchmarkReturnPct": 100 * benchmark_return,
        "riskFreeBenchmarkPnl": float(values[0] * benchmark_return),
    }
    # Returns across insolvency are not meaningful. Preserve the ledger, report undefined ratios.
    if (values <= 0).any():
        return result
    returns = values[1:] / values[:-1] - 1
    excess = returns - benchmark_returns
    excess[np.abs(excess) < 1e-14] = 0.0
    annual_excess = float(np.mean(excess) * 252)
    volatility = float(np.std(excess, ddof=1) * np.sqrt(252)) if len(excess) > 1 else 0.0
    downside = float(np.sqrt(np.mean(np.minimum(excess, 0) ** 2) * 252))
    cagr = float(np.expm1(np.log(values[-1] / values[0]) * 365 / days.sum()))
    utilization = np.array([row["grossExposure"] for row in daily], dtype=float) / values[1:]
    if not np.isfinite(utilization).all() or (utilization < 0).any():
        raise ValueError("Gross exposure must be finite and nonnegative")
    result.update({
        "annualizedReturnPct": 100 * cagr,
        "annualizedMeanReturnPct": float(100 * np.mean(returns) * 252),
        "annualizedExcessReturnPct": 100 * annual_excess,
        "annualizedVolatilityPct": 100 * volatility,
        "annualizedDownsideDeviationPct": 100 * downside,
        "sharpeRatio": annual_excess / volatility if volatility > 1e-12 and len(daily) > 1 else None,
        "sortinoRatio": annual_excess / downside if downside > 1e-12 and len(daily) > 1 else None,
        "calmarRatio": cagr / max_drawdown_fraction if max_drawdown_fraction > 1e-12 else None,
        "averageCapitalUtilizationPct": float(100 * utilization.mean()),
    })
    return result
