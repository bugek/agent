from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_code_agent import orchestrator
from ai_code_agent.metrics import load_execution_metrics_artifact, persist_execution_metrics


class ExecutionMetricsArtifactTest(unittest.TestCase):
    def test_persist_execution_metrics_writes_metrics_json_under_run_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            metrics = {"schema_version": "execution-metrics/v1", "run_id": "20260308T102233Z-deadbeef"}

            relative_path = persist_execution_metrics(temp_dir, "20260308T102233Z-deadbeef", metrics)

            self.assertEqual(relative_path, ".ai-code-agent/runs/20260308T102233Z-deadbeef/metrics.json")
            artifact_path = Path(temp_dir) / relative_path
            self.assertTrue(artifact_path.exists())
            self.assertEqual(json.loads(artifact_path.read_text(encoding="utf-8")), metrics)

    def test_plan_node_persists_metrics_artifact_and_returns_relative_path(self) -> None:
        planner_result = {
            "plan": "Inspect allowed files.",
            "files_to_edit": ["ai_code_agent/main.py"],
            "planning_context": {"retrieval_strategy": "hybrid"},
        }

        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "ai_code_agent.agents.planner.PlannerAgent"
        ) as mock_planner, patch("ai_code_agent.llm.client.LLMClient.from_config", return_value=object()):
            mock_planner.return_value.run.return_value = planner_result
            result = orchestrator.plan_node(
                {
                    "issue_description": "update app",
                    "workspace_dir": temp_dir,
                    "run_id": "20260308T102233Z-deadbeef",
                    "workflow_started_at": "2026-03-08T10:22:33Z",
                }
            )

            self.assertEqual(result["execution_metrics_path"], ".ai-code-agent/runs/20260308T102233Z-deadbeef/metrics.json")
            artifact_path = Path(temp_dir) / result["execution_metrics_path"]
            self.assertTrue(artifact_path.exists())
            payload = json.loads(artifact_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["run_id"], "20260308T102233Z-deadbeef")
            self.assertEqual(payload["planning"]["retrieval_strategy"], "hybrid")
            self.assertEqual(payload["phases"]["plan"]["attempts"], 1)

    def test_load_execution_metrics_artifact_returns_latest_metrics_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            older_metrics = {"schema_version": "execution-metrics/v1", "run_id": "20260308T102233Z-older"}
            newer_metrics = {"schema_version": "execution-metrics/v1", "run_id": "20260308T102244Z-newer"}

            older_path = persist_execution_metrics(temp_dir, "20260308T102233Z-older", older_metrics)
            newer_path = persist_execution_metrics(temp_dir, "20260308T102244Z-newer", newer_metrics)
            assert older_path is not None
            assert newer_path is not None
            older_file = Path(temp_dir) / older_path
            newer_file = Path(temp_dir) / newer_path
            os.utime(older_file, (1000, 1000))
            os.utime(newer_file, (2000, 2000))

            loaded_metrics, loaded_path = load_execution_metrics_artifact(temp_dir)

            self.assertEqual(loaded_metrics, newer_metrics)
            self.assertEqual(loaded_path, newer_path)

    def test_load_execution_metrics_artifact_supports_specific_run_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            metrics = {"schema_version": "execution-metrics/v1", "run_id": "20260308T102233Z-deadbeef"}
            expected_path = persist_execution_metrics(temp_dir, "20260308T102233Z-deadbeef", metrics)

            loaded_metrics, loaded_path = load_execution_metrics_artifact(temp_dir, "20260308T102233Z-deadbeef")

            self.assertEqual(loaded_metrics, metrics)
            self.assertEqual(loaded_path, expected_path)


if __name__ == "__main__":
    unittest.main()