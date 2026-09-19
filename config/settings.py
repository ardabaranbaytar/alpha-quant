import os
from typing import ClassVar

from dotenv import load_dotenv

load_dotenv()


def _environment_bool(name: str, default: bool) -> bool:
    """Read an explicitly configured boolean without silently weakening security."""
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"{name} must be a boolean value (true or false)")


class Settings:
    # Database
    DB_DIALECT = os.getenv("DB_DIALECT", "mysql")
    DB_DRIVER = os.getenv("DB_DRIVER", "pymysql")
    DB_USER = os.getenv("DB_USER", "root")
    DB_PASSWORD = os.getenv("DB_PASS", "")
    DB_HOST = os.getenv("DB_HOST", "localhost")
    DB_PORT = int(os.getenv("DB_PORT", "3306"))
    DB_NAME = os.getenv("DB_NAME", "alpha_quant")

    # App
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
    # The in-process execution worker and Hermes hook are singleton services.
    # Deploy this web process with one worker; additional workers can submit the
    # same paper orders concurrently because no cross-process lease exists yet.
    WEB_CONCURRENCY = int(os.getenv("WEB_CONCURRENCY", "1"))
    SCAN_CACHE_TTL_SECONDS = float(os.getenv("SCAN_CACHE_TTL_SECONDS", "60"))
    WORKER_CYCLE_SECONDS = int(os.getenv("WORKER_CYCLE_SECONDS", "180"))
    if WORKER_CYCLE_SECONDS <= 0:
        raise RuntimeError("WORKER_CYCLE_SECONDS must be a positive integer")
    # Daily bars can legitimately be several calendar days old over weekends,
    # holidays, and while an off-session backfill is still in progress.
    MAX_PRICE_AGE_DAYS: int = int(os.getenv("MAX_PRICE_AGE_DAYS", "14"))

    # The desk uses the same institutional entry threshold as the canonical
    # signal model; this remains display-only and does not alter execution.
    DASHBOARD_Z_THRESHOLD = float(os.getenv("DASHBOARD_Z_THRESHOLD", "2.0"))
    DASHBOARD_MAX_CANDIDATES = int(os.getenv("DASHBOARD_MAX_CANDIDATES", "5"))

    # Hermes never attempts localhost model calls unless this is explicitly on.
    OLLAMA_ENABLED = os.getenv("OLLAMA_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
    OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3:4b-instruct")
    # A local desk model can need longer than a one-pair diagnosis.  This
    # timeout is used only for the on-demand desk briefing.
    OLLAMA_DESK_READ_TIMEOUT_S = float(os.getenv("OLLAMA_DESK_READ_TIMEOUT_S", "90"))

    # Execution owns this decision.  Hermes only publishes the stability alert
    # which makes the controls safe to leave enabled by default.
    ENABLE_AUTO_DERISKING = os.getenv("ENABLE_AUTO_DERISKING", "true").strip().lower() in {"1", "true", "yes", "on"}
    DERISK_ON_STABILITY_ALERT = os.getenv("DERISK_ON_STABILITY_ALERT", "true").strip().lower() in {"1", "true", "yes", "on"}

    APP_USERNAME = os.getenv("APP_USERNAME", "admin")
    APP_PASSWORD = os.getenv("APP_PASSWORD", "")
    SESSION_SECRET = os.getenv("SESSION_SECRET", "")
    # HTTPS-only is the safe default in every environment.  Local HTTP use is
    # possible only through an explicit, valid configuration override.
    SESSION_COOKIE_SECURE = _environment_bool("SESSION_COOKIE_SECURE", default=True)

    ALLOWED_ORIGINS: ClassVar[list[str]] = [
        origin.strip()
        for origin in os.getenv(
            "ALLOWED_ORIGINS",
            "http://localhost:8000",
        ).split(",")
        if origin.strip()
    ]


settings = Settings()
