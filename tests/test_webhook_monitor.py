from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from ai_code_agent.metrics import persist_execution_metrics
from ai_code_agent.webhook import _monitor_frontend_url, _monitor_payload

try:
    from fastapi.testclient import TestClient
    from ai_code_agent.webhook import app
except ImportError:  # pragma: no cover - optional dependency
    TestClient = None
    app = None


class WebhookMonitorTest(unittest.TestCase):
    def test_monitor_payload_returns_latest_run_and_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            screenshots_dir = Path(temp_dir) / ".ai-code-agent" / "visual-review" / "screenshots"
            screenshots_dir.mkdir(parents=True, exist_ok=True)
            (screenshots_dir / "home.png").write_bytes(b"png-bytes")
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
                "planning": {"plan_summary": "Upgrade the monitor UI and keep the API contract stable.", "retrieval_strategy": "hybrid"},
                "testing": {"validation_strategy": "targeted_retry"},
                "review": {"status": "changes_required", "approved": False, "remediation_required": True, "remediation": {"guidance": ["Fix the failing typecheck."], "focus_areas": ["monitor_frontend/src/App.tsx"]}},
                "create_pr": {"outcome": "skipped", "reason": "review_not_approved", "provider": "github", "message": "Skipped PR creation until validation passes."},
                "phases": {
                    "test": {"status": "in_progress", "attempts": 1, "duration_ms": 0},
                },
                "execution_events": [
                    {"node": "test", "event_type": "node_started", "attempt": 1, "status": "started", "timestamp": "2026-03-08T18:45:04Z"}
                ],
                "testing": {
                    "validation_strategy": "targeted_retry",
                    "commands": [{"label": "typescript:noEmit", "exit_code": 1, "duration_ms": 2200, "mode": "local"}],
                    "visual_review": {"screenshot_status": "passed", "artifact_count": 1},
                },
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
            self.assertEqual(payload["phase_details"]["test"]["title"], "Tester agent")
            self.assertIn("Validation strategy: targeted_retry", payload["phase_details"]["test"]["inputs"])
            self.assertIn("Plan summary: Upgrade the monitor UI and keep the API contract stable.", payload["phase_details"]["plan"]["outputs"])
            self.assertIn("Remediation guidance: Fix the failing typecheck.", payload["phase_details"]["review"]["outputs"])
            self.assertIn("Command: typescript:noEmit (exit=1, duration_ms=2200, mode=local)", payload["phase_details"]["test"]["outputs"])
            self.assertIn("Screenshot status: passed", payload["phase_details"]["test"]["outputs"])
            self.assertIn("Screenshot artifacts: 1", payload["phase_details"]["test"]["outputs"])
            self.assertIn("Message: Skipped PR creation until validation passes.", payload["phase_details"]["create_pr"]["outputs"])
            self.assertEqual(len(payload["phase_details"]["test"]["images"]), 1)
            self.assertIn("/api/monitor/artifact?repo=", payload["phase_details"]["test"]["images"][0]["url"])
            self.assertEqual(payload["phase_details"]["review"]["images"][0]["path"], ".ai-code-agent/visual-review/screenshots/home.png")
            self.assertEqual(payload["trend"]["run_count"], 2)
            self.assertIn("generated_at", payload)

    @unittest.skipIf(TestClient is None or app is None, "fastapi test client unavailable")
    def test_monitor_routes_redirect_and_return_json(self) -> None:
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

            redirect_response = client.get("/monitor", params={"repo": temp_dir, "recent": 5}, follow_redirects=False)
            self.assertEqual(redirect_response.status_code, 307)
            self.assertEqual(redirect_response.headers["location"], _monitor_frontend_url(repo=temp_dir, recent=5))

            api_response = client.get("/api/monitor", params={"repo": temp_dir, "recent": 5})
            self.assertEqual(api_response.status_code, 200)
            payload = api_response.json()
            self.assertEqual(payload["latest"]["run_id"], "run-123")
            self.assertEqual(payload["latest"]["workflow"]["active_node"], "code")
            self.assertEqual(payload["rows"][0]["status"], "running")

    @unittest.skipIf(TestClient is None or app is None, "fastapi test client unavailable")
    def test_monitor_artifact_route_serves_visual_review_image(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / ".ai-code-agent" / "visual-review" / "screenshots" / "demo.png"
            image_path.parent.mkdir(parents=True, exist_ok=True)
            image_path.write_bytes(b"png-bytes")

            client = TestClient(app)
            response = client.get("/api/monitor/artifact", params={"repo": temp_dir, "path": ".ai-code-agent/visual-review/screenshots/demo.png"})

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.content, b"png-bytes")
            self.assertEqual(response.headers["content-type"], "image/png")


if __name__ == "__main__":
    unittest.main()
