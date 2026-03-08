import unittest

from ai_code_agent.agents.reviewer import ReviewerAgent
from ai_code_agent.config import AgentConfig


class StubLLM:
    def generate_json(self, system_prompt: str, user_prompt: str, schema: dict | None = None) -> dict:
        return {"review_comments": [], "review_approved": True}


class TestReviewerVisualReview(unittest.TestCase):
    def setUp(self) -> None:
        self.agent = ReviewerAgent(AgentConfig(workspace_dir="."), StubLLM())

    def test_visual_review_has_blockers_for_missing_states(self) -> None:
        visual_review = {
            "enabled": True,
            "state_coverage": {
                "loading_file": True,
                "error_file": True,
                "loading_state": True,
                "empty_state": False,
                "error_state": True,
                "success_state": True,
            },
            "screenshot_status": "passed",
        }

        self.assertTrue(self.agent._visual_review_has_blockers(visual_review))

    def test_visual_review_comments_include_structural_fallback_message(self) -> None:
        visual_review = {
            "enabled": True,
            "state_coverage": {
                "loading_file": True,
                "error_file": True,
                "loading_state": True,
                "empty_state": True,
                "error_state": True,
                "success_state": True,
            },
            "screenshot_status": "not_configured",
        }

        comments = self.agent._visual_review_comments(visual_review, analysis_only=False)

        self.assertIn("Frontend screenshot review is not configured; relying on structural visual checks only.", comments)

    def test_visual_review_has_blockers_when_artifacts_are_missing(self) -> None:
        visual_review = {
            "enabled": True,
            "state_coverage": {
                "loading_file": True,
                "error_file": True,
                "loading_state": True,
                "empty_state": True,
                "error_state": True,
                "success_state": True,
            },
            "screenshot_status": "missing_artifacts",
        }

        self.assertTrue(self.agent._visual_review_has_blockers(visual_review))

    def test_visual_review_comments_include_missing_artifacts_message(self) -> None:
        visual_review = {
            "enabled": True,
            "state_coverage": {
                "loading_file": True,
                "error_file": True,
                "loading_state": True,
                "empty_state": True,
                "error_state": True,
                "success_state": True,
            },
            "screenshot_status": "missing_artifacts",
        }

        comments = self.agent._visual_review_comments(visual_review, analysis_only=False)

        self.assertIn(
            "Frontend screenshot command completed without producing any screenshot artifacts or manifest metadata.",
            comments,
        )

    def test_visual_review_has_blockers_when_responsive_coverage_is_missing(self) -> None:
        visual_review = {
            "enabled": True,
            "state_coverage": {
                "loading_file": True,
                "error_file": True,
                "loading_state": True,
                "empty_state": True,
                "error_state": True,
                "success_state": True,
            },
            "screenshot_status": "passed",
            "responsive_review": {
                "missing_categories": ["mobile"],
                "missing_viewport_metadata": [],
            },
        }

        self.assertTrue(self.agent._visual_review_has_blockers(visual_review))

    def test_visual_review_comments_include_missing_responsive_coverage_message(self) -> None:
        visual_review = {
            "enabled": True,
            "state_coverage": {
                "loading_file": True,
                "error_file": True,
                "loading_state": True,
                "empty_state": True,
                "error_state": True,
                "success_state": True,
            },
            "screenshot_status": "passed",
            "responsive_review": {
                "missing_categories": ["mobile"],
                "missing_viewport_metadata": [],
            },
        }

        comments = self.agent._visual_review_comments(visual_review, analysis_only=False)

        self.assertIn("Frontend visual review is missing responsive viewport coverage for: mobile.", comments)


if __name__ == "__main__":
    unittest.main()