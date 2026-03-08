from __future__ import annotations

import unittest

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


if __name__ == "__main__":
    unittest.main()