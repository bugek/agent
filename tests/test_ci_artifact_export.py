from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from artifact.export_ci_artifacts import build_ci_artifact_summary, persist_ci_artifact_summary
from ai_code_agent.metrics import persist_execution_metrics


class CiArtifactExportTest(unittest.TestCase):
    def test_build_summary_without_metrics_records_note(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            log_path = workspace / ".ai-code-agent" / "ci" / "validation-full.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text("validation ok\n", encoding="utf-8")

            summary, diagnostics_path = build_ci_artifact_summary(
                workspace,
                validation_log=log_path,
                recent=5,
            )

            self.assertIsNone(diagnostics_path)
            self.assertEqual(summary["validation_log_path"], ".ai-code-agent/ci/validation-full.log")
            self.assertEqual(summary["execution_metrics_count"], 0)
            self.assertIsNone(summary["diagnostics_summary_path"])
            self.assertIn("no execution metrics artifacts were found in the workspace", summary["notes"])

    def test_build_summary_persists_diagnostics_when_metrics_exist(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            log_path = workspace / ".ai-code-agent" / "ci" / "validation-full.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text("validation ok\n", encoding="utf-8")
            metrics = {
                "schema_version": "execution-metrics/v1",
                "run_id": "run-123",
                "workflow": {"status": "approved", "duration_ms": 123, "attempt_count": 1, "terminal_node": "review"},
                "testing": {"validation_strategy": "full", "total_duration_ms": 100, "skipped_command_count": 0, "command_reduction_rate": 0.0},
                "effectiveness": {"retry_recovered": False},
                "failures": {"primary_category": None},
            }
            persist_execution_metrics(str(workspace), "run-123", metrics)

            summary, diagnostics_path = build_ci_artifact_summary(
                workspace,
                validation_log=log_path,
                recent=5,
            )
            summary_path = persist_ci_artifact_summary(workspace, summary)

            self.assertEqual(summary["execution_metrics_count"], 1)
            self.assertEqual(summary["latest_run_id"], "run-123")
            self.assertEqual(diagnostics_path, ".ai-code-agent/diagnostics/diagnose-recent-5.json")
            self.assertEqual(summary["diagnostics_summary_path"], diagnostics_path)
            self.assertEqual(summary_path, ".ai-code-agent/ci/summary.json")

            saved_summary = json.loads((workspace / ".ai-code-agent" / "ci" / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(saved_summary["latest_execution_metrics_path"], ".ai-code-agent/runs/run-123/metrics.json")
            diagnostics_payload = json.loads((workspace / ".ai-code-agent" / "diagnostics" / "diagnose-recent-5.json").read_text(encoding="utf-8"))
            self.assertEqual(diagnostics_payload["latest_run_id"], "run-123")


if __name__ == "__main__":
    unittest.main()