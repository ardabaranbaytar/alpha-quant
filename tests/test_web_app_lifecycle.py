import datetime
import time
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient


class _Hook:
    def __init__(self, application):
        self.application = application
        self.worker_stopped_when_hook_stopped = False

    def emit(self, event):
        pass

    def stop(self, timeout=5.0):
        self.worker_stopped_when_hook_stopped = not self.application.state.worker_thread.is_alive()
        self.stopped = True


class _Service:
    def start(self):
        self.started = True

    def stop(self, timeout=5.0):
        self.stopped = True


class _Engine:
    def __init__(self, telemetry=None):
        self.telemetry = telemetry

    def manage_orders_and_positions(self, price_matrix=None):
        return None


class WebAppLifecycleTests(unittest.TestCase):
    def test_daily_refresh_runs_once_after_us_market_close_and_is_failure_isolated(self):
        import web_app.app as web

        application = FastAPI()
        after_close = datetime.datetime(2026, 9, 11, 20, 15, tzinfo=datetime.UTC)
        with patch.object(web, "daily_price_task", return_value=True) as refresh:
            self.assertTrue(web._refresh_daily_prices_if_due(application, after_close))
            self.assertFalse(web._refresh_daily_prices_if_due(application, after_close))
        refresh.assert_called_once_with()

        failed_application = FastAPI()
        with patch.object(web, "daily_price_task", side_effect=TimeoutError("provider timeout")):
            self.assertFalse(web._refresh_daily_prices_if_due(failed_application, after_close))

    def test_lifespan_starts_and_stops_threads_promptly(self):
        import web_app.app as web

        hook = _Hook(web.app)
        service = _Service()
        started = time.monotonic()
        with patch.object(web, "HermesAuditorHook", return_value=hook), \
             patch.object(web, "HermesService", return_value=service), \
             patch.object(web, "ExecutionEngine", _Engine), TestClient(web.app):
            self.assertTrue(web.app.state.worker_thread.is_alive())
            self.assertIs(web.app.state.execution_engine.telemetry, hook)
            self.assertIs(web.app.state.hermes_service, service)
        self.assertLess(time.monotonic() - started, 2.0)
        self.assertTrue(web.app.state.worker_stop.is_set())
        self.assertFalse(web.app.state.worker_thread.is_alive())
        self.assertTrue(hook.worker_stopped_when_hook_stopped)
        self.assertTrue(hook.stopped)
        self.assertTrue(service.stopped)
