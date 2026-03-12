from __future__ import annotations

import unittest

from ai_code_agent.agents.reviewer import ReviewerAgent
from ai_code_agent.config import AgentConfig


class StubLLM:
    def __init__(self, response: dict):
        self._response = response

    def generate_json(self, system_prompt: str, user_prompt: str, schema: dict | None = None) -> dict:
        return dict(self._response)


class ReviewerNormalizationTest(unittest.TestCase):
    def test_visual_review_phrase_is_not_treated_as_analysis_only(self) -> None:
        reviewer = ReviewerAgent(
            AgentConfig(workspace_dir="."),
            StubLLM({"review_comments": [], "review_approved": True}),
        )

        result = reviewer.run(
            {
                "issue_description": "Build, typecheck, and visual-review flows continue to pass while adding React Flow.",
                "workspace_dir": ".",
                "patches": [],
                "test_passed": True,
                "test_results": "compileall(exit=0):\n",
                "codegen_summary": {},
            }
        )

        self.assertFalse(result["review_approved"])
        self.assertIn("No code changes were produced for a change-oriented request.", result["review_comments"])

    def test_string_review_comment_is_wrapped_as_single_list_item(self) -> None:
        reviewer = ReviewerAgent(
            AgentConfig(workspace_dir="."),
            StubLLM({"review_comments": "single comment", "review_approved": True}),
        )

        result = reviewer.run(
            {
                "issue_description": "analyze current repository and summarize readiness",
                "workspace_dir": ".",
                "patches": [],
                "test_passed": True,
                "test_results": "compileall(exit=0):\n",
                "codegen_summary": {},
            }
        )

        self.assertEqual(result["review_comments"], ["single comment"])
        self.assertTrue(result["review_approved"])

    def test_list_review_comments_filters_empty_and_non_string_values(self) -> None:
        reviewer = ReviewerAgent(
            AgentConfig(workspace_dir="."),
            StubLLM({"review_comments": ["keep this", "", 123, None, "and this"], "review_approved": True}),
        )

        result = reviewer.run(
            {
                "issue_description": "analyze current repository and summarize readiness",
                "workspace_dir": ".",
                "patches": [],
                "test_passed": True,
                "test_results": "compileall(exit=0):\n",
                "codegen_summary": {},
            }
        )

        self.assertEqual(result["review_comments"], ["keep this", "and this"])
        self.assertTrue(result["review_approved"])


if __name__ == "__main__":
    unittest.main()