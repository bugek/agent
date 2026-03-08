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
        )

        self.assertEqual(summary["command_count"], 2)
        self.assertEqual(summary["failed_command_count"], 1)
        self.assertEqual(summary["failed_commands"], ["script:build"])
        self.assertEqual(summary["lint_issue_count"], 1)
        self.assertEqual(summary["total_duration_ms"], 1100)
        self.assertEqual(summary["slowest_command"]["label"], "script:build")
        self.assertEqual(summary["slowest_command"]["duration_ms"], 980)


if __name__ == "__main__":
    unittest.main()