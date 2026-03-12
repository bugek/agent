from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from ai_code_agent.metrics import persist_execution_metrics
from ai_code_agent.webhook import _monitor_cors_origins, _monitor_frontend_url, _monitor_payload

try:
    from fastapi.testclient import TestClient
    from ai_code_agent.webhook import app
except ImportError:  # pragma: no cover - optional dependency
    TestClient = None
    app = None


class WebhookMonitorTest(unittest.TestCase):
    def test_monitor_cors_origins_include_configured_frontends(self) -> None:
        original_frontend_url = os.environ.get("MONITOR_FRONTEND_URL")
        original_frontend_origins = os.environ.get("MONITOR_FRONTEND_ORIGINS")
        try:
            os.environ["MONITOR_FRONTEND_URL"] = "http://127.0.0.1:4174"
            os.environ["MONITOR_FRONTEND_ORIGINS"] = "http://localhost:4175,http://127.0.0.1:4174"
            origins = _monitor_cors_origins()
        finally:
            if original_frontend_url is None:
                os.environ.pop("MONITOR_FRONTEND_URL", None)
            else:
                os.environ["MONITOR_FRONTEND_URL"] = original_frontend_url
            if original_frontend_origins is None:
                os.environ.pop("MONITOR_FRONTEND_ORIGINS", None)
            else:
                os.environ["MONITOR_FRONTEND_ORIGINS"] = original_frontend_origins

        self.assertIn("http://127.0.0.1:4173", origins)
        self.assertIn("http://localhost:4173", origins)
        self.assertIn("http://127.0.0.1:4174", origins)
        self.assertIn("http://localhost:4175", origins)

    def test_monitor_payload_returns_latest_run_and_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            screenshots_dir = Path(temp_dir) / ".ai-code-agent" / "visual-review" / "screenshots"
            screenshots_dir.mkdir(parents=True, exist_ok=True)
            (screenshots_dir / "home.png").write_bytes(b"png-bytes")
            compose_logs_path = Path(temp_dir) / ".ai-code-agent" / "compose" / "demo-stack-logs.txt"
            compose_logs_path.parent.mkdir(parents=True, exist_ok=True)
            compose_logs_path.write_text("app | booting\n", encoding="utf-8")
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
                "planning": {"plan_summary": "Upgrade the monitor UI and keep the API contract stable.", "retrieval_strategy": "hybrid", "available_skill_count": 2, "selected_skill_count": 1, "blocked_skill_count": 1, "selected_skills": ["frontend-visual-review"], "selected_skill_details": [{"name": "frontend-visual-review", "title": "Frontend Visual Review", "description": "Keep screenshot-backed UI checks visible in planning.", "path": "skills/frontend-visual-review/SKILL.md", "permission": "read-only", "sandbox": "optional", "score": 7, "reasons": ["Issue matched: screenshot"]}], "blocked_skills": [{"name": "compose-stack", "permission": "sandbox"}], "tasks": [{"id": "T1", "title": "Refresh monitor task panel", "status": "pending", "target_files": ["monitor_frontend/src/App.tsx"], "acceptance_checks": ["typecheck"]}], "task_failed_ids": ["T2"]},
                "skills": {"invocation_count": 2, "outcome_counts": {"applied": 1, "blocked": 1}, "invocations": [{"name": "frontend-visual-review", "phase": "plan", "outcome": "applied", "permission": "read-only"}, {"name": "compose-stack", "phase": "plan", "outcome": "blocked", "permission": "sandbox", "blocked_reason": "permission_not_allowed:sandbox"}]},
                "testing": {"validation_strategy": "targeted_retry"},
                "review": {"status": "changes_required", "approved": False, "remediation_required": True, "failed_task_ids": ["T2"], "remediation": {"guidance": ["Fix the failing typecheck."], "focus_areas": ["monitor_frontend/src/App.tsx"], "task_remediation": [{"task_id": "T2", "blocker_types": ["type_error"], "focus_areas": ["monitor_frontend/src/App.tsx"]}]}} ,
                "create_pr": {"outcome": "skipped", "reason": "review_not_approved", "provider": "github", "message": "Skipped PR creation until validation passes."},
                "phases": {
                    "test": {"status": "in_progress", "attempts": 1, "duration_ms": 0},
                },
                "execution_events": [
                    {"node": "test", "event_type": "node_started", "attempt": 1, "status": "started", "timestamp": "2026-03-08T18:45:04Z"}
                ],
                "testing": {
                    "validation_strategy": "targeted_retry",
                    "blocker_type_retry_used": True,
                    "blocker_type_retry_labels": ["typescript:noEmit"],
                    "commands": [{"label": "typescript:noEmit", "exit_code": 1, "duration_ms": 2200, "mode": "local"}],
                    "compose_readiness_status": "ready",
                    "compose_ready_services": ["app", "db"],
                    "compose_logs_path": ".ai-code-agent/compose/demo-stack-logs.txt",
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
            self.assertEqual(payload["rows"][0]["selected_skills"], ["frontend-visual-review"])
            self.assertEqual(payload["phase_details"]["test"]["title"], "Tester agent")
            self.assertIn("Validation strategy: targeted_retry", payload["phase_details"]["test"]["inputs"])
            self.assertIn("Selected skills: frontend-visual-review", payload["phase_details"]["plan"]["outputs"])
            self.assertIn("Skill invocations: frontend-visual-review | applied | plan, compose-stack | blocked | plan", payload["phase_details"]["plan"]["outputs"])
            self.assertIn("Available skills: 2", payload["phase_details"]["plan"]["highlights"])
            self.assertIn("Blocked skills: 1", payload["phase_details"]["plan"]["highlights"])
            self.assertIn("Failed tasks: T2", payload["phase_details"]["plan"]["highlights"])
            self.assertIn("Skill invocation outcomes: applied:1, blocked:1", payload["phase_details"]["plan"]["highlights"])
            self.assertEqual(payload["phase_details"]["plan"]["skills"][0]["name"], "frontend-visual-review")
            self.assertEqual(payload["phase_details"]["plan"]["skills"][0]["score"], 7)
            self.assertEqual(payload["phase_details"]["plan"]["blocked_skills"][0]["name"], "compose-stack")
            self.assertEqual(payload["phase_details"]["plan"]["skill_invocations"][0]["outcome"], "applied")
            self.assertEqual(payload["phase_details"]["plan"]["skill_invocations"][1]["outcome"], "blocked")
            self.assertIn("Plan summary: Upgrade the monitor UI and keep the API contract stable.", payload["phase_details"]["plan"]["outputs"])
            self.assertIn("Tasks: 1", payload["phase_details"]["plan"]["outputs"])
            self.assertEqual(payload["phase_details"]["plan"]["tasks"][0]["id"], "T1")
            self.assertIn("Remediation guidance: Fix the failing typecheck.", payload["phase_details"]["review"]["outputs"])
            self.assertIn("Failed tasks: T2", payload["phase_details"]["review"]["outputs"])
            self.assertEqual(payload["phase_details"]["review"]["tasks"][0]["status"], "pending")
            self.assertIn("Task blockers: T2 [type_error] -> monitor_frontend/src/App.tsx", payload["phase_details"]["review"]["outputs"])
            self.assertIn("Command: typescript:noEmit (exit=1, duration_ms=2200, mode=local)", payload["phase_details"]["test"]["outputs"])
            self.assertIn("Blocker-type retry labels: typescript:noEmit", payload["phase_details"]["test"]["outputs"])
            self.assertIn("Compose readiness: ready", payload["phase_details"]["test"]["outputs"])
            self.assertIn("Compose logs: .ai-code-agent/compose/demo-stack-logs.txt", payload["phase_details"]["test"]["outputs"])
            self.assertIn("Compose ready services: app, db", payload["phase_details"]["test"]["highlights"])
            self.assertIn("Blocker-type retry used: yes", payload["phase_details"]["test"]["highlights"])
            self.assertEqual(payload["phase_details"]["test"]["artifacts"][0]["path"], ".ai-code-agent/compose/demo-stack-logs.txt")
            self.assertIn("/api/monitor/artifact?repo=", payload["phase_details"]["test"]["artifacts"][0]["url"])
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
    def test_monitor_api_allows_localhost_alternate_port_cors(self) -> None:
        client = TestClient(app)

        response = client.options(
            "/api/monitor",
            headers={
                "Origin": "http://127.0.0.1:4174",
                "Access-Control-Request-Method": "GET",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("access-control-allow-origin"), "http://127.0.0.1:4174")

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

    @unittest.skipIf(TestClient is None or app is None, "fastapi test client unavailable")
    def test_monitor_artifact_route_serves_compose_log_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / ".ai-code-agent" / "compose" / "demo-stack-logs.txt"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text("app | booting\n", encoding="utf-8")

            client = TestClient(app)
            response = client.get("/api/monitor/artifact", params={"repo": temp_dir, "path": ".ai-code-agent/compose/demo-stack-logs.txt"})

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.text.replace("\r\n", "\n"), "app | booting\n")
            self.assertEqual(response.headers["content-type"], "text/plain; charset=utf-8")


if __name__ == "__main__":
    unittest.main()
