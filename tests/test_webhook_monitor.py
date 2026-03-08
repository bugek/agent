from __future__ import annotations

import os
import tempfile
import unittest

from ai_code_agent.metrics import persist_execution_metrics
from ai_code_agent.webhook import _monitor_payload

try:
    from fastapi.testclient import TestClient
    from ai_code_agent.webhook import app
except ImportError:  # pragma: no cover - optional dependency
    TestClient = None
    app = None


class WebhookMonitorTest(unittest.TestCase):
    def test_monitor_payload_returns_latest_run_and_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            first_metrics = {
                "schema_version": "execution-metrics/v1",
                "run_id": "run-1",
                "workflow": {"status": "approved", "duration_ms": 90, "terminal_node": "review", "active_node": None},
                "failures": {"primary_category": None, "subcategory": None},
                "testing": {"validation_strategy": "full"},
                "phases": {},
                "execution_events": [],
            }
            second_metrics = {
                "schema_version": "execution-metrics/v1",
                "run_id": "run-2",
                "workflow": {"status": "running", "duration_ms": 120, "terminal_node": "code", "active_node": "test"},
                "failures": {"primary_category": "validation", "subcategory": "command:typescript:noEmit"},
                "testing": {"validation_strategy": "targeted_retry"},
                "phases": {
                    "test": {"status": "in_progress", "attempts": 1, "duration_ms": 0},
                },
                "execution_events": [
                    {"node": "test", "event_type": "node_started", "attempt": 1, "status": "started", "timestamp": "2026-03-08T18:45:04Z"}
                ],
            }

            first_path = persist_execution_metrics(temp_dir, "run-1", first_metrics)
            second_path = persist_execution_metrics(temp_dir, "run-2", second_metrics)
            self.assertIsNotNone(first_path)
            self.assertIsNotNone(second_path)
            os.utime(f"{temp_dir}\\{first_path}", (100, 100))
            os.utime(f"{temp_dir}\\{second_path}", (200, 200))

            payload = _monitor_payload(temp_dir, 5)

            self.assertEqual(payload["workspace_dir"], temp_dir)
            self.assertEqual(payload["latest"]["run_id"], "run-2")
            self.assertEqual(payload["latest"]["workflow"]["active_node"], "test")
            self.assertEqual(payload["rows"][0]["run_id"], "run-2")
            self.assertEqual(payload["rows"][0]["failure_subcategory"], "command:typescript:noEmit")
            self.assertEqual(payload["trend"]["run_count"], 2)
            self.assertIn("generated_at", payload)

    @unittest.skipIf(TestClient is None or app is None, "fastapi test client unavailable")
    def test_monitor_routes_render_ui_and_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            metrics = {
                "schema_version": "execution-metrics/v1",
                "run_id": "run-123",
                "workflow": {"status": "running", "duration_ms": 42, "terminal_node": "code", "active_node": "code"},
                "failures": {"primary_category": None, "subcategory": None},
                "testing": {"validation_strategy": "full"},
                "phases": {
                    "code": {"status": "in_progress", "attempts": 1, "duration_ms": 0},
                },
                "execution_events": [
                    {"node": "code", "event_type": "node_started", "attempt": 1, "status": "started", "timestamp": "2026-03-08T18:45:04Z"}
                ],
            }
            persist_execution_metrics(temp_dir, "run-123", metrics)
            client = TestClient(app)

            html_response = client.get("/monitor")
            self.assertEqual(html_response.status_code, 200)
            self.assertIn("AI Code Agent Monitor", html_response.text)

            api_response = client.get("/api/monitor", params={"repo": temp_dir, "recent": 5})
            self.assertEqual(api_response.status_code, 200)
            payload = api_response.json()
            self.assertEqual(payload["latest"]["run_id"], "run-123")
            self.assertEqual(payload["latest"]["workflow"]["active_node"], "code")
            self.assertEqual(payload["rows"][0]["status"], "running")


if __name__ == "__main__":
    unittest.main()
