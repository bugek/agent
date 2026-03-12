from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_code_agent import orchestrator
from ai_code_agent.metrics import (
    build_diagnostics_summary,
    load_fresh_diagnostics_summary_artifact,
    load_execution_metrics_artifact,
    normalize_execution_metrics_artifacts,
    persist_diagnostics_summary,
    persist_execution_metrics,
)


class ExecutionMetricsArtifactTest(unittest.TestCase):
    def test_persist_execution_metrics_writes_metrics_json_under_run_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            metrics = {"schema_version": "execution-metrics/v1", "run_id": "20260308T102233Z-deadbeef"}

            relative_path = persist_execution_metrics(temp_dir, "20260308T102233Z-deadbeef", metrics)

            self.assertEqual(relative_path, ".ai-code-agent/runs/20260308T102233Z-deadbeef/metrics.json")
            artifact_path = Path(temp_dir) / relative_path
            self.assertTrue(artifact_path.exists())
            self.assertEqual(json.loads(artifact_path.read_text(encoding="utf-8")), metrics)

    def test_persist_execution_metrics_normalizes_nested_sets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            metrics = {
                "schema_version": "execution-metrics/v1",
                "run_id": "20260308T102233Z-deadbeef",
                "testing": {
                    "labels": {"script:typecheck", "script:visual-review"},
                    "nested": [{"categories": {"desktop", "mobile"}}],
                },
            }

            relative_path = persist_execution_metrics(temp_dir, "20260308T102233Z-deadbeef", metrics)

            artifact_path = Path(temp_dir) / relative_path
            payload = json.loads(artifact_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["testing"]["labels"], ["script:typecheck", "script:visual-review"])
            self.assertEqual(payload["testing"]["nested"][0]["categories"], ["desktop", "mobile"])

    def test_plan_node_persists_metrics_artifact_and_returns_relative_path(self) -> None:
        planner_result = {
            "plan": "Inspect allowed files.",
            "files_to_edit": ["ai_code_agent/main.py"],
            "planning_context": {"retrieval_strategy": "hybrid"},
        }

        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "ai_code_agent.agents.planner.PlanAgent"
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

    def test_persist_diagnostics_summary_writes_named_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            metrics = {
                "schema_version": "execution-metrics/v1",
                "run_id": "run-1",
                "workflow": {"status": "failed", "duration_ms": 100, "terminal_node": "test"},
                "failures": {"primary_category": "validation"},
                "testing": {"total_duration_ms": 70},
            }
            summary = build_diagnostics_summary(
                [(metrics, ".ai-code-agent/runs/run-1/metrics.json")],
                {"run_count": 1},
                recent=5,
                filters={"status": "failed", "failure_category": "validation"},
            )

            relative_path = persist_diagnostics_summary(
                temp_dir,
                summary,
                recent=5,
                status="failed",
                failure_category="validation",
            )

            self.assertEqual(relative_path, ".ai-code-agent/diagnostics/diagnose-recent-5-status-failed-failure-validation.json")
            artifact_path = Path(temp_dir) / relative_path
            self.assertTrue(artifact_path.exists())
            payload = json.loads(artifact_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["schema_version"], "diagnostics-summary/v2")
            self.assertEqual(payload["latest_run_id"], "run-1")

    def test_load_fresh_diagnostics_summary_artifact_requires_summary_newer_than_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            metrics = {
                "schema_version": "execution-metrics/v1",
                "run_id": "run-1",
                "workflow": {"status": "failed", "duration_ms": 100, "terminal_node": "test"},
                "failures": {"primary_category": "validation"},
                "testing": {"total_duration_ms": 70},
            }
            metrics_path = persist_execution_metrics(temp_dir, "run-1", metrics)
            summary = build_diagnostics_summary(
                [(metrics, ".ai-code-agent/runs/run-1/metrics.json")],
                {"run_count": 1},
                recent=5,
                filters={"status": "failed", "failure_category": None},
            )
            summary_path = persist_diagnostics_summary(
                temp_dir,
                summary,
                recent=5,
                status="failed",
                failure_category=None,
            )
            assert metrics_path is not None
            assert summary_path is not None
            os.utime(Path(temp_dir) / metrics_path, (100, 100))
            os.utime(Path(temp_dir) / summary_path, (200, 200))

            loaded_summary, loaded_path = load_fresh_diagnostics_summary_artifact(
                temp_dir,
                recent=5,
                status="failed",
                failure_category=None,
            )

            self.assertEqual(loaded_path, summary_path)
            self.assertEqual(loaded_summary["latest_run_id"], "run-1")

            os.utime(Path(temp_dir) / metrics_path, (300, 300))
            stale_summary, stale_path = load_fresh_diagnostics_summary_artifact(
                temp_dir,
                recent=5,
                status="failed",
                failure_category=None,
            )

            self.assertIsNone(stale_summary)
            self.assertIsNone(stale_path)

    def test_load_fresh_diagnostics_summary_artifact_rejects_old_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            diagnostics_dir = Path(temp_dir) / ".ai-code-agent" / "diagnostics"
            diagnostics_dir.mkdir(parents=True)
            summary_path = diagnostics_dir / "diagnose-recent-5.json"
            summary_path.write_text(
                json.dumps({"schema_version": "diagnostics-summary/v1", "latest_run_id": "run-1"}),
                encoding="utf-8",
            )

            loaded_summary, loaded_path = load_fresh_diagnostics_summary_artifact(
                temp_dir,
                recent=5,
                status=None,
                failure_category=None,
            )

            self.assertIsNone(loaded_summary)
            self.assertIsNone(loaded_path)

    def test_normalize_execution_metrics_artifacts_rewrites_legacy_payload_and_clears_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            legacy_metrics = {
                "schema_version": "execution-metrics/v1",
                "run_id": "run-1",
                "workflow": {"status": "approved", "created_pr": True, "linked_pr": False},
                "failures": {
                    "has_failure": True,
                    "primary_category": "unknown",
                    "subcategory": "unknown_failure",
                    "categories": ["unknown"],
                    "taxonomy": {"category": "unknown", "subcategory": "unknown_failure"},
                },
                "execution_events": [
                    {
                        "node": "create_pr",
                        "details": {
                            "created_pr_url": "https://example.test/pr/1",
                            "branch_name": "agent/run-1",
                            "issue_provider": "github",
                            "base_branch": "main",
                        },
                    }
                ],
            }
            persist_execution_metrics(temp_dir, "run-1", legacy_metrics)
            summary = build_diagnostics_summary(
                [(legacy_metrics, ".ai-code-agent/runs/run-1/metrics.json")],
                {"run_count": 1},
                recent=5,
                filters={"status": None, "failure_category": None},
            )
            summary_path = persist_diagnostics_summary(
                temp_dir,
                summary,
                recent=5,
                status=None,
                failure_category=None,
            )

            report = normalize_execution_metrics_artifacts(temp_dir)

            self.assertEqual(report, {"checked": 1, "updated": 1, "diagnostics_removed": 1})
            normalized_metrics, normalized_path = load_execution_metrics_artifact(temp_dir, "run-1")
            self.assertEqual(normalized_path, ".ai-code-agent/runs/run-1/metrics.json")
            assert normalized_metrics is not None
            self.assertEqual(normalized_metrics["failures"]["has_failure"], False)
            self.assertIsNone(normalized_metrics["failures"]["primary_category"])
            self.assertEqual(normalized_metrics["failures"]["categories"], [])
            self.assertEqual(normalized_metrics["create_pr"]["outcome"], "created")
            self.assertEqual(normalized_metrics["create_pr"]["reason"], "legacy_created_pr")
            self.assertEqual(normalized_metrics["create_pr"]["provider"], "github")
            self.assertEqual(normalized_metrics["create_pr"]["branch_name"], "agent/run-1")
            self.assertEqual(normalized_metrics["create_pr"]["base_branch"], "main")
            self.assertEqual(normalized_metrics["create_pr"]["pr_url"], "https://example.test/pr/1")
            self.assertTrue(normalized_metrics["workflow"]["created_pr"])
            self.assertTrue(normalized_metrics["workflow"]["linked_pr"])
            self.assertFalse((Path(temp_dir) / summary_path).exists())

    def test_normalize_execution_metrics_artifacts_preserves_metrics_mtime(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            legacy_metrics = {
                "schema_version": "execution-metrics/v1",
                "run_id": "20260308T120000Z-older",
                "workflow": {"status": "approved", "created_pr": True, "linked_pr": False},
                "failures": {
                    "has_failure": True,
                    "primary_category": "unknown",
                    "subcategory": "unknown_failure",
                    "categories": ["unknown"],
                    "taxonomy": {"category": "unknown", "subcategory": "unknown_failure"},
                },
                "execution_events": [
                    {"node": "create_pr", "details": {"created_pr_url": "https://example.test/pr/1", "issue_provider": "github"}}
                ],
            }
            relative_path = persist_execution_metrics(temp_dir, "20260308T120000Z-older", legacy_metrics)
            assert relative_path is not None
            artifact_path = Path(temp_dir) / relative_path
            os.utime(artifact_path, (100, 100))

            normalize_execution_metrics_artifacts(temp_dir)

            self.assertEqual(artifact_path.stat().st_mtime, 100)

    def test_load_execution_metrics_artifact_prefers_run_id_timestamp_over_rewrite_mtime(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            older_metrics = {"schema_version": "execution-metrics/v1", "run_id": "20260308T120000Z-older"}
            newer_metrics = {"schema_version": "execution-metrics/v1", "run_id": "20260308T130000Z-newer"}

            older_path = persist_execution_metrics(temp_dir, "20260308T120000Z-older", older_metrics)
            newer_path = persist_execution_metrics(temp_dir, "20260308T130000Z-newer", newer_metrics)
            assert older_path is not None
            assert newer_path is not None
            os.utime(Path(temp_dir) / older_path, (300, 300))
            os.utime(Path(temp_dir) / newer_path, (100, 100))

            loaded_metrics, loaded_path = load_execution_metrics_artifact(temp_dir)

            self.assertEqual(loaded_metrics, newer_metrics)
            self.assertEqual(loaded_path, newer_path)


if __name__ == "__main__":
    unittest.main()