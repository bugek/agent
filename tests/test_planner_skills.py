from __future__ import annotations

import unittest
from pathlib import Path

from ai_code_agent.agents.planner import PlannerAgent
from ai_code_agent.config import AgentConfig


REPO_ROOT = Path(__file__).resolve().parents[1]


class NullLLM:
    def generate_json(self, system_prompt: str, user_prompt: str, schema: dict | None = None) -> dict:
        return {}


class PlannerSkillsTest(unittest.TestCase):
    def test_planner_surfaces_selected_skills_in_planning_context(self) -> None:
        planner = PlannerAgent(AgentConfig(workspace_dir=str(REPO_ROOT)), NullLLM())

        result = planner.run(
            {
                "issue_description": "create a 1.0 checklist and validate the runtime support matrix before release",
                "workspace_dir": str(REPO_ROOT),
            }
        )

        planning_context = result["planning_context"]
        self.assertGreaterEqual(planning_context["available_skill_count"], 2)
        self.assertTrue(planning_context["selected_skills"])
        self.assertIn("release-readiness", {item["name"] for item in planning_context["selected_skills"]})
        self.assertEqual(planning_context["skill_invocations"][0]["name"], "release-readiness")
        self.assertEqual(planning_context["skill_invocations"][0]["phase"], "plan")
        self.assertEqual(planning_context["skill_invocations"][0]["outcome"], "applied")

    def test_planner_blocks_skills_with_disallowed_permissions(self) -> None:
        planner = PlannerAgent(
            AgentConfig(
                workspace_dir=str(REPO_ROOT),
                skill_allowed_permissions=["read-only"],
            ),
            NullLLM(),
        )

        result = planner.run(
            {
                "issue_description": "start a compose sandbox stack for integration testing",
                "workspace_dir": str(REPO_ROOT),
            }
        )

        planning_context = result["planning_context"]
        self.assertNotIn("compose-stack", {item["name"] for item in planning_context["selected_skills"]})
        self.assertTrue(planning_context["blocked_skills"])
        self.assertEqual(planning_context["blocked_skills"][0]["name"], "compose-stack")
        self.assertEqual(planning_context["blocked_skills"][0]["permission"], "sandbox")
        blocked_invocations = {
            item["name"]: item["outcome"]
            for item in planning_context["skill_invocations"]
            if item.get("outcome") == "blocked"
        }
        self.assertEqual(blocked_invocations["compose-stack"], "blocked")


if __name__ == "__main__":
    unittest.main()