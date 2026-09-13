import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests

from hermes.ollama_client import CircuitState, OllamaClient


class OllamaClientTests(unittest.TestCase):
    def _diagnose(self, client):
        return client.diagnose(
            pair="AAA / BBB", coint_pvalue=0.12, adf_pvalue=0.08,
            ou_half_life=25.0, hurst=0.55, rejection_reasons=("COINT_PVALUE",),
            recent_event_kind="PAIR_ORDER_TERMINAL",
        )

    def test_success_posts_scalar_payload_and_returns_diagnosis(self):
        response = MagicMock(status_code=200)
        response.json.return_value = {"response": "Mean reversion has weakened."}
        client = OllamaClient()
        self.assertEqual(client.desk_read_timeout_s, 90.0)
        with patch("hermes.ollama_client.requests.post", return_value=response) as post:
            self.assertEqual(self._diagnose(client), "Mean reversion has weakened.")
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["model"], "qwen3:4b-instruct")
        self.assertFalse(payload["stream"])
        self.assertIn("AAA / BBB", payload["prompt"])
        self.assertEqual(post.call_args.kwargs["timeout"], (2.0, 5.0))

        with patch("hermes.ollama_client.requests.post", return_value=response) as desk_post:
            self.assertEqual(
                client.generate_desk_briefing(summary={"gatekeeper_rejections": 0}),
                "Mean reversion has weakened.",
            )
        self.assertEqual(desk_post.call_args.args[0], "http://127.0.0.1:11434/api/generate")
        desk_payload = desk_post.call_args.kwargs["json"]
        self.assertEqual(desk_payload["model"], "qwen3:4b-instruct")
        self.assertFalse(desk_payload["stream"])
        self.assertIn("prompt", desk_payload)
        self.assertEqual(desk_payload["options"], {"temperature": 0.2, "num_predict": 650})
        self.assertEqual(desk_post.call_args.kwargs["timeout"], (2.0, 90.0))

    def test_connection_failure_returns_none_and_increments_counter(self):
        client = OllamaClient()
        with patch("hermes.ollama_client.requests.post", side_effect=requests.Timeout):
            self.assertIsNone(self._diagnose(client))
        self.assertEqual(client.consecutive_failures, 1)
        self.assertIs(client.state, CircuitState.CLOSED)

    def test_three_failures_open_circuit_and_skip_http(self):
        client = OllamaClient()
        with patch("hermes.ollama_client.requests.post", side_effect=requests.ConnectionError) as post:
            for _ in range(3):
                self.assertIsNone(self._diagnose(client))
            self.assertIs(client.state, CircuitState.OPEN)
            self.assertIsNone(self._diagnose(client))
        self.assertEqual(post.call_count, 3)

    def test_cooldown_half_open_probe_closes_circuit_on_success(self):
        response = MagicMock(status_code=200)
        response.json.return_value = {"response": "Recovered"}
        client = OllamaClient(cooldown_s=0)
        with patch("hermes.ollama_client.requests.post", side_effect=requests.ConnectionError):
            for _ in range(3):
                self._diagnose(client)
        with patch("hermes.ollama_client.requests.post", return_value=response) as post:
            self.assertEqual(self._diagnose(client), "Recovered")
        self.assertEqual(post.call_count, 1)
        self.assertIs(client.state, CircuitState.CLOSED)
        self.assertEqual(client.consecutive_failures, 0)

    def test_module_has_no_configuration_or_environment_access(self):
        source = Path("hermes/ollama_client.py").read_text(encoding="utf-8")
        for forbidden in ("os.environ", "config.database", "config.settings"):
            self.assertNotIn(forbidden, source)
