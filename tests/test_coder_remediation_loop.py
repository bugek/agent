from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ai_code_agent.agents.coder import CoderAgent
from ai_code_agent.config import AgentConfig


class CapturingCoderLLM:
    def __init__(self) -> None:
        self.payload: dict | None = None

    def generate_json(self, system_prompt: str, user_prompt: str, schema: dict | None = None) -> dict:
        self.payload = json.loads(user_prompt)
        return {
            "operations": [
                {
                    "type": "write_file",
                    "file_path": "app/dashboard/page.tsx",
                    "content": "export default function Page() {\n  return <main>Retry fix</main>;\n}\n",
                }
            ]
        }


class CoderRemediationLoopTest(unittest.TestCase):
    def test_retry_path_uses_llm_with_remediation_context_instead_of_scaffold(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "app/dashboard").mkdir(parents=True)
            (workspace / "app/dashboard/page.tsx").write_text("export default function Page() { return null; }\n", encoding="utf-8")

            llm = CapturingCoderLLM()
            coder = CoderAgent(AgentConfig(workspace_dir=temp_dir), llm)

            result = coder.run(
                {
                    "issue_description": "revamp dashboard page with hero section",
                    "workspace_dir": temp_dir,
                    "workspace_profile": {
                        "nextjs": {
                            "router_type": "app",
                            "app_dir": "app",
                            "pages_dir": None,
                            "component_directories": ["components"],
                        }
                    },
                    "files_to_edit": ["app/dashboard/page.tsx"],
                    "patches": [{"file": "app/dashboard/page.tsx"}],
                    "plan": "Fix the dashboard page.",
                    "retry_count": 1,
                    "review_summary": {
                        "status": "changes_required",
                        "remediation": {
                            "required": True,
                            "failed_validation_labels": ["script:test"],
                            "blocked_file_paths": [],
                            "failed_operations": [],
                            "focus_areas": ["app/dashboard/page.tsx"],
                            "guidance": ["Fix the failing dashboard render path."],
                        },
                    },
                    "testing_summary": {"failed_commands": ["script:test"], "total_duration_ms": 10},
                    "planning_context": {},
                }
            )

            self.assertEqual(result["codegen_summary"]["generated_by"], "llm")
            self.assertEqual(result["codegen_summary"]["retry_count"], 1)
            self.assertEqual(result["codegen_summary"]["remediation_applied"], True)
            self.assertEqual(result["codegen_summary"]["remediation_focus_count"], 1)
            self.assertEqual(len(result["patches"]), 1)
            self.assertEqual(result["patches"][0]["file"], "app/dashboard/page.tsx")
            self.assertIsNotNone(llm.payload)
            self.assertEqual(llm.payload["retry_count"], 1)
            self.assertEqual(llm.payload["remediation"]["source"], "review_loop")
            self.assertEqual(llm.payload["edit_intent"], [])
            self.assertEqual(llm.payload["remediation"]["failed_validation_labels"], ["script:test"])
            self.assertEqual(llm.payload["remediation"]["focus_areas"], ["app/dashboard/page.tsx"])
            self.assertEqual(llm.payload["files"][0]["file_path"], "app/dashboard/page.tsx")
            self.assertIn("Retry fix", (workspace / "app/dashboard/page.tsx").read_text(encoding="utf-8"))

    def test_retry_path_includes_planner_edit_intent_in_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "app/dashboard").mkdir(parents=True)
            (workspace / "app/dashboard/page.tsx").write_text("export default function Page() { return null; }\n", encoding="utf-8")

            llm = CapturingCoderLLM()
            coder = CoderAgent(AgentConfig(workspace_dir=temp_dir), llm)

            coder.run(
                {
                    "issue_description": "revamp dashboard page with hero section",
                    "workspace_dir": temp_dir,
                    "workspace_profile": {
                        "nextjs": {
                            "router_type": "app",
                            "app_dir": "app",
                            "pages_dir": None,
                            "component_directories": ["components"],
                        }
                    },
                    "files_to_edit": ["app/dashboard/page.tsx"],
                    "patches": [{"file": "app/dashboard/page.tsx"}],
                    "plan": "Fix the dashboard page.",
                    "retry_count": 1,
                    "review_summary": {
                        "status": "changes_required",
                        "remediation": {
                            "required": True,
                            "failed_validation_labels": ["script:test"],
                            "blocked_file_paths": [],
                            "failed_operations": [],
                            "focus_areas": ["app/dashboard/page.tsx"],
                            "guidance": ["Fix the failing dashboard render path."],
                        },
                    },
                    "testing_summary": {"failed_commands": ["script:test"], "total_duration_ms": 10},
                    "planning_context": {
                        "edit_intent": [
                            {
                                "file_path": "app/dashboard/page.tsx",
                                "intent": "Repair the dashboard route render path.",
                                "reason": "The previous attempt failed script:test.",
                                "validation_targets": ["script:test"],
                            }
                        ]
                    },
                }
            )

            self.assertIsNotNone(llm.payload)
            self.assertEqual(llm.payload["edit_intent"][0]["file_path"], "app/dashboard/page.tsx")
            self.assertEqual(llm.payload["edit_intent"][0]["validation_targets"], ["script:test"])


if __name__ == "__main__":
    unittest.main()