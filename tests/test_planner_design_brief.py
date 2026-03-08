from __future__ import annotations

import unittest

from ai_code_agent.agents.planner import PlannerAgent
from ai_code_agent.config import AgentConfig


class NullLLM:
    def generate_json(self, system_prompt: str, user_prompt: str, schema: dict | None = None) -> dict:
        return {}


class PlannerDesignBriefTest(unittest.TestCase):
    def setUp(self) -> None:
        self.planner = PlannerAgent(AgentConfig(workspace_dir="."), NullLLM())

    def test_extract_design_brief_for_dashboard_frontend_request(self) -> None:
        design_brief = self.planner._extract_design_brief(
            "revamp dashboard page with bold signal-rich visuals and warm amber accents",
            {"nextjs": {"router_type": "app"}},
        )

        self.assertIsNotNone(design_brief)
        self.assertEqual(design_brief["style_family"], "dashboard")
        self.assertEqual(design_brief["visual_tone"], "signal-rich")
        self.assertEqual(design_brief["palette_hint"], "warm")
        self.assertEqual(design_brief["state_coverage"], ["loading", "empty", "error", "success"])

    def test_extract_design_brief_for_calm_profile_request(self) -> None:
        design_brief = self.planner._extract_design_brief(
            "create calm minimal profile page with cool slate styling",
            {"nextjs": {"router_type": "app"}},
        )

        self.assertIsNotNone(design_brief)
        self.assertEqual(design_brief["style_family"], "calm")
        self.assertEqual(design_brief["visual_tone"], "calm")
        self.assertEqual(design_brief["palette_hint"], "cool")

    def test_extract_design_brief_returns_none_for_non_frontend_request(self) -> None:
        design_brief = self.planner._extract_design_brief(
            "fix payments repository query",
            {"nextjs": None},
        )

        self.assertIsNone(design_brief)


if __name__ == "__main__":
    unittest.main()