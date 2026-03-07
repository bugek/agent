from __future__ import annotations

import os
import unittest
from pathlib import Path

from ai_code_agent.agents.planner import PlannerAgent
from ai_code_agent.config import AgentConfig


FIXTURE_DIR = Path(__file__).resolve().parents[1] / "artifact" / "fixtures" / "retrieval-eval-sample"


class NullLLM:
    def generate_json(self, system_prompt: str, user_prompt: str, schema: dict | None = None) -> dict:
        return {}


class PlannerCandidateExplanationsTest(unittest.TestCase):
    def test_planning_context_includes_structured_candidate_explanations(self) -> None:
        previous_mode = os.environ.get("RETRIEVAL_MODE")
        os.environ["RETRIEVAL_MODE"] = "hybrid"
        try:
            planner = PlannerAgent(AgentConfig(workspace_dir=str(FIXTURE_DIR)), NullLLM())
            result = planner.run(
                {
                    "issue_description": "update payments webhook event handling",
                    "workspace_dir": str(FIXTURE_DIR),
                }
            )
        finally:
            if previous_mode is None:
                os.environ.pop("RETRIEVAL_MODE", None)
            else:
                os.environ["RETRIEVAL_MODE"] = previous_mode

        planning_context = result["planning_context"]
        self.assertEqual(planning_context["candidate_explanations_schema_version"], 2)
        self.assertEqual(planning_context["retrieval_strategy"], "hybrid")
        self.assertTrue(planning_context["graph_seed_files"])

        candidate_scores = planning_context["candidate_scores"]
        candidate_explanations = planning_context["candidate_explanations"]
        self.assertEqual(len(candidate_explanations), len(candidate_scores))

        explanations_by_path = {
            explanation["file_path"]: explanation for explanation in candidate_explanations
        }
        self.assertIn("backend/payments/webhook.py", result["files_to_edit"])
        self.assertIn("backend/payments/webhook.py", explanations_by_path)

        webhook_explanation = explanations_by_path["backend/payments/webhook.py"]
        self.assertIn("kind", webhook_explanation)
        self.assertIn("reasons", webhook_explanation)
        self.assertIn("explanation_edges", webhook_explanation)
        self.assertTrue(webhook_explanation["reasons"])
        self.assertTrue(webhook_explanation["explanation_edges"])

        for explanation in candidate_explanations:
            self.assertIn("file_path", explanation)
            self.assertIn("kind", explanation)
            self.assertIn("path_overlap", explanation)
            self.assertIn("symbol_overlap", explanation)
            self.assertIn("import_overlap", explanation)
            self.assertIn("reasons", explanation)
            self.assertIn("explanation_edges", explanation)

            for edge in explanation["explanation_edges"]:
                self.assertIn("edge_type", edge)
                self.assertIn("target_file", edge)
                self.assertIn("direction", edge)
                self.assertIn("depth", edge)


if __name__ == "__main__":
    unittest.main()