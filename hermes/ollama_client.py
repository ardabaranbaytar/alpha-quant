"""Failure-isolated localhost client for concise Hermes diagnoses.

This module deliberately receives only audit scalars.  It has no configuration
or environment dependency and cannot expose trading credentials to the model.
"""

from enum import Enum
import json
import logging
import re
import threading
import time

import requests


logger = logging.getLogger(__name__)


class CircuitState(str, Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class OllamaClient:
    _PAIR_PATTERN = re.compile(r"^[A-Z0-9._-]+ / [A-Z0-9._-]+$")

    def __init__(self, base_url="http://127.0.0.1:11434", model="qwen3:4b-instruct", connect_timeout_s=2.0,
                 read_timeout_s=5.0, desk_read_timeout_s=90.0, failure_threshold=3, cooldown_s=120):
        self.base_url = str(base_url).rstrip("/")
        self.model = str(model)
        self.connect_timeout_s = float(connect_timeout_s)
        self.read_timeout_s = float(read_timeout_s)
        self.desk_read_timeout_s = float(desk_read_timeout_s)
        self.failure_threshold = int(failure_threshold)
        self.cooldown_s = float(cooldown_s)
        if (self.failure_threshold <= 0 or self.connect_timeout_s <= 0 or self.read_timeout_s <= 0
                or self.desk_read_timeout_s < 45 or self.cooldown_s < 0):
            raise ValueError("Ollama client thresholds and timeouts must be positive")
        self.state = CircuitState.CLOSED
        self.consecutive_failures = 0
        self._opened_at = None
        self._half_open_in_flight = False
        self._lock = threading.Lock()

    @staticmethod
    def _metric(value: float | None) -> float | None:
        if value is None:
            return None
        return float(value)

    def _begin_request(self) -> bool:
        now = time.monotonic()
        with self._lock:
            if self.state is CircuitState.OPEN:
                if now - self._opened_at < self.cooldown_s:
                    return False
                self.state = CircuitState.HALF_OPEN
            if self.state is CircuitState.HALF_OPEN:
                if self._half_open_in_flight:
                    return False
                self._half_open_in_flight = True
            return True

    def _succeed(self) -> None:
        with self._lock:
            self.state = CircuitState.CLOSED
            self.consecutive_failures = 0
            self._opened_at = None
            self._half_open_in_flight = False

    def _fail(self) -> None:
        now = time.monotonic()
        with self._lock:
            self._half_open_in_flight = False
            self.consecutive_failures += 1
            if self.state is CircuitState.HALF_OPEN or self.consecutive_failures >= self.failure_threshold:
                self.state = CircuitState.OPEN
                self._opened_at = now

    def _generate(self, prompt: str, read_timeout_s: float | None = None,
                  error_label: str = "Ollama generation error", options: dict | None = None) -> str | None:
        """Use the shared circuit breaker for a bounded local text generation."""
        if not self._begin_request():
            return None
        try:
            payload = {"model": self.model, "stream": False, "prompt": prompt}
            if options is not None:
                payload["options"] = options
            response = requests.post(
                f"{self.base_url}/api/generate",
                json=payload,
                timeout=(self.connect_timeout_s, read_timeout_s or self.read_timeout_s),
            )
            if response.status_code != 200:
                logger.warning("%s: HTTP %s", error_label, response.status_code)
                self._fail()
                return None
            body = response.json()
            response_text = body.get("response") if isinstance(body, dict) else None
            if not isinstance(response_text, str) or not response_text.strip():
                self._fail()
                return None
        except Exception as exc:
            logger.warning("%s: %s - %s", error_label, type(exc).__name__, str(exc))
            self._fail()
            return None
        self._succeed()
        return response_text.strip()

    def diagnose(self, *, pair: str, coint_pvalue: float | None, adf_pvalue: float | None,
                 ou_half_life: float | None, hurst: float | None,
                 rejection_reasons: tuple[str, ...], recent_event_kind: str) -> str | None:
        """Return a best-effort concise diagnosis, never an operational failure."""
        if not isinstance(pair, str) or not self._PAIR_PATTERN.fullmatch(pair):
            return None
        metrics = {
            "pair": pair,
            "coint_pvalue": self._metric(coint_pvalue),
            "adf_pvalue": self._metric(adf_pvalue),
            "ou_half_life": self._metric(ou_half_life),
            "hurst": self._metric(hurst),
            "rejection_reasons": [str(reason) for reason in rejection_reasons],
            "recent_event_kind": str(recent_event_kind),
        }
        return self._generate(
            "Provide a concise stability diagnosis from these audit scalars: "
            + json.dumps(metrics, sort_keys=True, separators=(",", ":"), allow_nan=False)
        )

    def generate_desk_briefing(self, *, summary: dict) -> str | None:
        """Produce a bounded Turkish desk briefing from already-aggregated data."""
        if not isinstance(summary, dict):
            return None
        try:
            serialized = json.dumps(summary, sort_keys=True, separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError):
            return None
        return self._generate(
            "Aşağıdaki sadece risk özeti verisine dayanarak kısa, kurumsal ve Türkçe "
            "madde işaretli bir risk masası brifingi yaz. Tam olarak şu başlıkları kullan: "
            "Portföy Sağlığı, En Çok Reddedilen Kapılar, Dikkat Edilmesi Gereken Çiftler, "
            "Aksiyon Önerisi. Veri dışında varsayım yapma. Özet: " + serialized,
            read_timeout_s=self.desk_read_timeout_s,
            error_label="Ollama desk briefing error",
            options={"temperature": 0.2, "num_predict": 650},
        )
