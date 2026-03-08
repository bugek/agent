from __future__ import annotations

import json
import unittest

from ai_code_agent.agents.reviewer import ReviewerAgent
from ai_code_agent.config import AgentConfig


class StubLLM:
    def __init__(self, response: dict):
        self._response = response

    def generate_json(self, system_prompt: str, user_prompt: str, schema: dict | None = None) -> dict:
        return dict(self._response)


class RaisingStubLLM:
    def generate_json(self, system_prompt: str, user_prompt: str, schema: dict | None = None) -> dict:
        raise TimeoutError("review timeout")


class CapturingReviewLLM:
    def __init__(self) -> None:
        self.payload: dict | None = None

    def generate_json(self, system_prompt: str, user_prompt: str, schema: dict | None = None) -> dict:
        self.payload = json.loads(user_prompt)
        return {"review_comments": [], "review_approved": True}


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
        self.assertEqual(summary["remediation"]["required"], False)

    def test_review_summary_includes_remediation_for_retry_loop(self) -> None:
        reviewer = ReviewerAgent(
            AgentConfig(workspace_dir="."),
            StubLLM({"review_comments": ["Fix the failing compile step in the generated controller."], "review_approved": False}),
        )

        result = reviewer.run(
            {
                "issue_description": "add controller endpoint",
                "workspace_dir": ".",
                "patches": [{"file": "src/users/users.controller.ts"}],
                "test_passed": False,
                "test_results": "compileall(exit=1):\nboom\nscript:test(exit=0):\n",
                "codegen_summary": {
                    "failed_operations": ["replace_text failed for src/users/users.controller.ts"],
                    "blocked_operations": [
                        {"file_path": "artifact/fixtures/demo.txt", "reason": "matched deny rule: artifact/fixtures/**"}
                    ],
                },
                "visual_review": None,
            }
        )

        remediation = result["review_summary"]["remediation"]
        self.assertEqual(remediation["required"], True)
        self.assertEqual(remediation["failed_validation_labels"], ["compileall"])
        self.assertEqual(remediation["blocked_file_paths"], ["artifact/fixtures/demo.txt"])
        self.assertEqual(remediation["failed_operations"], ["replace_text failed for src/users/users.controller.ts"])
        self.assertIn("src/users/users.controller.ts", remediation["focus_areas"])
        self.assertIn("Fix the failing compile step in the generated controller.", remediation["guidance"])

    def test_reviewer_falls_back_when_llm_review_fails(self) -> None:
        reviewer = ReviewerAgent(AgentConfig(workspace_dir="."), RaisingStubLLM())

        result = reviewer.run(
            {
                "issue_description": "update dashboard page",
                "workspace_dir": ".",
                "patches": [{"file": "app/page.tsx"}],
                "test_passed": True,
                "test_results": "script:build(exit=0):\n",
                "codegen_summary": {},
                "visual_review": None,
            }
        )

        self.assertTrue(result["review_approved"])
        self.assertIn("Reviewer LLM request failed; using deterministic fallback review.", result["review_comments"])

    def test_reviewer_payload_ignores_responsive_gaps_without_screenshots(self) -> None:
        llm = CapturingReviewLLM()
        reviewer = ReviewerAgent(AgentConfig(workspace_dir="."), llm)

        reviewer.run(
            {
                "issue_description": "update dashboard page",
                "workspace_dir": ".",
                "patches": [{"file": "app/page.tsx"}],
                "test_passed": True,
                "test_results": "script:build(exit=0):\n",
                "codegen_summary": {},
                "visual_review": {
                    "enabled": True,
                    "screenshot_status": "not_configured",
                    "state_coverage": {
                        "loading_file": True,
                        "error_file": True,
                        "loading_state": True,
                        "empty_state": True,
                        "error_state": True,
                        "success_state": True,
                    },
                    "responsive_review": {
                        "categories_present": [],
                        "missing_categories": ["desktop", "mobile"],
                        "missing_viewport_metadata": [".ai-code-agent/visual-review/screenshots/demo.png"],
                    },
                },
            }
        )

        self.assertIsNotNone(llm.payload)
        self.assertEqual(llm.payload["visual_review"]["responsive_review"]["missing_categories"], [])
        self.assertEqual(llm.payload["visual_review"]["responsive_review"]["missing_viewport_metadata"], [])
        self.assertEqual(llm.payload["visual_review"]["responsive_review"]["passed"], True)

    def test_reviewer_payload_includes_structured_version_evidence(self) -> None:
        llm = CapturingReviewLLM()
        reviewer = ReviewerAgent(AgentConfig(workspace_dir="."), llm)

        reviewer.run(
            {
                "issue_description": "upgrade Next.js and display app version from package.json",
                "workspace_dir": ".",
                "patches": [
                    {
                        "file": "package.json",
                        "diff": "--- package.json\n+++ package.json\n-    \"next\": \"14.2.16\"\n+    \"next\": \"16.1.6\"\n",
                    }
                ],
                "test_passed": True,
                "test_results": "script:typecheck(exit=0):\n\nscript:build(exit=0):\n",
                "planning_context": {
                    "version_resolution": {
                        "selected_version": "16.1.6",
                        "latest_version": "16.1.6",
                        "selection_reason": "prefer_project_baseline",
                    }
                },
                "codegen_summary": {},
                "visual_review": None,
            }
        )

        self.assertIsNotNone(llm.payload)
        self.assertEqual(llm.payload["version_resolution"]["selected_version"], "16.1.6")
        self.assertEqual(llm.payload["dependency_changes"]["next"]["before"], "14.2.16")
        self.assertEqual(llm.payload["dependency_changes"]["next"]["after"], "16.1.6")




if __name__ == "__main__":
    unittest.main()