"""Plan or execute the NVDA / AMD paper short-spread trade.

The default remains read-only.  Passing ``--mutate-ledger`` executes the two
simulated market orders and writes the resulting filled legs to the SQL ledger.
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.database import db
from execution.execution_engine import ExecutionEngine
from execution.order_models import Order, OrderStatus
from execution.position_ledger import insert_open_position_legs
from research.pair_screener import ScreenerConfig, screen_pairs
from strategies.kalman_pair import KalmanPairStrategy
from strategies.pair_trading import PairTradingConfig, PairTradingStrategy

PAIR = "NVDA / AMD"
SYMBOLS = ("NVDA", "AMD")
SCREENING_LOOKBACK_BARS = 252


def _daily_closes() -> pd.DataFrame:
    """Read the two local daily close histories without modifying the database."""
    query = text("""
        SELECT date, symbol, close
        FROM stock_prices_daily
        WHERE symbol IN (:first, :second)
        ORDER BY date, symbol
    """)
    with db.engine.connect() as connection:
        bars = pd.read_sql(query, connection, params={"first": SYMBOLS[0], "second": SYMBOLS[1]})
    bars["date"] = pd.to_datetime(bars["date"], errors="coerce").dt.normalize()
    bars["close"] = pd.to_numeric(bars["close"], errors="coerce")
    matrix = bars.dropna(subset=["date", "symbol", "close"]).pivot(
        index="date", columns="symbol", values="close"
    ).sort_index().reindex(columns=list(SYMBOLS)).dropna()
    if len(matrix) < 20 or not np.isfinite(matrix.to_numpy(dtype=float)).all() or (matrix <= 0).any().any():
        raise ValueError("NVDA / AMD requires at least 20 finite, positive aligned daily bars")
    return matrix.tail(SCREENING_LOOKBACK_BARS)


def _trade_context() -> dict:
    """Build a fully validated, but not yet submitted, paper-trade context."""
    matrix = _daily_closes()
    history = {symbol: pd.DataFrame({"Close": matrix[symbol]}) for symbol in SYMBOLS}
    screening = screen_pairs(
        history,
        matrix.index.min(),
        matrix.index.max() + pd.Timedelta(days=1),
        config=ScreenerConfig(max_pvalue=0.05, max_half_life=20.0, max_hurst=0.45),
        sectors={"Technology / semiconductors": SYMBOLS},
    )
    candidate = next((item for item in screening.selected if item.pair == SYMBOLS), None)
    if candidate is None:
        raise RuntimeError("NVDA / AMD is not currently qualified by the 0.05 / 20 / 0.45 screen")

    static_model = PairTradingStrategy(PairTradingConfig(window=len(matrix))).fit(matrix["NVDA"], matrix["AMD"])
    kalman_observation = KalmanPairStrategy().analyze(matrix["NVDA"], matrix["AMD"])[-1]
    if static_model is None or not np.isfinite(kalman_observation.beta) or kalman_observation.beta <= 0:
        raise RuntimeError("Could not compute a valid NVDA / AMD hedge ratio")
    z_score = static_model.z_score(float(matrix["NVDA"].iloc[-1]), float(matrix["AMD"].iloc[-1]))
    if z_score < 2.0:
        raise RuntimeError(f"NVDA / AMD no longer has a short-spread entry z-score: {z_score:.3f}")

    engine = ExecutionEngine()
    nvda_price, amd_price = float(matrix["NVDA"].iloc[-1]), float(matrix["AMD"].iloc[-1])
    beta = float(kalman_observation.beta)
    nvda_shares = engine._pair_notional / (nvda_price + beta * amd_price)
    amd_shares = beta * nvda_shares
    opportunity = {
        "pair": PAIR,
        "action": "SHORT_SPREAD",
        "z_score": float(z_score),
        "price_a": nvda_price,
        "price_b": amd_price,
        "coint_pvalue": float(candidate.pvalue),
        "half_life": float(candidate.half_life),
        "price_timestamp": matrix.index[-1],
    }
    with db.engine.connect() as connection:
        gate_approved, gate_reason = engine._pre_trade_risk_gate(connection, opportunity)
    hermes_clear, hermes_reason = engine._hermes_allows_pair(PAIR)
    short_notional = nvda_shares * nvda_price
    long_notional = amd_shares * amd_price
    plan = {
        "mode": "DRY_RUN_ONLY",
        "pair": PAIR,
        "screen": {
            "sector": candidate.sector,
            "coint_pvalue": candidate.pvalue,
            "ou_half_life_bars": candidate.half_life,
            "hurst": candidate.hurst,
            "z_score": z_score,
            "signal": "SHORT_SPREAD",
            "last_daily_bar": matrix.index[-1].strftime("%Y-%m-%d"),
        },
        "orders": [
            {"symbol": "NVDA", "side": "SELL_SHORT", "target_shares": nvda_shares,
             "estimated_entry_price": nvda_price, "dollar_value": short_notional},
            {"symbol": "AMD", "side": "BUY", "target_shares": amd_shares,
             "estimated_entry_price": amd_price, "dollar_value": long_notional},
        ],
        "hedge_ratio_beta": beta,
        "net_dollar_delta": long_notional - short_notional,
        "pre_trade_risk_gate": {
            "approved": gate_approved,
            "reason": gate_reason,
            "coint_pvalue_pass": candidate.pvalue < engine.MAX_COINT_PVALUE,
            "hermes_clear": hermes_clear,
            "hermes_reason": hermes_reason,
        },
    }
    return {
        "plan": plan,
        "engine": engine,
        "opportunity": opportunity,
        "quantities": (nvda_shares, amd_shares),
    }


def dry_run_plan() -> dict:
    """Build a read-only paper execution plan."""
    return _trade_context()["plan"]


def execute_paper_trade(*, mutate_ledger: bool = False) -> dict:
    """Optionally submit and persist this specific qualified paper trade.

    Both position legs are written in a single transaction only after the
    pair-order simulator reports both legs FILLED.  ``mutate_ledger=False`` is
    deliberately side-effect-free and returns the normal plan.
    """
    context = _trade_context()
    plan = context["plan"]
    if not mutate_ledger:
        return plan

    engine = context["engine"]
    opportunity = context["opportunity"]
    quantity_a, quantity_b = context["quantities"]
    with db.engine.connect() as connection:
        approved, reason = engine._pre_trade_risk_gate(connection, opportunity)
    if not approved:
        raise RuntimeError(f"Pre-trade risk gate rejected NVDA / AMD: {reason}")

    # Checked before any order is submitted: aborting here must not leave
    # dangling CREATED/SUBMITTED/FILLED audit rows for an entry that never
    # reached the position ledger.
    with db.engine.connect() as connection:
        existing = connection.execute(text("""
            SELECT COUNT(*) FROM positions
            WHERE strategy_name = :pair AND status = 'OPEN'
        """), {"pair": PAIR}).scalar_one()
    if existing:
        raise RuntimeError("NVDA / AMD already has an open position; refusing duplicate paper entry")

    requested_at = pd.Timestamp.now(tz="UTC")
    order_a = Order(PAIR, "NVDA", "SELL", quantity_a, "MARKET", requested_at)
    order_b = Order(PAIR, "AMD", "BUY", quantity_b, "MARKET", requested_at)
    order_result = engine.order_manager.submit_pair_orders(
        PAIR,
        order_a,
        order_b,
        engine._market_snapshot("NVDA", opportunity["price_a"], quantity_a),
        engine._market_snapshot("AMD", opportunity["price_b"], quantity_b),
    )
    if order_result["status"] is not OrderStatus.FILLED:
        raise RuntimeError(f"NVDA / AMD pair order was not fully filled: {order_result['status'].value}")

    fills = [leg.fill for leg in order_result["legs"]]
    with db.engine.begin() as connection:
        insert_open_position_legs(connection, PAIR, zip((order_a, order_b), fills))

    order_ids = [engine.order_manager.simulator._order_id(order) for order in (order_a, order_b)]
    with db.engine.connect() as connection:
        order_rows = connection.execute(text("""
            SELECT order_id, pair, timestamp, leg_symbol, side, target_shares,
                   limit_price, status, rejection_reason, created_at
            FROM orders
            WHERE order_id IN (:first_id, :second_id)
            ORDER BY timestamp, leg_symbol, created_at
        """), {"first_id": order_ids[0], "second_id": order_ids[1]}).mappings().all()
        position_rows = connection.execute(text("""
            SELECT id, strategy_name, symbol, side, quantity, entry_price,
                   current_price, stop_price, take_profit_price, unrealized_pnl,
                   status, opened_at, closed_at
            FROM positions
            WHERE strategy_name = :pair AND status = 'OPEN'
            ORDER BY id
        """), {"pair": PAIR}).mappings().all()

    return {
        **plan,
        "mode": "PAPER_TRADE_LEDGER_MUTATED",
        "pre_trade_risk_gate": {**plan["pre_trade_risk_gate"], "approved": approved, "reason": reason},
        "pair_order_status": order_result["status"].value,
        "orders": [dict(row) for row in order_rows],
        "positions": [dict(row) for row in position_rows],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mutate-ledger", action="store_true",
                        help="submit paper orders and atomically persist the filled positions")
    args = parser.parse_args()
    print(json.dumps(execute_paper_trade(mutate_ledger=args.mutate_ledger), indent=2, allow_nan=False, default=str))


if __name__ == "__main__":
    main()
