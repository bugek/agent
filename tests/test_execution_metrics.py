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
                "plan": "Update the dashboard page safely, keep the loading state intact, and limit edits to app/page.tsx and components/panel.tsx.",
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
                    "retry_policy_reason": "history_prefers_targeted_retry",
                    "retry_policy_history_source": "failure_category",
                    "retry_policy_confidence": "weak",
                    "retry_policy_stop_reason": "history_low_recovery_probability",
                    "selected_command_labels": ["script:build"],
                    "skipped_command_labels": ["compileall"],
                    "requested_retry_labels": ["script:build"],
                    "sandbox_requested_mode": "auto",
                    "sandbox_mode": "local",
                    "sandbox_started": True,
                    "sandbox_fallback_reason": "docker_unavailable",
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
                    "remediation": {
                        "required": True,
                        "failed_validation_labels": ["script:build"],
                        "focus_areas": ["app/page.tsx"],
                        "guidance": ["Repair the failing build before approval."],
                    },
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
        self.assertIn("Update the dashboard page safely", metrics["planning"]["plan_summary"])
        self.assertEqual(metrics["coding"]["blocked_operation_count"], 1)
        self.assertEqual(metrics["coding"]["remediation_focus_count"], 2)
        self.assertEqual(metrics["testing"]["failed_commands"], ["script:build"])
        self.assertEqual(metrics["testing"]["lint_issue_count"], 1)
        self.assertEqual(metrics["testing"]["total_duration_ms"], 1100)
        self.assertEqual(metrics["testing"]["validation_strategy"], "targeted_retry")
        self.assertEqual(metrics["testing"]["selected_command_count"], 1)
        self.assertEqual(metrics["testing"]["skipped_command_count"], 1)
        self.assertEqual(metrics["testing"]["requested_retry_labels"], ["script:build"])
        self.assertEqual(metrics["testing"]["sandbox_requested_mode"], "auto")
        self.assertEqual(metrics["testing"]["sandbox_mode"], "local")
        self.assertEqual(metrics["testing"]["sandbox_started"], True)
        self.assertEqual(metrics["testing"]["sandbox_fallback_reason"], "docker_unavailable")
        self.assertEqual(metrics["testing"]["retry_policy_reason"], "history_prefers_targeted_retry")
        self.assertEqual(metrics["testing"]["retry_policy_history_source"], "failure_category")
        self.assertEqual(metrics["testing"]["retry_policy_confidence"], "weak")
        self.assertEqual(metrics["testing"]["retry_policy_stop_reason"], "history_low_recovery_probability")
        self.assertEqual(metrics["testing"]["command_reduction_rate"], 0.5)
        self.assertEqual(metrics["testing"]["slowest_command"]["label"], "script:build")
        self.assertEqual(metrics["testing"]["commands"][1]["duration_ms"], 980)
        self.assertEqual(metrics["testing"]["visual_review"]["missing_state_count"], 2)
        self.assertEqual(metrics["review"]["validation_failed_count"], 1)
        self.assertEqual(metrics["review"]["remediation_required"], True)
        self.assertEqual(metrics["review"]["remediation"]["focus_areas"], ["app/page.tsx"])
        self.assertEqual(metrics["review"]["remediation"]["guidance"], ["Repair the failing build before approval."])
        self.assertEqual(metrics["effectiveness"]["retry_attempted"], True)
        self.assertEqual(metrics["effectiveness"]["retry_recovered"], False)
        self.assertEqual(metrics["effectiveness"]["remediation_applied"], True)
        self.assertEqual(metrics["effectiveness"]["edit_intent_used"], True)
        self.assertEqual(metrics["effectiveness"]["targeted_retry_used"], True)
        self.assertEqual(metrics["effectiveness"]["command_reduction_count"], 1)
        self.assertEqual(metrics["effectiveness"]["command_reduction_rate"], 0.5)
        self.assertEqual(metrics["failures"]["primary_category"], "policy")
        self.assertEqual(metrics["failures"]["subcategory"], "blocked_edit_target")
        self.assertEqual(metrics["failures"]["taxonomy"], {"category": "policy", "subcategory": "blocked_edit_target"})
        self.assertEqual(metrics["phases"]["test"]["status"], "failed")
        self.assertEqual(metrics["phases"]["test"]["duration_ms"], 37000)

    def test_build_execution_metrics_marks_running_node_progress(self) -> None:
        metrics = build_execution_metrics(
            {
                "run_id": "20260308T184500Z-running01",
                "workflow_started_at": "2026-03-08T18:45:00Z",
                "issue_description": "build monitor ui",
                "workspace_dir": ".",
                "execution_events": [
                    {"timestamp": "2026-03-08T18:45:00Z", "node": "plan", "event_type": "node_started", "attempt": 1, "status": "started", "duration_ms": 0},
                    {"timestamp": "2026-03-08T18:45:03Z", "node": "plan", "event_type": "node_completed", "attempt": 1, "status": "completed", "duration_ms": 3000},
                    {"timestamp": "2026-03-08T18:45:04Z", "node": "code", "event_type": "node_started", "attempt": 1, "status": "started", "duration_ms": 0},
                ],
            }
        )

        self.assertEqual(metrics["workflow"]["status"], "running")
        self.assertEqual(metrics["workflow"]["active_node"], "code")
        self.assertEqual(metrics["phases"]["plan"]["status"], "completed")
        self.assertEqual(metrics["phases"]["code"]["status"], "in_progress")
        self.assertEqual(metrics["phases"]["code"]["attempts"], 1)
        self.assertEqual(len(metrics["execution_events"]), 3)

    def test_build_execution_metrics_captures_create_pr_outcome(self) -> None:
        metrics = build_execution_metrics(
            {
                "run_id": "20260308T184500Z-createpr1",
                "workflow_started_at": "2026-03-08T18:45:00Z",
                "issue_description": "open pull request",
                "workspace_dir": ".",
                "test_passed": True,
                "review_approved": True,
                "created_pr_url": "https://github.com/octo/repo/pull/9",
                "create_pr_result": {
                    "outcome": "existing",
                    "reason": "existing_open_pr",
                    "provider": "github",
                    "branch_name": "ai-code-agent/gh-42-fix-flaky-validation",
                    "base_branch": "main",
                    "pr_url": "https://github.com/octo/repo/pull/9",
                    "message": "Pushed branch, and found existing open GitHub PR: https://github.com/octo/repo/pull/9",
                    "error": None,
                },
                "execution_events": [
                    {"timestamp": "2026-03-08T18:45:00Z", "node": "plan", "event_type": "node_started", "attempt": 1, "status": "started", "duration_ms": 0},
                    {"timestamp": "2026-03-08T18:45:02Z", "node": "plan", "event_type": "node_completed", "attempt": 1, "status": "completed", "duration_ms": 2000},
                    {"timestamp": "2026-03-08T18:45:03Z", "node": "create_pr", "event_type": "node_started", "attempt": 1, "status": "started", "duration_ms": 0},
                    {"timestamp": "2026-03-08T18:45:05Z", "node": "create_pr", "event_type": "node_completed", "attempt": 1, "status": "completed", "duration_ms": 2000},
                ],
            }
        )

        self.assertEqual(metrics["workflow"]["created_pr"], False)
        self.assertEqual(metrics["workflow"]["linked_pr"], True)
        self.assertEqual(metrics["failures"]["has_failure"], False)
        self.assertIsNone(metrics["failures"]["primary_category"])
        self.assertIsNone(metrics["failures"]["subcategory"])
        self.assertEqual(metrics["create_pr"]["outcome"], "existing")
        self.assertEqual(metrics["create_pr"]["reason"], "existing_open_pr")
        self.assertEqual(metrics["phases"]["create_pr"]["status"], "existing")
        self.assertIsNone(metrics["failures"]["error_message"])

    def test_build_execution_metrics_does_not_report_smoke_test_failure_when_tests_not_run(self) -> None:
        metrics = build_execution_metrics(
            {
                "run_id": "20260308T110000Z-deadbeef",
                "workflow_started_at": "2026-03-08T11:00:00Z",
                "issue_description": "create dashboard",
                "workspace_dir": ".",
                "patches": [{"file": "app/page.tsx"}],
                "test_passed": False,
                "test_results": None,
                "review_comments": [],
                "review_approved": False,
                "review_summary": {},
                "execution_events": [
                    {"timestamp": "2026-03-08T11:00:00Z", "node": "plan", "event_type": "node_started", "attempt": 1, "status": "started", "duration_ms": 0},
                    {"timestamp": "2026-03-08T11:00:05Z", "node": "plan", "event_type": "node_completed", "attempt": 1, "status": "completed", "duration_ms": 5000},
                    {"timestamp": "2026-03-08T11:00:06Z", "node": "code", "event_type": "node_started", "attempt": 1, "status": "started", "duration_ms": 0},
                    {"timestamp": "2026-03-08T11:00:10Z", "node": "code", "event_type": "node_completed", "attempt": 1, "status": "completed", "duration_ms": 4000},
                ],
            }
        )

        self.assertEqual(metrics["testing"]["status"], "not_run")
        self.assertIsNone(metrics["failures"]["error_message"])

    def test_build_execution_metrics_ignores_responsive_gap_when_screenshots_not_configured(self) -> None:
        metrics = build_execution_metrics(
            {
                "run_id": "20260308T110500Z-deadbeef",
                "workflow_started_at": "2026-03-08T11:05:00Z",
                "issue_description": "update dashboard page",
                "workspace_dir": ".",
                "patches": [{"file": "app/page.tsx"}],
                "test_results": "script:build(exit=1):\nboom\n",
                "test_passed": False,
                "visual_review": {
                    "enabled": True,
                    "screenshot_status": "not_configured",
                    "artifact_count": 0,
                    "state_coverage": {
                        "loading_file": True,
                        "error_file": True,
                        "loading_state": True,
                        "empty_state": True,
                        "error_state": True,
                        "success_state": True,
                    },
                    "responsive_review": {"missing_categories": ["mobile", "desktop"]},
                },
                "review_comments": ["Smoke tests failed."],
                "review_approved": False,
                "review_summary": {
                    "status": "changes_required",
                    "validation": {"passed": [], "failed": ["script:build"]},
                    "visual_review": {
                        "screenshot_status": "not_configured",
                        "artifact_count": 0,
                        "missing_states": [],
                        "missing_responsive_categories": ["mobile", "desktop"],
                    },
                    "residual_risks": ["Smoke tests failed."],
                },
                "execution_events": [
                    {"timestamp": "2026-03-08T11:05:00Z", "node": "plan", "event_type": "node_started", "attempt": 1, "status": "started", "duration_ms": 0},
                    {"timestamp": "2026-03-08T11:05:05Z", "node": "plan", "event_type": "node_completed", "attempt": 1, "status": "completed", "duration_ms": 5000},
                    {"timestamp": "2026-03-08T11:05:06Z", "node": "code", "event_type": "node_started", "attempt": 1, "status": "started", "duration_ms": 0},
                    {"timestamp": "2026-03-08T11:05:10Z", "node": "code", "event_type": "node_completed", "attempt": 1, "status": "completed", "duration_ms": 4000},
                    {"timestamp": "2026-03-08T11:05:11Z", "node": "test", "event_type": "node_started", "attempt": 1, "status": "started", "duration_ms": 0},
                    {"timestamp": "2026-03-08T11:05:16Z", "node": "test", "event_type": "node_completed", "attempt": 1, "status": "failed", "duration_ms": 5000},
                    {"timestamp": "2026-03-08T11:05:17Z", "node": "review", "event_type": "node_started", "attempt": 1, "status": "started", "duration_ms": 0},
                    {"timestamp": "2026-03-08T11:05:19Z", "node": "review", "event_type": "node_completed", "attempt": 1, "status": "changes_required", "duration_ms": 2000},
                ],
            }
        )

        self.assertEqual(metrics["failures"]["subcategory"], "command:script:build")

    def test_trend_ignores_legacy_unknown_failure_for_approved_run(self) -> None:
        from ai_code_agent.metrics import build_execution_metrics_trend

        trend = build_execution_metrics_trend(
            [
                (
                    {
                        "run_id": "run-legacy-approved",
                        "workflow": {"status": "approved", "duration_ms": 100, "attempt_count": 1, "terminal_node": "create_pr"},
                        "failures": {"has_failure": False, "primary_category": "unknown", "subcategory": "unknown_failure"},
                        "testing": {"failed_commands": [], "total_duration_ms": 40, "validation_strategy": "full", "commands": []},
                        "review": {"status": "approved", "residual_risk_count": 0},
                    },
                    ".ai-code-agent/runs/run-legacy-approved/metrics.json",
                )
            ]
        )

        self.assertEqual(trend["primary_failure_categories"], {})
        self.assertEqual(trend["primary_failure_subcategories"], {})
        self.assertEqual(trend["dashboard"]["latest_failure_category"], None)
        self.assertEqual(trend["dashboard"]["latest_failure_subcategory"], None)


if __name__ == "__main__":
    unittest.main()