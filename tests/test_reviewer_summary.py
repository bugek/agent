from __future__ import annotations

import unittest

from ai_code_agent.agents.reviewer import ReviewerAgent
from ai_code_agent.config import AgentConfig


class StubLLM:
    def __init__(self, response: dict):
        self._response = response

    def generate_json(self, system_prompt: str, user_prompt: str, schema: dict | None = None) -> dict:
        return dict(self._response)


class ReviewerSummaryTest(unittest.TestCase):
    def test_review_summary_includes_changed_areas_validation_and_risks(self) -> None:
        reviewer = ReviewerAgent(
            AgentConfig(workspace_dir="."),
            StubLLM({"review_comments": ["Needs follow-up on generated docs."], "review_approved": True}),
        )

        result = reviewer.run(
            {
                "issue_description": "update dashboard page",
                "workspace_dir": ".",
                "patches": [
                    {"file": "ai_code_agent/agents/reviewer.py"},
                    {"file": "tests/test_reviewer_summary.py"},
                ],
                "test_passed": True,
                "test_results": "compileall(exit=0):\n\nscript:test(exit=0):\n",
                "codegen_summary": {
                    "blocked_operations": [
                        {"file_path": "artifact/fixtures/demo.txt", "reason": "matched deny rule: artifact/fixtures/**"}
                    ]
                },
                "visual_review": {
                    "enabled": True,
                    "screenshot_status": "passed",
                    "artifact_count": 2,
                    "state_coverage": {
                        "loading_file": True,
                        "error_file": True,
                        "loading_state": True,
                        "empty_state": True,
                        "error_state": True,
                        "success_state": True,
                    },
                    "responsive_review": {
                        "categories_present": ["desktop", "mobile"],
                        "missing_categories": [],
                    },
                },
            }
        )

        summary = result["review_summary"]
        self.assertEqual(summary["status"], "approved")
        self.assertEqual(summary["changed_areas"], ["ai_code_agent/agents", "tests/test_reviewer_summary.py"])
        self.assertEqual(summary["validation"]["passed"], ["compileall", "script:test"])
        self.assertEqual(summary["validation"]["failed"], [])
        self.assertEqual(summary["visual_review"]["screenshot_status"], "passed")
        self.assertEqual(summary["visual_review"]["responsive_categories"], ["desktop", "mobile"])
        self.assertIn("1 operation(s) were blocked by file edit policy.", summary["residual_risks"])
        self.assertIn("Needs follow-up on generated docs.", summary["residual_risks"])


if __name__ == "__main__":
    unittest.main()