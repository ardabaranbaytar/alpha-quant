import secrets
import os
import datetime
import logging
import threading
from contextlib import asynccontextmanager
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from sqlalchemy import text
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from config.database import db
from config.settings import settings
from core.signal_generator import signals_hub
from execution.execution_engine import ExecutionEngine
from hermes.auditor_hook import HermesAuditorHook
from hermes.ollama_client import OllamaClient
from hermes.service import HermesService
from core.analytics import analytics_manager
from data_pipeline.scheduler import daily_price_task, hourly_price_task

# Uvicorn owns the process console handler; use its logger so worker telemetry is
# emitted alongside access logs instead of being discarded by an unconfigured
# module logger.  basicConfig remains a harmless fallback for direct execution.
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger    = logging.getLogger("uvicorn.error")
logger.setLevel(logging.INFO)
_COOKIE   = "aq_session"
_MAX_AGE  = 8 * 3600  
_US_MARKET_TZ = ZoneInfo("America/New_York")


def _validate_security_configuration() -> None:
    """Reject unsafe web configuration before serving any application routes."""
    if not str(settings.APP_PASSWORD or "").strip():
        raise RuntimeError("APP_PASSWORD must be set to a non-empty value before the web application can start.")
    if "*" in settings.ALLOWED_ORIGINS:
        raise RuntimeError(
            "ALLOWED_ORIGINS must not contain '*' while CORS allow_credentials=True; "
            "configure explicit trusted origins instead."
        )


def _is_us_market_hours(now: datetime.datetime | None = None) -> bool:
    """Return whether ``now`` falls in the regular US equity session."""
    local_now = now or datetime.datetime.now(datetime.UTC)
    if local_now.tzinfo is None:
        local_now = local_now.replace(tzinfo=datetime.UTC)
    local_now = local_now.astimezone(_US_MARKET_TZ)
    market_open = datetime.time(9, 30)
    market_close = datetime.time(16, 0)
    return local_now.weekday() < 5 and market_open <= local_now.time() < market_close


def _refresh_hourly_prices_if_due(application: FastAPI, now: datetime.datetime | None = None) -> bool:
    """Run one hourly refresh per market hour without letting fetch failures escape."""
    current_time = now or datetime.datetime.now(datetime.UTC)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=datetime.UTC)
    market_time = current_time.astimezone(_US_MARKET_TZ)
    if not _is_us_market_hours(market_time):
        return False

    hour_key = market_time.replace(minute=0, second=0, microsecond=0)
    if getattr(application.state, "last_hourly_price_refresh", None) == hour_key:
        return False

    # Record the attempted hour first: a provider outage should be reported but
    # must not cause the one-minute worker loop to hammer yfinance repeatedly.
    application.state.last_hourly_price_refresh = hour_key
    try:
        hourly_price_task()
        return True
    except Exception as exc:
        logger.warning("Hourly price refresh failed; worker will continue: %s", exc, exc_info=True)
        return False


def _refresh_daily_prices_if_due(application: FastAPI, now: datetime.datetime | None = None) -> bool:
    """Refresh daily bars once after the regular US session has closed."""
    current_time = now or datetime.datetime.now(datetime.UTC)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=datetime.UTC)
    market_time = current_time.astimezone(_US_MARKET_TZ)
    if market_time.weekday() >= 5 or not datetime.time(16, 10) <= market_time.time() < datetime.time(23, 59, 59):
        return False

    day_key = market_time.date()
    if getattr(application.state, "last_daily_price_refresh", None) == day_key:
        return False

    # Mark the day before calling the provider so a timeout cannot turn the
    # one-minute worker loop into an uncontrolled retry storm.
    application.state.last_daily_price_refresh = day_key
    try:
        return daily_price_task() is not False
    except Exception as exc:
        logger.warning("Daily price refresh failed; worker will continue: %s", exc, exc_info=True)
        return False


def read_pair_prices(pair: str, bars: int = 300) -> tuple[pd.Series, pd.Series]:
    """Read recent aligned close histories without mutating the market database."""
    symbols = pair.split(" / ")
    if len(symbols) != 2 or not all(symbols):
        raise ValueError("pair must use the 'SYMBOL / SYMBOL' format")
    if bars < 1:
        raise ValueError("bars must be positive")

    histories = []
    query = text("""
        SELECT date, price FROM stock_prices
        WHERE symbol = :symbol
        ORDER BY date DESC LIMIT :bars
    """)
    with db.engine.connect() as conn:
        for symbol in symbols:
            rows = conn.execute(query, {"symbol": symbol, "bars": int(bars)}).mappings().all()
            index = pd.to_datetime([row["date"] for row in rows], utc=True)
            values = pd.to_numeric([row["price"] for row in rows], errors="coerce")
            series = pd.Series(values, index=index, dtype=float).sort_index()
            histories.append(series[np.isfinite(series) & (series > 0)])
    aligned = pd.concat(histories, axis=1, join="inner").dropna()
    return aligned.iloc[:, 0], aligned.iloc[:, 1]


def read_portfolio_briefing() -> dict:
    """Return a compact, read-only portfolio summary for the Hermes risk desk."""
    try:
        with db.engine.connect() as conn:
            legs = conn.execute(text("""
                SELECT symbol, side, status FROM positions;
            """)).mappings().all()
    except Exception as exc:
        logger.warning("Portfolio briefing query failed: %s", exc)
        return {"open_leg_count": 0, "closed_leg_count": 0, "exposure_map": [], "data_available": False}

    net_units: dict[str, int] = {}
    open_leg_count = 0
    closed_leg_count = 0
    for leg in legs:
        status = str(leg.get("status") or "").upper()
        if status == "CLOSED":
            closed_leg_count += 1
        if status != "OPEN":
            continue
        open_leg_count += 1
        symbol = str(leg.get("symbol") or "").strip()
        side = str(leg.get("side") or "").upper()
        if not symbol or side not in {"BUY", "SELL"}:
            continue
        net_units[symbol] = net_units.get(symbol, 0) + (1 if side == "BUY" else -1)

    exposure_map = []
    for symbol, units in sorted(net_units.items()):
        net_side = "LONG" if units > 0 else "SHORT" if units < 0 else "NEUTRAL"
        exposure_map.append({"symbol": symbol, "net_side": net_side, "count": abs(units)})
    return {
        "open_leg_count": open_leg_count,
        "closed_leg_count": closed_leg_count,
        "exposure_map": exposure_map,
        "data_available": True,
    }


def _read_exposure_map() -> list[dict]:
    """Keep the dashboard heatmap bound to the same portfolio summary contract."""
    return read_portfolio_briefing()["exposure_map"]


def _recent_gatekeeper_rejections(request: Request) -> list[dict]:
    """Read the bounded worker audit buffer; absence during isolated tests is benign."""
    engine = getattr(request.app.state, "execution_engine", None)
    if engine is None:
        return []
    try:
        return engine.recent_gatekeeper_rejections()
    except Exception as exc:
        logger.warning("Gatekeeper audit read failed: %s", exc)
        return []

# ── Session helpers ───────────────────────────────────────────────────────────
def _serializer() -> URLSafeTimedSerializer:
    if not settings.SESSION_SECRET:
        raise RuntimeError("SESSION_SECRET is not set in .env")
    return URLSafeTimedSerializer(settings.SESSION_SECRET)

def _create_session(username: str) -> str:
    return _serializer().dumps(username)

def _verify_session(token: str) -> str | None:
    try:
        return _serializer().loads(token, max_age=_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None

# ── Rate limiter ──────────────────────────────────────────────────────────────
limiter = Limiter(key_func=get_remote_address)

# ── App ───────────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(application: FastAPI):
    """Own live components explicitly and release their threads on shutdown."""
    _validate_security_configuration()
    if settings.WEB_CONCURRENCY != 1:
        logger.critical(
            "[DEPLOYMENT BLOCKED] WEB_CONCURRENCY=%s; in-process execution "
            "requires exactly one worker.",
            settings.WEB_CONCURRENCY,
        )
        raise RuntimeError("Alpha Quant Bot requires WEB_CONCURRENCY=1 for in-process execution safety.")
    application.state.hermes_hook = HermesAuditorHook()
    application.state.execution_engine = ExecutionEngine(telemetry=application.state.hermes_hook)
    ollama_client = OllamaClient(
        model=settings.OLLAMA_MODEL,
        desk_read_timeout_s=settings.OLLAMA_DESK_READ_TIMEOUT_S,
    ) if settings.OLLAMA_ENABLED else None
    application.state.hermes_service = HermesService(
        price_reader=read_pair_prices,
        ollama_client=ollama_client,
        portfolio_reader=read_portfolio_briefing,
    )
    application.state.worker_stop = threading.Event()
    application.state.hermes_service.start()
    application.state.worker_thread = threading.Thread(
        target=_background_worker, args=(application,), name="alpha-quant-worker", daemon=True
    )
    application.state.worker_thread.start()
    try:
        yield
    finally:
        application.state.worker_stop.set()
        worker = getattr(application.state, "worker_thread", None)
        if worker is not None:
            worker.join(5.0)
        application.state.hermes_hook.stop(timeout=5.0)
        application.state.hermes_service.stop(timeout=5.0)


app = FastAPI(title="Alpha Quant", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

static_path = os.path.join(BASE_DIR, "static")
if os.path.exists(static_path):
    app.mount("/static", StaticFiles(directory=static_path), name="static")


# ── Auth middleware — runs on every request ───────────────────────────────────
@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    public = {"/login", "/favicon.ico", "/health"}
    if request.url.path in public or request.url.path.startswith("/static/"):
        return await call_next(request)

    token    = request.cookies.get(_COOKIE)
    username = _verify_session(token) if token else None
    if not username:
        return RedirectResponse(url="/login", status_code=302)

    return await call_next(request)


# ── Global exception middleware ───────────────────────────────────────────────
@app.middleware("http")
async def exception_middleware(request: Request, call_next):
    try:
        return await call_next(request)
    except Exception as exc:
        logger.error("Unhandled exception: %s", exc, exc_info=(settings.LOG_LEVEL == "DEBUG"))
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"status": "error", "message": "An unexpected error occurred."},
        )


# ── Startup: background worker ────────────────────────────────────────────────
def _background_worker(application: FastAPI):
    logger.info("[WORKER] Background worker thread active and starting routine...")
    # Give a just-started app a brief, interruptible shutdown window before the
    # first full-universe statistical scan begins.
    if application.state.worker_stop.wait(0.25):
        return
    while not application.state.worker_stop.is_set():
        try:
            logger.info("[WORKER] Routine cycle started: Checking positions & market scan...")
            _refresh_daily_prices_if_due(application)
            _refresh_hourly_prices_if_due(application)
            # Built once and shared: manage_orders_and_positions and the desk
            # scan would otherwise each run an identical daily+intraday query
            # and pivot against the full universe every 60s cycle.
            price_matrix = signals_hub._build_price_matrix()
            application.state.execution_engine.manage_orders_and_positions(price_matrix)
            opps = signals_hub.scan_instant_opportunities(force_refresh=True, price_matrix=price_matrix)
            logger.info("[SCAN] Evaluated candidates count: %d", len(opps) if opps else 0)
            for opportunity in opps or []:
                if isinstance(opportunity, dict):
                    pair = opportunity.get("pair", "UNKNOWN")
                    z_score = opportunity.get("z_score", "N/A")
                else:
                    pair = getattr(opportunity, "pair", "UNKNOWN")
                    z_score = getattr(opportunity, "z_score", "N/A")
                logger.info("[SCAN] Candidate %s | Z-score=%s", pair, z_score)
        except Exception as e:
            logger.error("Worker cycle error: %s", e)
        application.state.worker_stop.wait(settings.WORKER_CYCLE_SECONDS)


# ── Login / Logout ────────────────────────────────────────────────────────────
@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse(request=request, name="login.html", context={})

@app.post("/login")
@limiter.limit("5/minute")
async def login_submit(request: Request):
    form     = await request.form()
    username = str(form.get("username", ""))
    password = str(form.get("password", ""))

    valid_user = secrets.compare_digest(username.encode(), settings.APP_USERNAME.encode())
    # Keep login fail-closed even if a caller bypasses the ASGI lifespan in a
    # test harness or a nonstandard deployment.
    valid_pass = bool(settings.APP_PASSWORD) and secrets.compare_digest(
        password.encode(), settings.APP_PASSWORD.encode()
    )

    if not (valid_user and valid_pass):
        logger.warning("Failed login attempt for username: %s", username)
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"error": "Invalid username or password."},
            status_code=401,
        )

    response = RedirectResponse(url="/", status_code=302)
    response.set_cookie(
        key=_COOKIE,
        value=_create_session(username),
        httponly=True,
        secure=settings.SESSION_COOKIE_SECURE,
        samesite="strict",
        max_age=_MAX_AGE,
    )
    logger.info("User '%s' logged in.", username)
    return response

@app.get("/logout")
def logout():
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie(_COOKIE)
    return response


# ── Dashboard ─────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
@limiter.limit("20/minute")
def dashboard(request: Request):
    exposure_map = _read_exposure_map()
    rejection_logs = _recent_gatekeeper_rejections(request)
    try:
        opportunities = signals_hub.get_cached_opportunities()
        metrics = analytics_manager.generate_report(save_csv=False)
    except Exception as e:
        logger.error("Dashboard data fetch failed: %s", e)
        opportunities = []
        metrics = {"Total PnL": "$0.00", "Win Rate": "0.00%",
                     "Profit Factor": "0.00", "Max Drawdown": "$0.00"}

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "opportunities": opportunities,
            "total_opportunities": len(opportunities),
            "metrics": metrics,
            "exposure_map": exposure_map,
            "rejection_logs": rejection_logs,
        },
    )


# ── Positions ─────────────────────────────────────────────────────────────────
@app.get("/positions", response_class=HTMLResponse)
@limiter.limit("20/minute")
def positions(request: Request):
    trades     = []
    total_pnl  = 0.0
    try:
        with db.engine.connect() as conn:
            trades = conn.execute(
                text("""
                    SELECT strategy_name, symbol, side, entry_price, current_price,
                           status, opened_at, closed_at, unrealized_pnl
                    FROM positions ORDER BY opened_at DESC;
                """)
            ).mappings().fetchall()
        total_pnl = sum(float(row["unrealized_pnl"] or 0.0) for row in trades)
        trades = [{
            "pair": row["strategy_name"] or row["symbol"],
            "action": row["side"],
            "entry_time": row["opened_at"],
            "entry_z_score": "--",
            "price_a_entry": row["entry_price"],
            "price_b_entry": "--",
            "price_a_exit": row["current_price"] if row["status"] == "CLOSED" else None,
            "price_b_exit": "--",
            "status": row["status"],
            "pnl": row["unrealized_pnl"],
        } for row in trades]
    except Exception as e:
        logger.error("Positions query failed: %s", e)

    return templates.TemplateResponse(
        request=request,
        name="positions.html",
        context={"trades": trades, "total_pnl": round(total_pnl, 2)},
    )


# ── Health ────────────────────────────────────────────────────────────────────
@app.get("/health")
@limiter.limit("30/minute")
async def health(request: Request):
    try:
        with db.engine.connect() as conn:
            conn.execute(text("SELECT 1;"))
        db_status = "CONNECTED"
    except Exception:
        db_status = "DISCONNECTED"
    return {
        "status":    "OPERATIONAL" if db_status == "CONNECTED" else "DEGRADED",
        "database":  db_status,
        "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


# ── API: raw signals ──────────────────────────────────────────────────────────
@app.get("/api/signals")
@limiter.limit("10/minute")
async def api_signals(request: Request):
    return signals_hub.get_cached_opportunities()


@app.get("/api/hermes/desk-briefing")
@limiter.limit("30/minute")
async def hermes_desk_briefing(request: Request, lookback_hours: int = 24):
    """Return a failure-isolated Hermes risk-desk briefing for the dashboard."""
    service = getattr(request.app.state, "hermes_service", None)
    if service is None:
        return {
            "briefing": "Ollama çevrimdışı - Salt Veri Özeti: 0 ret, 0 alarm",
            "timestamp": datetime.datetime.now(datetime.UTC).isoformat().replace("+00:00", "Z"),
            "metrics_summary": {"lookback_hours": lookback_hours, "service_available": False},
        }
    try:
        return service.generate_desk_briefing(lookback_hours=lookback_hours)
    except ValueError as exc:
        return JSONResponse(status_code=422, content={"detail": str(exc)})
    except Exception:
        logger.exception("Hermes desk briefing failed")
        return {
            "briefing": "Ollama çevrimdışı - Salt Veri Özeti: 0 ret, 0 alarm",
            "timestamp": datetime.datetime.now(datetime.UTC).isoformat().replace("+00:00", "Z"),
            "metrics_summary": {"lookback_hours": lookback_hours, "service_available": False},
        }
