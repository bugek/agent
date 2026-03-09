from __future__ import annotations

import json
import unittest

from ai_code_agent.llm.client import LLMClient


class LLMClientFallbackReviewTest(unittest.TestCase):
    def test_fallback_review_approves_clean_change_payload(self) -> None:
        client = LLMClient(provider="openai", api_key="")
        prompt = json.dumps(
            {
                "patch_count": 1,
                "changed_files": ["app/page.tsx"],
                "validation_signals": [{"label": "script:build", "exit_code": 0}],
                "test_results": "script:build(exit=0):\n",
                "visual_review": {
                    "enabled": True,
                    "screenshot_status": "passed",
                    "responsive_review": {"missing_categories": [], "missing_viewport_metadata": []},
                },
                "codegen_summary": {"failed_operations": []},
                "analysis_only": False,
            }
        )

        result = client.generate_json("return review_approved", prompt)

        self.assertTrue(result["review_approved"])
        self.assertEqual(result["review_comments"], ["Fallback review completed without an LLM provider."])

    def test_fallback_review_rejects_failed_validation_payload(self) -> None:
        client = LLMClient(provider="openai", api_key="")
        prompt = json.dumps(
            {
                "patch_count": 1,
                "changed_files": ["app/page.tsx"],
                "validation_signals": [{"label": "script:build", "exit_code": 1}],
                "test_results": "script:build(exit=1):\nboom\n",
                "visual_review": None,
                "codegen_summary": {"failed_operations": []},
                "analysis_only": False,
            }
        )

        result = client.generate_json("return review_approved", prompt)

        self.assertFalse(result["review_approved"])
        self.assertEqual(result["review_comments"], ["Fallback review detected a failure signal in the review payload."])


if __name__ == "__main__":
    unittest.main()
