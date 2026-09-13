"""Data-refresh tasks called directly by the web worker loop (web_app/app.py).

A separate APScheduler/BlockingScheduler cron path used to live here but was
never wired into the running app -- the in-process worker thread is the only
thing that actually triggers these two tasks, on an hourly/daily cadence it
tracks itself. Removed rather than kept "for later" to avoid re-introducing
that dead path.
"""
import datetime
import logging

from .yfinance_fetcher import fetcher

logger = logging.getLogger(__name__)


def hourly_price_task():
    logger.info("Hourly bar update triggered at %s", datetime.datetime.now())
    fetcher.inject_hourly_bars(period="3d")


def daily_price_task() -> bool:
    """Refresh recent daily closes without allowing provider failures to escape."""
    logger.info("Daily bar update triggered at %s", datetime.datetime.now())
    try:
        # A short overlap captures delayed corrected closes while avoiding a
        # full-history download in the production worker every trading day.
        fetcher.inject_daily_bars(period="5d")
    except Exception:
        logger.exception("Daily price refresh failed; the next scheduled sync will retry")
        return False
    return True
