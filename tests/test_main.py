from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout

from ai_code_agent.config import AgentConfig
from ai_code_agent.main import parse_args, run_diagnostics
from ai_code_agent.metrics import persist_execution_metrics


class MainCliTest(unittest.TestCase):
    def test_parse_args_supports_diagnose_command(self) -> None:
        args = parse_args(["diagnose", "--repo", "workspace", "--run-id", "run-123", "--json"])

        self.assertEqual(args.command, "diagnose")
        self.assertEqual(args.repo, "workspace")
        self.assertEqual(args.run_id, "run-123")
        self.assertTrue(args.json)

    def test_run_diagnostics_prints_latest_metrics_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            metrics = {
                "schema_version": "execution-metrics/v1",
                "run_id": "run-123",
                "workflow": {"status": "changes_required", "attempt_count": 2, "duration_ms": 1234, "terminal_node": "review"},
                "failures": {"primary_category": "validation"},
                "testing": {
                    "failed_commands": ["script:build"],
                    "total_duration_ms": 1100,
                    "slowest_command": {"label": "script:build", "duration_ms": 980, "exit_code": 1, "timed_out": False},
                },
                "review": {"status": "changes_required", "residual_risk_count": 2},
            }
            persist_execution_metrics(temp_dir, "run-123", metrics)
            output = io.StringIO()

            with redirect_stdout(output):
                exit_code = run_diagnostics(AgentConfig(workspace_dir=temp_dir), None, None, False)

            self.assertEqual(exit_code, 0)
            rendered = output.getvalue()
            self.assertIn("Run ID: run-123", rendered)
            self.assertIn("Metrics artifact: .ai-code-agent/runs/run-123/metrics.json", rendered)
            self.assertIn("Primary failure category: validation", rendered)
            self.assertIn("Failed commands: script:build", rendered)
            self.assertIn("Slowest command: script:build (980 ms)", rendered)
            self.assertIn("Testing duration ms: 1100", rendered)

    def test_run_diagnostics_prints_json_when_requested(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            metrics = {"schema_version": "execution-metrics/v1", "run_id": "run-123"}
            persist_execution_metrics(temp_dir, "run-123", metrics)
            output = io.StringIO()

            with redirect_stdout(output):
                exit_code = run_diagnostics(AgentConfig(workspace_dir=temp_dir), temp_dir, "run-123", True)

            self.assertEqual(exit_code, 0)
            self.assertEqual(json.loads(output.getvalue()), metrics)


if __name__ == "__main__":
    unittest.main()