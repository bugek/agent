from __future__ import annotations

import unittest

from ai_code_agent.metrics import build_execution_metrics


class ExecutionMetricsTest(unittest.TestCase):
    def test_build_execution_metrics_aggregates_existing_state(self) -> None:
        metrics = build_execution_metrics(
            {
                "run_id": "20260308T102233Z-deadbeef",
                "workflow_started_at": "2026-03-08T10:22:33Z",
                "issue_description": "update dashboard page",
                "workspace_dir": ".",
                "workspace_profile": {
                    "has_python": True,
                    "has_package_json": True,
                    "frameworks": ["nextjs"],
                    "package_manager": "npm",
                },
                "files_to_edit": ["app/page.tsx", "components/panel.tsx"],
                "patches": [
                    {"file": "app/page.tsx"},
                    {"file": "components/panel.tsx"},
                ],
                "planning_context": {
                    "retrieval_strategy": "hybrid",
                    "candidate_scores": {"app/page.tsx": 0.9, "components/panel.tsx": 0.8},
                    "graph_seed_files": ["app/page.tsx"],
                    "blocked_files_to_edit": [{"file_path": "artifact/fixtures/demo.txt", "reason": "matched deny rule"}],
                    "edit_intent": [{"file_path": "app/page.tsx", "intent": "Fix dashboard render regression."}],
                },
                "codegen_summary": {
                    "generated_by": "llm",
                    "requested_operations": 3,
                    "applied_operations": 2,
                    "failed_operations": ["replace_text failed for missing.ts"],
                    "blocked_operations": [{"file_path": "artifact/fixtures/demo.txt", "reason": "matched deny rule"}],
                    "remediation_applied": True,
                    "remediation_focus_count": 2,
                },
                "test_results": "compileall(exit=0):\n\nscript:build(exit=1):\nboom\n\nlint:\n[app/page.tsx] warning\n",
                "test_passed": False,
                "testing_summary": {
                    "commands": [
                        {"label": "compileall", "exit_code": 0, "duration_ms": 120, "mode": "local", "timed_out": False},
                        {"label": "script:build", "exit_code": 1, "duration_ms": 980, "mode": "local", "timed_out": False},
                    ],
                    "command_count": 2,
                    "failed_command_count": 1,
                    "failed_commands": ["script:build"],
                    "lint_issue_count": 1,
                    "total_duration_ms": 1100,
                    "slowest_command": {"label": "script:build", "exit_code": 1, "duration_ms": 980, "mode": "local", "timed_out": False},
                    "validation_strategy": "targeted_retry",
                    "selected_command_labels": ["script:build"],
                    "skipped_command_labels": ["compileall"],
                    "requested_retry_labels": ["script:build"],
                },
                "visual_review": {
                    "enabled": True,
                    "screenshot_status": "passed",
                    "artifact_count": 2,
                    "state_coverage": {
                        "loading_file": True,
                        "error_file": False,
                        "loading_state": True,
                        "empty_state": True,
                        "error_state": False,
                        "success_state": True,
                    },
                    "responsive_review": {"missing_categories": ["mobile"]},
                },
                "review_comments": ["Smoke tests failed.", "Frontend visual review is missing responsive viewport coverage for: mobile."],
                "review_approved": False,
                "review_summary": {
                    "status": "changes_required",
                    "changed_areas": ["app/page.tsx", "components/panel.tsx"],
                    "validation": {"passed": ["compileall"], "failed": ["script:build"]},
                    "residual_risks": ["Smoke tests failed."],
                },
                "retry_count": 1,
                "execution_events": [
                    {"timestamp": "2026-03-08T10:22:33Z", "node": "plan", "event_type": "node_started", "attempt": 1, "status": "started", "duration_ms": 0},
                    {"timestamp": "2026-03-08T10:22:40Z", "node": "plan", "event_type": "node_completed", "attempt": 1, "status": "completed", "duration_ms": 7000},
                    {"timestamp": "2026-03-08T10:22:41Z", "node": "code", "event_type": "node_started", "attempt": 1, "status": "started", "duration_ms": 0},
                    {"timestamp": "2026-03-08T10:23:10Z", "node": "code", "event_type": "node_completed", "attempt": 1, "status": "completed", "duration_ms": 29000},
                    {"timestamp": "2026-03-08T10:23:11Z", "node": "test", "event_type": "node_started", "attempt": 1, "status": "started", "duration_ms": 0},
                    {"timestamp": "2026-03-08T10:23:48Z", "node": "test", "event_type": "node_completed", "attempt": 1, "status": "failed", "duration_ms": 37000},
                    {"timestamp": "2026-03-08T10:23:49Z", "node": "review", "event_type": "node_started", "attempt": 1, "status": "started", "duration_ms": 0},
                    {"timestamp": "2026-03-08T10:24:01Z", "node": "review", "event_type": "node_completed", "attempt": 1, "status": "changes_required", "duration_ms": 12000},
                ],
            }
        )

        self.assertEqual(metrics["schema_version"], "execution-metrics/v1")
        self.assertEqual(metrics["workflow"]["status"], "changes_required")
        self.assertEqual(metrics["workflow"]["attempt_count"], 2)
        self.assertEqual(metrics["planning"]["candidate_file_count"], 2)
        self.assertEqual(metrics["planning"]["edit_intent_count"], 1)
        self.assertEqual(metrics["coding"]["blocked_operation_count"], 1)
        self.assertEqual(metrics["coding"]["remediation_focus_count"], 2)
        self.assertEqual(metrics["testing"]["failed_commands"], ["script:build"])
        self.assertEqual(metrics["testing"]["lint_issue_count"], 1)
        self.assertEqual(metrics["testing"]["total_duration_ms"], 1100)
        self.assertEqual(metrics["testing"]["validation_strategy"], "targeted_retry")
        self.assertEqual(metrics["testing"]["selected_command_count"], 1)
        self.assertEqual(metrics["testing"]["skipped_command_count"], 1)
        self.assertEqual(metrics["testing"]["requested_retry_labels"], ["script:build"])
        self.assertEqual(metrics["testing"]["command_reduction_rate"], 0.5)
        self.assertEqual(metrics["testing"]["slowest_command"]["label"], "script:build")
        self.assertEqual(metrics["testing"]["commands"][1]["duration_ms"], 980)
        self.assertEqual(metrics["testing"]["visual_review"]["missing_state_count"], 2)
        self.assertEqual(metrics["review"]["validation_failed_count"], 1)
        self.assertEqual(metrics["review"]["remediation_required"], False)
        self.assertEqual(metrics["effectiveness"]["retry_attempted"], True)
        self.assertEqual(metrics["effectiveness"]["retry_recovered"], False)
        self.assertEqual(metrics["effectiveness"]["remediation_applied"], True)
        self.assertEqual(metrics["effectiveness"]["edit_intent_used"], True)
        self.assertEqual(metrics["effectiveness"]["targeted_retry_used"], True)
        self.assertEqual(metrics["effectiveness"]["command_reduction_count"], 1)
        self.assertEqual(metrics["effectiveness"]["command_reduction_rate"], 0.5)
        self.assertEqual(metrics["failures"]["primary_category"], "policy")
        self.assertEqual(metrics["phases"]["test"]["status"], "failed")
        self.assertEqual(metrics["phases"]["test"]["duration_ms"], 37000)


if __name__ == "__main__":
    unittest.main()