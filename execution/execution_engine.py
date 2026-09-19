"""Database-backed simulated execution using the canonical pair-trading primitives.

The live ledger intentionally reuses Pipeline A's spread model and net-liquidation
accounting. The legacy scanner's raw price-ratio z-score and fixed-dollar PnL
calculation are not used here.
"""

import datetime
import hashlib
import itertools
import json
import logging
import sqlite3
from collections import deque
from pathlib import Path
from threading import Lock

import numpy as np
import pandas as pd
from sqlalchemy import text

from config.database import db
from config.risk_config import RiskConfig
from config.settings import settings
from core.signal_generator import signals_hub
from execution.order_book_simulator import OrderBookSimulator
from execution.order_manager import OrderManager
from execution.order_models import MarketSnapshot, Order, OrderStatus
from execution.position_ledger import insert_open_position_legs
from execution.telemetry import NullTelemetryHook, TelemetryHook
from research.pair_accounting import liquidation_pnl
from research.run_backtest import ExecutionConfig
from strategies.kalman_pair import KalmanPairStrategy, fit_ou
from strategies.pair_trading import PairTradingStrategy

logger = logging.getLogger(__name__)


class ExecutionEngine:
    """Manage the simulated SQL ledger with research-consistent trade mechanics."""

    MIN_BAR_VOLUME = 1_000.0
    MAX_HALF_LIFE = 20.0
    MAX_COINT_PVALUE = 0.05
    MIN_PRICE_SERIES_BARS = 20

    def __init__(self, portfolio_equity: float = 100000.0, strategy=None, costs=None, order_manager=None,
                 telemetry: TelemetryHook | None = None, hermes_ledger_path=None):
        if not np.isfinite(portfolio_equity) or portfolio_equity <= 0:
            raise ValueError("portfolio_equity must be finite and positive")
        self.portfolio_equity = float(portfolio_equity)
        # KalmanPairStrategy is the canonical live scanner: each observation is
        # filtered causally, so the current signal only has access to the price
        # history available at that instant.  PairTradingStrategy remains an
        # injectable compatibility path for research/backwards-compatible users.
        self.strategy = strategy or KalmanPairStrategy()
        # Pipeline A's documented defaults: 10 bps commission, 5 bps slippage,
        # and 3% annual short borrow. liquidation_pnl owns their application.
        self.costs = costs or ExecutionConfig()
        self.telemetry = telemetry if telemetry is not None else NullTelemetryHook()
        self.order_manager = order_manager or OrderManager(OrderBookSimulator(self.costs), telemetry=self.telemetry)
        self.hermes_ledger_path = hermes_ledger_path
        self._gatekeeper_rejections = deque(maxlen=25)
        self._gatekeeper_rejections_lock = Lock()

    def _record_gatekeeper_rejection(self, pair: str, reason: str) -> None:
        """Keep a bounded, in-process audit trail for the operator dashboard."""
        occurred_at = pd.Timestamp.now(tz="UTC").isoformat()
        item = {
            "pair": str(pair),
            "reason": str(reason),
            "occurred_at": pd.Timestamp(occurred_at).strftime("%H:%M:%S UTC"),
        }
        with self._gatekeeper_rejections_lock:
            self._gatekeeper_rejections.appendleft(item)
        try:
            self.telemetry.emit({
                "type": "gatekeeper_rejection",
                "pair": str(pair),
                "status": "REJECTED",
                "reason": str(reason),
                "occurred_at": occurred_at,
            })
        except Exception:
            logger.debug("Could not emit gatekeeper rejection telemetry for %s", pair, exc_info=True)

    def _record_pre_trade_rejection(self, opportunity: dict, reason: str) -> None:
        """Persist rejected candidate legs without letting audit storage affect risk control."""
        pair = opportunity.get("pair")
        if not isinstance(pair, str):
            return
        symbols = tuple(pair.split(" / "))
        if len(symbols) != 2 or not all(symbols):
            return
        try:
            self.order_manager.record_pre_trade_rejection(
                pair,
                symbols,
                str(opportunity.get("action") or ""),
                pd.Timestamp.now(tz="UTC"),
                reason,
            )
        except Exception:
            logger.debug("Could not persist pre-trade rejection for %s", pair, exc_info=True)

    def recent_gatekeeper_rejections(self) -> list[dict]:
        """Return a snapshot without exposing the worker's mutable deque."""
        with self._gatekeeper_rejections_lock:
            return list(self._gatekeeper_rejections)

    def _emit_stale_data_warning(self, pair: str, missing_symbols: list[str]) -> None:
        """Expose an unmonitored open position without interrupting the trade loop."""
        event = {
            "type": "pair_order_terminal",
            "pair": pair,
            "status": "STALE_DATA_UNMONITORED",
            "reason": "Missing current price data for open position",
            "missing_symbols": missing_symbols,
            "occurred_at": pd.Timestamp.now(tz="UTC").isoformat(),
        }
        try:
            self.telemetry.emit(event)
        except Exception:
            # Telemetry is observational; missing data must be visible in logs
            # but its delivery may never halt position management.
            logger.debug("Could not emit stale-data telemetry for %s", pair, exc_info=True)

    @property
    def _pair_notional(self) -> float:
        """The configured maximum pair weight, split evenly across both legs."""
        return self.portfolio_equity * RiskConfig.MAX_POSITION_WEIGHT

    @property
    def _leg_weight(self) -> float:
        return RiskConfig.MAX_POSITION_WEIGHT / 2

    def _get_open_positions(self) -> dict:
        """Reconstruct pair positions from the schema's one-symbol rows."""
        query = """SELECT id, strategy_name, symbol, side, quantity, entry_price,
                          current_price, opened_at
                   FROM positions WHERE status = 'OPEN';"""
        with db.engine.connect() as conn:
            result = conn.execute(text(query)).mappings().fetchall()
        grouped = {}
        for row in result:
            pair = str(row["strategy_name"] or "")
            if " / " not in pair:
                # Positions from other strategies are intentionally outside this
                # pair engine's lifecycle.
                continue
            grouped.setdefault(pair, []).append(dict(row))

        positions = {}
        for pair, legs in grouped.items():
            symbols = pair.split(" / ")
            by_symbol = {leg["symbol"]: leg for leg in legs}
            if len(symbols) != 2 or any(symbol not in by_symbol for symbol in symbols):
                continue
            first, second = (by_symbol[symbol] for symbol in symbols)
            action = "LONG_SPREAD" if first["side"] == "BUY" and second["side"] == "SELL" else "SHORT_SPREAD"
            positions[pair] = {
                "pair": pair,
                "action": action,
                "legs": [first, second],
                "id": [first["id"], second["id"]],
                "price_a_entry": first["entry_price"],
                "price_b_entry": second["entry_price"],
                "entry_time": first["opened_at"],
            }
        return positions

    @staticmethod
    def _direction(action: str) -> str:
        """Accept canonical actions and the pre-consolidation SQL ledger spelling."""
        if action == "LONG_SPREAD" or str(action).startswith("BUY"):
            return "LONG_SPREAD"
        if action == "SHORT_SPREAD" or str(action).startswith("SELL"):
            return "SHORT_SPREAD"
        raise ValueError(f"Unsupported position action: {action!r}")

    def _position_from_record(self, record):
        """Build Pipeline A's accounting contract for a SQL row without a migration.

        Positions are persisted one leg per row, so actual signed quantities are
        retained rather than being reconstructed from a notional guess.
        """
        self._direction(record["action"])  # validates the action is a recognized position type
        prices = np.asarray([record["price_a_entry"], record["price_b_entry"]], dtype=float)
        if not np.isfinite(prices).all() or (prices <= 0).any():
            raise ValueError("Position entry prices must be finite and positive")
        legs = record["legs"]
        quantities = np.asarray([
            float(leg["quantity"]) * (1 if leg["side"] == "BUY" else -1)
            for leg in legs
        ])
        entry_fee = float(np.dot(np.abs(quantities), prices) * self.costs.commission_bps / 10000)
        entry_time = pd.Timestamp(record["entry_time"])
        entry_time = entry_time.tz_localize("UTC") if entry_time.tzinfo is None else entry_time.tz_convert("UTC")
        return {
            "quantities": quantities,
            "prices": prices,
            "entryTime": entry_time,
            "entry_fee": entry_fee,
            "short_notional": float(np.dot(np.maximum(-quantities, 0), prices)),
        }

    def _net_pnl(self, record, market, timestamp) -> float:
        return liquidation_pnl(self._position_from_record(record), market, timestamp, self.costs)

    def _validated_pair_series(self, pair: str, series_a: pd.Series, series_b: pd.Series) -> tuple[pd.Series, pd.Series] | None:
        """Return aligned, tradeable closes or skip a malformed pair safely."""
        try:
            aligned = pd.concat(
                [series_a.rename("a"), series_b.rename("b")], axis=1, join="inner"
            ).apply(pd.to_numeric, errors="coerce").dropna().sort_index()
            values = aligned.to_numpy(dtype=float)
        except (TypeError, ValueError):
            logger.warning("[INVALID_PRICE_SERIES_SKIPPED] Pair: %s, Reason: non-numeric prices", pair)
            return None
        if len(aligned) < self.MIN_PRICE_SERIES_BARS:
            logger.warning("[INVALID_PRICE_SERIES_SKIPPED] Pair: %s, Reason: %d bars (< %d)",
                           pair, len(aligned), self.MIN_PRICE_SERIES_BARS)
            return None
        if not np.isfinite(values).all() or (values <= 0).any():
            logger.warning("[INVALID_PRICE_SERIES_SKIPPED] Pair: %s, Reason: non-finite or non-positive prices", pair)
            return None
        return aligned["a"], aligned["b"]

    def _is_risk_approved(self, conn, pair, stock_a, stock_b, action=None) -> tuple[bool, str]:
        """Apply the current percentage-based risk contract to the SQL ledger."""
        daily_pnl = conn.execute(text("""
            SELECT COALESCE(SUM(unrealized_pnl), 0) FROM positions
            WHERE status = 'CLOSED' AND closed_at >= CURRENT_DATE;
        """)).scalar() or 0.0
        daily_loss_limit = -self.portfolio_equity * RiskConfig.MAX_DAILY_LOSS_PCT
        if float(daily_pnl) <= daily_loss_limit:
            return False, f"Daily Loss Limit Hit (${daily_pnl:.2f})"

        closed_pnls = conn.execute(text("""
            SELECT unrealized_pnl FROM positions WHERE status = 'CLOSED' ORDER BY closed_at ASC;
        """)).scalars().all()
        equity, peak, maximum_drawdown = self.portfolio_equity, self.portfolio_equity, 0.0
        for pnl in closed_pnls:
            equity += float(pnl or 0.0)
            peak = max(peak, equity)
            maximum_drawdown = max(maximum_drawdown, peak - equity)
        if maximum_drawdown >= self.portfolio_equity * RiskConfig.MAX_PORTFOLIO_DRAWDOWN_PCT:
            return False, "Portfolio Drawdown Limit Hit"

        open_count = conn.execute(text("SELECT COUNT(DISTINCT strategy_name) FROM positions WHERE status = 'OPEN';")).scalar() or 0
        if int(open_count) >= RiskConfig.MAX_OPEN_POSITIONS:
            return False, f"Max Open Positions Reached ({open_count})"
        if (int(open_count) + 1) * RiskConfig.MAX_POSITION_WEIGHT > RiskConfig.MAX_STRATEGY_EXPOSURE + 1e-12:
            return False, "Max Strategy Exposure Reached"

        # A symbol's risk is directional.  A BUY and a SELL in separate pairs
        # offset one another; two BUYs (or two SELLs) compound the exposure.
        # We deliberately use the individual persisted legs, not pair labels,
        # because the positions schema is one row per leg.
        open_legs = conn.execute(text("""
            SELECT symbol, side FROM positions WHERE status = 'OPEN';
        """)).mappings().all()
        net_asset_units = {}
        for leg in open_legs:
            side = str(leg.get("side") or "").upper()
            if side not in {"BUY", "SELL"}:
                continue
            symbol = str(leg.get("symbol") or "")
            if symbol:
                net_asset_units[symbol] = net_asset_units.get(symbol, 0) + (1 if side == "BUY" else -1)

        direction = self._direction(action or "LONG_SPREAD")
        proposed_sides = (
            ((stock_a, 1), (stock_b, -1)) if direction == "LONG_SPREAD"
            else ((stock_a, -1), (stock_b, 1))
        )
        for asset, proposed_unit in proposed_sides:
            exposure = abs(net_asset_units.get(asset, 0) + proposed_unit) * self._leg_weight
            if exposure > RiskConfig.MAX_ASSET_EXPOSURE + 1e-12:
                return False, f"Max Asset Exposure Hit for {asset}"

        last_close = conn.execute(text("""
            SELECT MAX(closed_at) FROM positions
            WHERE status = 'CLOSED' AND strategy_name = :pair;
        """), {"pair": pair}).scalar()
        if last_close:
            close_time = pd.Timestamp(last_close)
            close_time = close_time.tz_localize("UTC") if close_time.tzinfo is None else close_time.tz_convert("UTC")
            elapsed = pd.Timestamp.now(tz="UTC").to_pydatetime() - close_time.to_pydatetime()
            cooldown = datetime.timedelta(hours=RiskConfig.COOLDOWN_HOURS)
            if elapsed < cooldown:
                return False, f"Pair in Cooldown ({int((cooldown - elapsed).total_seconds() / 60)} min left)"
        return True, "Risk Approved"

    def _scan_opportunities(self, price_matrix: pd.DataFrame) -> list[dict]:
        """Generate causal pair signals from price levels, never a price ratio."""
        if price_matrix.empty:
            return []
        opportunities = []
        for stock_a, stock_b in itertools.combinations(price_matrix.columns, 2):
            pair = f"{stock_a} / {stock_b}"
            valid_series = self._validated_pair_series(pair, price_matrix[stock_a], price_matrix[stock_b])
            if valid_series is None:
                continue
            series_a, series_b = valid_series
            if isinstance(self.strategy, KalmanPairStrategy):
                observations = self.strategy.analyze(series_a, series_b)
                if not observations:
                    continue
                observation = observations[-1]
                action = self.strategy.entry_signal(observation)
                if action is None:
                    continue

                # Cointegration remains a separate pre-trade statistical gate.
                # Fit it only on the preceding window: it validates the signal
                # without supplying its beta, alpha, or z-score.
                validation = PairTradingStrategy().fit(series_a.iloc[:-1], series_b.iloc[:-1])
                if validation is None:
                    continue
                opportunities.append({"pair": pair, "action": action,
                                      "z_score": observation.z, "alpha": observation.alpha,
                                      "beta": observation.beta, "price_a": float(series_a.iloc[-1]),
                                      "price_b": float(series_b.iloc[-1]),
                                      "coint_pvalue": validation.pvalue,
                                      "half_life": observation.half_life,
                                      "price_timestamp": series_a.index[-1]})
                continue

            # Legacy injection path retained for callers that explicitly supply
            # Pipeline A's static strategy.
            training_a, training_b = series_a.iloc[:-1], series_b.iloc[:-1]
            model = self.strategy.fit(training_a, training_b)
            if model is None:
                continue
            price_a, price_b = float(series_a.iloc[-1]), float(series_b.iloc[-1])
            action = self.strategy.entry_signal(model, price_a, price_b)
            if action is not None:
                residuals = training_a - model.alpha - model.beta * training_b
                ou = fit_ou(residuals.to_numpy(dtype=float), decay=1, min_observations=30)
                opportunities.append({"pair": pair, "action": action,
                                      "z_score": model.z_score(price_a, price_b), "alpha": model.alpha,
                                      "beta": model.beta, "price_a": price_a, "price_b": price_b,
                                      "coint_pvalue": model.pvalue,
                                      "half_life": ou.half_life if ou is not None else None,
                                      "price_timestamp": series_a.index[-1]})
        return opportunities

    def _active_hermes_stability_alert(self, pair: str) -> dict | None:
        """Return the latest active alert for a pair without giving Hermes control.

        The execution worker reads Hermes' append-only ledger, then makes its own
        risk decision.  A ledger outage remains non-blocking, consistent with the
        existing pre-trade gate.
        """
        try:
            from hermes.audit_ledger import DEFAULT_LEDGER_PATH

            path = Path(self.hermes_ledger_path or DEFAULT_LEDGER_PATH)
            if not path.exists():
                return None
            uri = f"{path.resolve().as_uri()}?mode=ro"
            with sqlite3.connect(uri, uri=True) as connection:
                rows = connection.execute("""
                    SELECT event_id, payload_json FROM audit_events
                    WHERE kind = 'STABILITY_ALERT' ORDER BY epoch_ms DESC
                """).fetchall()
            for event_id, payload_json in rows:
                payload = json.loads(payload_json)
                if payload.get("pair") == pair and payload.get("active", True):
                    return {"event_id": str(event_id), "payload": payload}
        except Exception:
            # Hermes is an observer: unavailable audit storage must never hold the
            # paper execution loop hostage.
            logger.debug("Hermes passive check unavailable for %s", pair, exc_info=True)
        return None

    def _hermes_allows_pair(self, pair: str) -> tuple[bool, str]:
        """Block new entries for pairs with an active Hermes stability alert."""
        if self._active_hermes_stability_alert(pair) is not None:
            return False, "Active Hermes stability alert"
        return True, "Hermes clear"

    @staticmethod
    def _alert_metrics(alert: dict) -> tuple[float | None, float | None]:
        """Normalize the stability values carried by present and older alerts."""
        payload = alert.get("payload", {})
        report = payload.get("report", {}) if isinstance(payload, dict) else {}
        raw_pvalue = report.get("coint_pvalue", payload.get("coint_pvalue", payload.get("p_value")))
        raw_half_life = report.get("ou_half_life", payload.get("ou_half_life", payload.get("half_life")))

        def finite_number(value):
            try:
                number = float(value)
            except (TypeError, ValueError):
                return None
            return number if np.isfinite(number) else None

        return finite_number(raw_pvalue), finite_number(raw_half_life)

    def _record_de_risk_trigger(self, pair: str, record: dict, alert: dict) -> None:
        """Persist the execution-owned kill-switch decision to the audit ledger."""
        from hermes.audit_ledger import DEFAULT_LEDGER_PATH, AuditLedger
        from hermes.models import AuditEvent, AuditEventKind

        closed_symbols = [str(leg["symbol"]) for leg in record["legs"]]
        coint_pvalue, ou_half_life = self._alert_metrics(alert)
        event_identity = {
            "pair": pair,
            "position_ids": [leg["id"] for leg in record["legs"]],
            "stability_alert_event_id": alert.get("event_id"),
        }
        event_id = hashlib.sha256(json.dumps(event_identity, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        occurred_at = pd.Timestamp.now(tz="UTC").isoformat()
        payload = {
            "type": "de_risk_triggered",
            "event_id": event_id,
            "pair": pair,
            "reason": "STABILITY_KILL_SWITCH",
            "closed_symbols": closed_symbols,
            "stability_alert_event_id": alert.get("event_id"),
            "coint_pvalue": coint_pvalue,
            "ou_half_life": ou_half_life,
            "occurred_at": occurred_at,
        }
        try:
            path = self.hermes_ledger_path or DEFAULT_LEDGER_PATH
            with AuditLedger(path) as ledger:
                ledger.append(AuditEvent(event_id, AuditEventKind.DE_RISK_TRIGGERED, occurred_at, payload))
        except Exception:
            # Audit persistence is retried by the telemetry bridge if one is
            # configured; it may not reverse a successfully filled emergency exit.
            logger.exception("Could not write de-risk audit event for %s", pair)
        try:
            self.telemetry.emit(payload)
        except Exception:
            logger.debug("Could not emit de-risk telemetry for %s", pair, exc_info=True)

    def _pre_trade_risk_gate(self, conn, opportunity: dict) -> tuple[bool, str]:
        """Fail closed on invalid market/statistical inputs before paper entry."""
        pair = opportunity.get("pair", "UNKNOWN")
        if not isinstance(pair, str) or len(pair.split(" / ")) != 2:
            return False, "Invalid pair identity"
        try:
            pvalue = float(opportunity.get("coint_pvalue"))
            z_score = float(opportunity.get("z_score"))
            half_life = float(opportunity.get("half_life"))
            prices = tuple(float(opportunity.get(key)) for key in ("price_a", "price_b"))
        except (TypeError, ValueError):
            return False, "Incomplete statistical or price data"
        if not np.isfinite(pvalue) or pvalue >= self.MAX_COINT_PVALUE:
            return False, "Cointegration p-value threshold failed"
        entry_z = float(getattr(self.strategy.config, "entry_z", 2.0))
        if not np.isfinite(z_score) or abs(z_score) < entry_z:
            return False, "Z-score entry threshold failed"
        if not np.isfinite(half_life) or not 0 < half_life <= self.MAX_HALF_LIFE:
            return False, "OU half-life threshold failed"
        if not np.isfinite(prices).all() or min(prices) <= 0:
            return False, "Invalid latest prices"
        timestamp = opportunity.get("price_timestamp")
        if timestamp is not None:
            try:
                price_time = pd.Timestamp(timestamp)
                price_time = price_time.tz_localize("UTC") if price_time.tzinfo is None else price_time.tz_convert("UTC")
                age = pd.Timestamp.now(tz="UTC") - price_time
                max_price_age = datetime.timedelta(days=settings.MAX_PRICE_AGE_DAYS)
                if age < datetime.timedelta(0) or age > max_price_age:
                    return False, "Stale price data"
            except (TypeError, ValueError):
                return False, "Invalid price timestamp"
        approved, reason = self._is_risk_approved(
            conn, pair, *pair.split(" / "), action=opportunity.get("action")
        )
        if not approved:
            return False, reason
        return self._hermes_allows_pair(pair)

    def _close_position(self, record, prices, reason):
        """Submit offsetting legs before mutating the SQL position ledger."""
        stock_a, stock_b = record["pair"].split(" / ")
        position = self._position_from_record(record)
        quantities = position["quantities"]
        requested_at = pd.Timestamp.now(tz="UTC")
        close_a = Order(record["pair"], stock_a, "SELL" if quantities[0] > 0 else "BUY",
                        abs(float(quantities[0])), "MARKET", requested_at)
        close_b = Order(record["pair"], stock_b, "SELL" if quantities[1] > 0 else "BUY",
                        abs(float(quantities[1])), "MARKET", requested_at)
        order_result = self.order_manager.submit_pair_orders(
            record["pair"], close_a, close_b,
            self._market_snapshot(stock_a, float(prices[0]), close_a.quantity),
            self._market_snapshot(stock_b, float(prices[1]), close_b.quantity),
        )
        if order_result["status"] is not OrderStatus.FILLED:
            logger.warning("Close order for %s was not fully filled: %s", record["pair"],
                           order_result["status"].value)
            return False
        fill_prices = [leg.fill.filled_price for leg in order_result["legs"]]
        net_pnl = self._net_pnl(record, fill_prices, datetime.datetime.now(datetime.UTC))
        with db.engine.begin() as conn:
            for index, leg in enumerate(record["legs"]):
                signed_quantity = float(leg["quantity"]) * (1 if leg["side"] == "BUY" else -1)
                leg_pnl = signed_quantity * (fill_prices[index] - float(leg["entry_price"]))
                conn.execute(text("""
                    UPDATE positions SET status = 'CLOSED', closed_at = CURRENT_TIMESTAMP,
                        current_price = :current_price, unrealized_pnl = :pnl
                    WHERE id = :id;
                """), {"current_price": fill_prices[index], "pnl": round(leg_pnl, 2), "id": leg["id"]})
        logger.info("Closed %s (%s). Net PnL: $%.2f", record["pair"], reason, net_pnl)
        return True

    def _market_snapshot(self, symbol: str, price: float, quantity: float) -> MarketSnapshot:
        """Use the last persisted bar's liquidity without manufacturing volume.

        ``quantity`` remains in the bridge signature for callers, but deliberately
        never contributes to bar volume: an order must not make itself liquid.
        The current stock_prices schema has no quoted-spread field, so the
        research-consistent configured spread is used until one is persisted.
        """
        del quantity
        volume = self.MIN_BAR_VOLUME
        try:
            with db.engine.connect() as conn:
                row = conn.execute(text("""
                    SELECT volume FROM stock_prices
                    WHERE symbol = :symbol
                    ORDER BY date DESC LIMIT 1
                """), {"symbol": symbol}).mappings().first()
            if row is not None:
                candidate = float(row.get("volume") or 0.0)
                if np.isfinite(candidate) and candidate > 0:
                    volume = max(candidate, self.MIN_BAR_VOLUME)
        except Exception:
            logger.debug("No usable market volume for %s; applying safe volume floor", symbol, exc_info=True)
        return MarketSnapshot(float(price), volume, float(self.costs.slippage_bps))

    def manage_orders_and_positions(self, price_matrix: pd.DataFrame | None = None):
        """Run one database-backed, canonical-model execution cycle.

        ``price_matrix``, when supplied, is used instead of rebuilding it --
        the worker cycle can compute it once and share it with the desk scan
        that follows, rather than each running an identical DB query+pivot.
        """
        logger.info("Execution cycle triggered at %s", datetime.datetime.now(datetime.UTC).strftime("%H:%M:%S"))
        price_matrix = signals_hub._build_price_matrix() if price_matrix is None else price_matrix
        open_positions = self._get_open_positions()
        if price_matrix.empty:
            logger.info("Price matrix is empty; only emergency exits can be processed.")
            opportunities = []
        else:
            opportunities = self._scan_opportunities(price_matrix)

        for pair, record in list(open_positions.items()):
            stock_a, stock_b = pair.split(" / ")
            alert = None
            if settings.ENABLE_AUTO_DERISKING and settings.DERISK_ON_STABILITY_ALERT:
                alert = self._active_hermes_stability_alert(pair)
            if alert is not None:
                # A market order does not require a fresh quote to be sent.  The
                # simulator does, so use the last persisted mark only when the
                # current matrix cannot provide both legs.
                if stock_a in price_matrix and stock_b in price_matrix:
                    prices = [float(price_matrix[stock_a].iloc[-1]), float(price_matrix[stock_b].iloc[-1])]
                else:
                    prices = [float(leg.get("current_price") or leg["entry_price"]) for leg in record["legs"]]
                if np.isfinite(prices).all() and min(prices) > 0:
                    if self._close_position(record, prices, "STABILITY_KILL_SWITCH"):
                        self._record_de_risk_trigger(pair, record, alert)
                        open_positions.pop(pair)
                    continue
                logger.warning("[STABILITY_KILL_SWITCH_PENDING] Pair: %s has no usable close price", pair)
            if stock_a not in price_matrix or stock_b not in price_matrix:
                missing_symbols = [
                    symbol for symbol in (stock_a, stock_b) if symbol not in price_matrix.columns
                ]
                logger.warning(
                    "[STALE_DATA_UNMONITORED] Pair: %s, Missing symbols: %s",
                    pair, ", ".join(missing_symbols),
                )
                self._emit_stale_data_warning(pair, missing_symbols)
                continue
            valid_series = self._validated_pair_series(pair, price_matrix[stock_a], price_matrix[stock_b])
            if valid_series is None:
                continue
            series_a, series_b = valid_series
            prices = [float(series_a.iloc[-1]), float(series_b.iloc[-1])]
            if isinstance(self.strategy, KalmanPairStrategy):
                observations = self.strategy.analyze(series_a, series_b)
                z_score = observations[-1].z if observations else None
            else:
                current_model = self.strategy.fit(
                    series_a.iloc[:-1], series_b.iloc[:-1]
                )
                z_score = current_model.z_score(*prices) if current_model is not None else None
            direction = self._direction(record["action"])
            stop_loss = self._net_pnl(record, prices, datetime.datetime.now(datetime.UTC)) <= -self.portfolio_equity * RiskConfig.MAX_RISK_PER_TRADE
            mean_reverted = z_score is not None and self.strategy.should_close(direction, z_score)
            if (stop_loss or mean_reverted) and self._close_position(
                    record, prices, "risk stop" if stop_loss else "mean reversion"):
                open_positions.pop(pair)

        for opportunity in opportunities:
            pair = opportunity["pair"]
            if pair in open_positions:
                continue
            with db.engine.connect() as conn:
                approved, reason = self._pre_trade_risk_gate(conn, opportunity)
            if not approved:
                logger.info("[GATEKEEPER REJECT] Pair: %s, Reason: %s", pair, reason)
                self._record_gatekeeper_rejection(pair, reason)
                self._record_pre_trade_rejection(opportunity, reason)
                continue
            stock_a, stock_b = pair.split(" / ")
            direction = opportunity["action"]
            requested_at = pd.Timestamp.now(tz="UTC")
            # Dynamic Kalman beta determines the hedge quantity.  The pair's
            # gross notional stays capped while beta controls the relative legs.
            beta = float(opportunity.get("beta", opportunity["price_a"] / opportunity["price_b"]))
            if not np.isfinite(beta) or beta <= 0:
                reason = "Invalid dynamic hedge ratio"
                logger.info("[GATEKEEPER REJECT] Pair: %s, Reason: %s", pair, reason)
                self._record_gatekeeper_rejection(pair, reason)
                self._record_pre_trade_rejection(opportunity, reason)
                continue
            hedge_denominator = opportunity["price_a"] + beta * opportunity["price_b"]
            quantity_a = self._pair_notional / hedge_denominator
            quantity_b = beta * quantity_a
            order_a = Order(pair, stock_a, "BUY" if direction == "LONG_SPREAD" else "SELL",
                            quantity_a, "MARKET", requested_at)
            order_b = Order(pair, stock_b, "SELL" if direction == "LONG_SPREAD" else "BUY",
                            quantity_b, "MARKET", requested_at)
            order_result = self.order_manager.submit_pair_orders(
                pair, order_a, order_b,
                self._market_snapshot(stock_a, opportunity["price_a"], quantity_a),
                self._market_snapshot(stock_b, opportunity["price_b"], quantity_b),
            )
            if order_result["status"] is not OrderStatus.FILLED:
                logger.info("Pair order for %s was not fully filled: %s", pair, order_result["status"].value)
                continue
            fills = [leg.fill for leg in order_result["legs"]]
            with db.engine.begin() as conn:
                insert_open_position_legs(conn, pair, zip((order_a, order_b), fills))
            logger.info("Opened %s using canonical %s signal.", pair, opportunity["action"])
