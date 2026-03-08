from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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

    def test_planner_includes_version_resolution_for_dependency_upgrade_requests(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "app").mkdir(parents=True)
            (workspace / "app/layout.tsx").write_text("export default function RootLayout({ children }) { return <html><body>{children}</body></html>; }\n", encoding="utf-8")
            (workspace / "package.json").write_text(
                json.dumps(
                    {
                        "name": "demo",
                        "version": "0.1.0",
                        "dependencies": {"next": "14.2.16", "react": "18.3.1", "react-dom": "18.3.1"},
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            llm = CapturingPlannerLLM()
            planner = PlannerAgent(AgentConfig(workspace_dir=temp_dir), llm)
            with patch(
                "ai_code_agent.agents.planner.resolve_workspace_version_context",
                return_value={
                    "dependency_upgrade_request": True,
                    "package_name": "next",
                    "current_version": "14.2.16",
                    "baseline_version": "16.1.6",
                    "latest_version": "16.1.6",
                    "selected_version": "16.1.6",
                    "selection_reason": "prefer_project_baseline",
                    "requires_version_display": True,
                    "package_json_version": "0.1.0",
                    "dist_tags": {"latest": "16.1.6"},
                },
            ):
                result = planner.run(
                    {
                        "issue_description": "upgrade Next.js and display app version from package.json",
                        "workspace_dir": temp_dir,
                    }
                )

            self.assertIsNotNone(llm.payload)
            self.assertEqual(llm.payload["version_resolution"]["selected_version"], "16.1.6")
            self.assertEqual(result["planning_context"]["version_resolution"]["selection_reason"], "prefer_project_baseline")
            self.assertEqual(result["files_to_edit"][:2], ["package.json", "app/layout.tsx"])


if __name__ == "__main__":
    unittest.main()