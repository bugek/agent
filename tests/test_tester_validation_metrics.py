from __future__ import annotations

import unittest
from unittest.mock import patch

from ai_code_agent.agents.tester import TesterAgent
from ai_code_agent.config import AgentConfig


class StubLLM:
    def generate_json(self, system_prompt: str, user_prompt: str, schema: dict | None = None) -> dict:
        return {}


class TesterValidationMetricsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.agent = TesterAgent(AgentConfig(workspace_dir="."), StubLLM())

    def test_build_testing_summary_tracks_durations_failures_and_slowest_command(self) -> None:
        summary = self.agent._build_testing_summary(
            [
                {"label": "compileall", "exit_code": 0, "duration_ms": 120, "mode": "local", "timed_out": False},
                {"label": "script:build", "exit_code": 1, "duration_ms": 980, "mode": "local", "timed_out": False},
            ],
            ["[src/app.ts] warning"],
            {"strategy": "targeted_retry", "selected_labels": ["compileall", "script:build"], "skipped_labels": ["script:test"], "requested_retry_labels": ["script:build"]},
            {"requested_mode": "auto", "resolved_mode": "local", "started": True, "fallback_reason": "docker_unavailable"},
        )

        self.assertEqual(summary["command_count"], 2)
        self.assertEqual(summary["failed_command_count"], 1)
        self.assertEqual(summary["failed_commands"], ["script:build"])
        self.assertEqual(summary["lint_issue_count"], 1)
        self.assertEqual(summary["total_duration_ms"], 1100)
        self.assertEqual(summary["slowest_command"]["label"], "script:build")
        self.assertEqual(summary["slowest_command"]["duration_ms"], 980)
        self.assertEqual(summary["validation_strategy"], "targeted_retry")
        self.assertEqual(summary["selected_command_labels"], ["compileall", "script:build"])
        self.assertEqual(summary["skipped_command_labels"], ["script:test"])
        self.assertEqual(summary["requested_retry_labels"], ["script:build"])
        self.assertIsNone(summary["retry_policy_reason"])
        self.assertIsNone(summary["retry_policy_history_source"])
        self.assertIsNone(summary["retry_policy_confidence"])
        self.assertEqual(summary["stop_retry_after_failure"], False)
        self.assertIsNone(summary["retry_policy_stop_reason"])
        self.assertEqual(summary["sandbox_requested_mode"], "auto")
        self.assertEqual(summary["sandbox_mode"], "local")
        self.assertEqual(summary["sandbox_started"], True)
        self.assertEqual(summary["sandbox_fallback_reason"], "docker_unavailable")

    def test_build_validation_plan_targets_retry_failures_for_nextjs_workspace(self) -> None:
        workspace_profile = {
            "has_python": False,
            "has_package_json": True,
            "needs_install": True,
            "package_manager": "npm",
            "frameworks": ["nextjs"],
            "scripts": ["lint", "typecheck", "build", "test", "visual-review"],
            "nextjs": {"router_type": "app"},
            "tsconfig_exists": True,
            "lockfiles": ["package-lock.json"],
        }

        plan = self.agent._build_validation_plan(
            {
                "workspace_dir": ".",
                "retry_count": 1,
                "testing_summary": {"failed_commands": ["script:test"]},
                "review_summary": {
                    "status": "changes_required",
                    "visual_review": {"screenshot_status": "missing_artifacts", "missing_states": [], "missing_responsive_categories": []},
                    "remediation": {
                        "required": True,
                        "failed_validation_labels": ["script:test"],
                    },
                },
            },
            workspace_profile,
        )

        self.assertEqual(plan["strategy"], "targeted_retry")
        self.assertEqual(plan["requested_retry_labels"], ["script:test", "script:visual-review"])
        self.assertEqual(plan["selected_labels"], ["package-install", "script:test", "script:visual-review"])
        self.assertIn("script:lint", plan["skipped_labels"])
        self.assertIn("script:build", plan["skipped_labels"])
        self.assertEqual(plan["policy_reason"], "default_targeted_retry")
        self.assertIsNone(plan["history_source"])
        self.assertIsNone(plan["policy_confidence"])
        self.assertEqual(plan["stop_retry_after_failure"], False)
        self.assertIsNone(plan["stop_reason"])

    def test_build_validation_plan_falls_back_to_full_without_retry_signals(self) -> None:
        workspace_profile = {
            "has_python": True,
            "has_package_json": False,
        }

        plan = self.agent._build_validation_plan(
            {
                "workspace_dir": ".",
                "retry_count": 1,
                "review_summary": {"status": "changes_required", "remediation": {"required": False}},
            },
            workspace_profile,
        )

        self.assertEqual(plan["strategy"], "full")
        self.assertEqual(plan["selected_labels"], ["compileall", "cli-help"])
        self.assertEqual(plan["requested_retry_labels"], [])
        self.assertEqual(plan["policy_reason"], "remediation_not_required")

    def test_build_validation_plan_falls_back_to_full_after_targeted_retry_failure(self) -> None:
        workspace_profile = {
            "has_python": False,
            "has_package_json": True,
            "needs_install": True,
            "package_manager": "npm",
            "frameworks": ["nextjs"],
            "scripts": ["lint", "build", "test"],
            "nextjs": {"router_type": "app"},
            "lockfiles": ["package-lock.json"],
        }

        plan = self.agent._build_validation_plan(
            {
                "workspace_dir": ".",
                "retry_count": 2,
                "testing_summary": {"validation_strategy": "targeted_retry", "failed_commands": ["script:test"]},
                "review_summary": {
                    "status": "changes_required",
                    "remediation": {
                        "required": True,
                        "failed_validation_labels": ["script:test"],
                    },
                },
            },
            workspace_profile,
        )

        self.assertEqual(plan["strategy"], "full")
        self.assertEqual(plan["policy_reason"], "fallback_to_full_after_targeted_retry")
        self.assertEqual(plan["history_source"], "previous_attempt")
        self.assertEqual(plan["policy_confidence"], "strong")
        self.assertEqual(plan["stop_retry_after_failure"], True)
        self.assertEqual(plan["stop_reason"], "failed_targeted_retry_then_full_fallback")

    def test_build_validation_plan_prefers_full_when_history_is_stronger(self) -> None:
        workspace_profile = {
            "has_python": False,
            "has_package_json": True,
            "needs_install": True,
            "package_manager": "npm",
            "frameworks": ["nextjs"],
            "scripts": ["lint", "build", "test"],
            "nextjs": {"router_type": "app"},
            "lockfiles": ["package-lock.json"],
        }
        history_entries = [
            (
                {
                    "run_id": "hist-1",
                    "workflow": {"attempt_count": 2, "status": "approved"},
                    "testing": {"validation_strategy": "full", "total_duration_ms": 100},
                    "failures": {"primary_category": "validation"},
                },
                ".ai-code-agent/runs/hist-1/metrics.json",
            ),
            (
                {
                    "run_id": "hist-2",
                    "workflow": {"attempt_count": 2, "status": "approved"},
                    "testing": {"validation_strategy": "full", "total_duration_ms": 120},
                    "failures": {"primary_category": "validation"},
                },
                ".ai-code-agent/runs/hist-2/metrics.json",
            ),
            (
                {
                    "run_id": "hist-3",
                    "workflow": {"attempt_count": 2, "status": "failed"},
                    "testing": {"validation_strategy": "targeted_retry", "total_duration_ms": 200},
                    "failures": {"primary_category": "validation"},
                },
                ".ai-code-agent/runs/hist-3/metrics.json",
            ),
            (
                {
                    "run_id": "hist-4",
                    "workflow": {"attempt_count": 2, "status": "failed"},
                    "testing": {"validation_strategy": "targeted_retry", "total_duration_ms": 180},
                    "failures": {"primary_category": "validation"},
                },
                ".ai-code-agent/runs/hist-4/metrics.json",
            ),
        ]

        with patch("ai_code_agent.agents.tester.list_execution_metrics_artifacts", return_value=history_entries):
            plan = self.agent._build_validation_plan(
                {
                    "workspace_dir": ".",
                    "run_id": "run-current",
                    "retry_count": 1,
                    "execution_metrics": {"failures": {"primary_category": "validation"}},
                    "testing_summary": {"failed_commands": ["script:test"]},
                    "review_summary": {
                        "status": "changes_required",
                        "remediation": {
                            "required": True,
                            "failed_validation_labels": ["script:test"],
                        },
                    },
                },
                workspace_profile,
            )

        self.assertEqual(plan["strategy"], "full")
        self.assertEqual(plan["policy_reason"], "history_prefers_full")
        self.assertEqual(plan["history_source"], "failure_category")
        self.assertEqual(plan["policy_confidence"], "strong")
        self.assertEqual(plan["stop_retry_after_failure"], False)
        self.assertEqual(plan["selected_labels"], ["package-install", "script:lint", "script:build", "script:test", "next:router-detected"])

    def test_build_validation_plan_stops_after_low_recovery_history(self) -> None:
        workspace_profile = {
            "has_python": False,
            "has_package_json": True,
            "needs_install": True,
            "package_manager": "npm",
            "frameworks": ["nextjs"],
            "scripts": ["lint", "build", "test"],
            "nextjs": {"router_type": "app"},
            "lockfiles": ["package-lock.json"],
        }
        history_entries = [
            (
                {
                    "run_id": "hist-1",
                    "workflow": {"attempt_count": 2, "status": "failed"},
                    "testing": {"validation_strategy": "full", "total_duration_ms": 300},
                    "failures": {"primary_category": "validation"},
                },
                ".ai-code-agent/runs/hist-1/metrics.json",
            ),
            (
                {
                    "run_id": "hist-2",
                    "workflow": {"attempt_count": 2, "status": "failed"},
                    "testing": {"validation_strategy": "full", "total_duration_ms": 320},
                    "failures": {"primary_category": "validation"},
                },
                ".ai-code-agent/runs/hist-2/metrics.json",
            ),
            (
                {
                    "run_id": "hist-3",
                    "workflow": {"attempt_count": 2, "status": "failed"},
                    "testing": {"validation_strategy": "targeted_retry", "total_duration_ms": 140},
                    "failures": {"primary_category": "validation"},
                },
                ".ai-code-agent/runs/hist-3/metrics.json",
            ),
            (
                {
                    "run_id": "hist-4",
                    "workflow": {"attempt_count": 2, "status": "failed"},
                    "testing": {"validation_strategy": "targeted_retry", "total_duration_ms": 130},
                    "failures": {"primary_category": "validation"},
                },
                ".ai-code-agent/runs/hist-4/metrics.json",
            ),
        ]

        with patch("ai_code_agent.agents.tester.list_execution_metrics_artifacts", return_value=history_entries):
            plan = self.agent._build_validation_plan(
                {
                    "workspace_dir": ".",
                    "run_id": "run-current",
                    "retry_count": 1,
                    "execution_metrics": {"failures": {"primary_category": "validation"}},
                    "testing_summary": {"failed_commands": ["script:test"]},
                    "review_summary": {
                        "status": "changes_required",
                        "remediation": {
                            "required": True,
                            "failed_validation_labels": ["script:test"],
                        },
                    },
                },
                workspace_profile,
            )

        self.assertEqual(plan["strategy"], "targeted_retry")
        self.assertEqual(plan["policy_reason"], "history_prefers_targeted_retry")
        self.assertEqual(plan["history_source"], "failure_category")
        self.assertEqual(plan["policy_confidence"], "weak")
        self.assertEqual(plan["stop_retry_after_failure"], True)
        self.assertEqual(plan["stop_reason"], "history_low_recovery_probability")


if __name__ == "__main__":
    unittest.main()