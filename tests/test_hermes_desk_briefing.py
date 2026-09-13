import asyncio
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from hermes.audit_ledger import AuditLedger
from hermes.models import AuditEvent, AuditEventKind
from hermes.service import HermesService


class HermesDeskBriefingTests(unittest.TestCase):
    def test_ledger_summary_ollama_fallback_and_endpoint_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "briefing.db")
            timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            with AuditLedger(path) as ledger:
                ledger.append(AuditEvent(
                    "rejection-1", AuditEventKind.PAIR_ORDER_TERMINAL, timestamp,
                    {"type": "gatekeeper_rejection", "pair": "AAA / BBB", "reason": "OU half-life threshold failed"},
                ))
                ledger.append(AuditEvent(
                    "alert-1", AuditEventKind.STABILITY_ALERT, timestamp,
                    {"pair": "AAA / BBB", "active": True, "report": {"rejection_reasons": ["COINTEGRATION_PVALUE"]}},
                ))

            portfolio = {"open_leg_count": 2, "closed_leg_count": 4,
                         "exposure_map": [{"symbol": "AAA", "net_side": "LONG", "count": 1}]}
            client = MagicMock()
            client.generate_desk_briefing.return_value = "- Portföy Sağlığı: İzlenmeli"
            service = HermesService(ledger_path=path, portfolio_reader=lambda: portfolio, ollama_client=client)
            result = service.generate_desk_briefing()
            self.assertEqual(result["briefing"], "- Portföy Sağlığı: İzlenmeli")
            self.assertEqual(result["metrics_summary"]["gatekeeper_rejections"], 1)
            self.assertEqual(result["metrics_summary"]["active_stability_alerts"], 1)
            self.assertEqual(result["metrics_summary"]["portfolio"], portfolio)
            client.generate_desk_briefing.assert_called_once()

            fallback = HermesService(ledger_path=path, portfolio_reader=lambda: portfolio).generate_desk_briefing()
            self.assertEqual(fallback["briefing"], "Ollama çevrimdışı - Salt Veri Özeti: 1 ret, 1 alarm")

            try:
                import web_app.app as web
                handler = getattr(web.hermes_desk_briefing, "__wrapped__", web.hermes_desk_briefing)
                request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(hermes_service=service)))
                response = asyncio.run(handler(request))
                self.assertEqual(response["briefing"], result["briefing"])
                self.assertIn("timestamp", response)
                self.assertIn("metrics_summary", response)
            finally:
                # Preserve the research suite's guarantee that importing its
                # pure modules never depends on web/database configuration.
                for module in ("web_app.app", "execution.execution_engine", "config.database",
                               "config.settings", "dotenv", "core.analytics", "core.signal_generator"):
                    sys.modules.pop(module, None)
