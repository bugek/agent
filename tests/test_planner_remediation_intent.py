from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ai_code_agent.agents.planner import PlannerAgent
from ai_code_agent.config import AgentConfig


class CapturingPlannerLLM:
    def __init__(self) -> None:
        self.payload: dict | None = None

    def generate_json(self, system_prompt: str, user_prompt: str, schema: dict | None = None) -> dict:
        self.payload = json.loads(user_prompt)
        return {
            "plan": [
                "Inspect the failing dashboard page.",
                "Repair the route render path and keep validation focused on the failed checks.",
            ],
            "files_to_edit": ["app/dashboard/page.tsx"],
            "edit_intent": [
                {
                    "file_path": "app/dashboard/page.tsx",
                    "intent": "Fix the dashboard route regression.",
                    "reason": "script:test failed on the previous attempt.",
                    "validation_targets": ["script:test"],
                }
            ],
        }


class PlannerRemediationIntentTest(unittest.TestCase):
    def test_planner_includes_retry_remediation_and_edit_intent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "app/dashboard").mkdir(parents=True)
            (workspace / "app/dashboard/page.tsx").write_text("export default function Page() { return null; }\n", encoding="utf-8")
            (workspace / "app/dashboard/loading.tsx").write_text("export default function Loading() { return null; }\n", encoding="utf-8")
            (workspace / "app/dashboard/error.tsx").write_text("export default function Error() { return null; }\n", encoding="utf-8")

            llm = CapturingPlannerLLM()
            planner = PlannerAgent(AgentConfig(workspace_dir=temp_dir), llm)
            result = planner.run(
                {
                    "issue_description": "revamp dashboard page with hero section",
                    "workspace_dir": temp_dir,
                    "retry_count": 1,
                    "review_summary": {
                        "status": "changes_required",
                        "remediation": {
                            "required": True,
                            "failed_validation_labels": ["script:test"],
                            "focus_areas": ["app/dashboard/page.tsx"],
                            "guidance": ["Fix the failing dashboard route before broadening validation."],
                            "failed_operations": [],
                        },
                    },
                }
            )

            self.assertIsNotNone(llm.payload)
            self.assertEqual(llm.payload["retry_count"], 1)
            self.assertEqual(llm.payload["remediation"]["failed_validation_labels"], ["script:test"])
            self.assertEqual(llm.payload["candidate_files"][0], "app/dashboard/page.tsx")
            self.assertEqual(result["files_to_edit"], ["app/dashboard/page.tsx", "app/dashboard/loading.tsx", "app/dashboard/error.tsx"])
            self.assertEqual(result["planning_context"]["remediation"]["focus_areas"], ["app/dashboard/page.tsx", "app/dashboard/loading.tsx", "app/dashboard/error.tsx"])
            self.assertEqual(result["planning_context"]["edit_intent"][0]["file_path"], "app/dashboard/page.tsx")
            self.assertEqual(result["planning_context"]["edit_intent"][0]["validation_targets"], ["script:test"])
            self.assertIn("Inspect the failing dashboard page.", result["plan"])


if __name__ == "__main__":
    unittest.main()