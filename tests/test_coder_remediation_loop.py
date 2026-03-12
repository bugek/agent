from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ai_code_agent.agents.coder import CoderAgent
from ai_code_agent.config import AgentConfig
from ai_code_agent.tools.file_editor import FileEditor


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
    def test_visual_review_phrase_does_not_trigger_analysis_only(self) -> None:
        coder = CoderAgent(AgentConfig(workspace_dir="."), CapturingCoderLLM())

        self.assertFalse(
            coder._is_analysis_only(
                "Build, typecheck, and visual-review flows continue to pass while adding a React Flow page."
            )
        )

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
                            "task_remediation": [
                                {
                                    "task_id": "T2",
                                    "blocker_types": ["validation_failure"],
                                    "focus_areas": ["app/dashboard/page.tsx"],
                                    "guidance": ["Repair the dashboard task before retrying."],
                                }
                            ],
                        },
                    },
                    "testing_summary": {"failed_commands": ["script:test"], "total_duration_ms": 10},
                    "planning_context": {},
                }
            )

            self.assertEqual(result["codegen_summary"]["generated_by"], "llm")
            self.assertEqual(result["codegen_summary"]["retry_count"], 1)
            self.assertEqual(result["codegen_summary"]["remediation_applied"], True)
            self.assertEqual(result["codegen_summary"]["remediation_focus_count"], 3)
            self.assertEqual(len(result["patches"]), 1)
            self.assertEqual(result["patches"][0]["file"], "app/dashboard/page.tsx")
            self.assertIsNotNone(llm.payload)
            self.assertEqual(llm.payload["retry_count"], 1)
            self.assertEqual(llm.payload["remediation"]["source"], "review_loop")
            self.assertEqual(llm.payload["edit_intent"], [])
            self.assertEqual(llm.payload["remediation"]["failed_validation_labels"], ["script:test"])
            self.assertEqual(
                llm.payload["remediation"]["focus_areas"],
                ["app/dashboard/page.tsx", "app/dashboard/loading.tsx", "app/dashboard/error.tsx"],
            )
            self.assertEqual(llm.payload["remediation"]["task_remediation"][0]["task_id"], "T2")
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

    def test_retry_create_file_can_overwrite_existing_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            target = workspace / "components/dashboard/summary-cards.tsx"
            target.parent.mkdir(parents=True)
            target.write_text("export function SummaryCards() { return null; }\n", encoding="utf-8")

            coder = CoderAgent(AgentConfig(workspace_dir=temp_dir), CapturingCoderLLM())
            result = coder._apply_operation(
                FileEditor(temp_dir),
                {"workspace_dir": temp_dir},
                {
                    "type": "create_file",
                    "file_path": "components/dashboard/summary-cards.tsx",
                    "content": "export function SummaryCards() { return <div>Updated</div>; }\n",
                },
            )

            self.assertIsNotNone(result)
            self.assertEqual(result["operation"], "write_file")
            self.assertIn("Updated", target.read_text(encoding="utf-8"))

    def test_llm_package_json_write_is_normalized_before_apply(self) -> None:
        class PackageJsonLLM:
            def generate_json(self, system_prompt: str, user_prompt: str, schema: dict | None = None) -> dict:
                return {
                    "operations": [
                        {
                            "type": "write_file",
                            "file_path": "package.json",
                            "content": '{\n  "name": "next-test-agent",\n  "scripts": {"visual-review": "next build"},\n  "dependencies": {\n    "next": "latest",\n    "react": "latest",\n    "react-dom": "latest",\n    "reactflow": "^11.11.4",\n    "reactflow": "^11.11.4"\n  },\n  "devDependencies": {\n    "typescript": "latest"\n  }\n}',
                        }
                    ]
                }

        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "package.json").write_text(
                json.dumps(
                    {
                        "name": "smartfarm-dashboard",
                        "scripts": {"visual-review": "node scripts/visual-review.mjs"},
                        "dependencies": {"next": "16.1.6", "react": "18.3.1", "react-dom": "18.3.1"},
                        "devDependencies": {"typescript": "5.6.3"},
                    },
                    indent=2,
                ) + "\n",
                encoding="utf-8",
            )

            coder = CoderAgent(AgentConfig(workspace_dir=temp_dir), PackageJsonLLM())
            result = coder.run(
                {
                    "issue_description": "add react flow workspace",
                    "workspace_dir": temp_dir,
                    "files_to_edit": ["package.json"],
                    "plan": "Update package metadata.",
                    "planning_context": {},
                }
            )

            self.assertEqual(len(result["patches"]), 1)
            package_data = json.loads((workspace / "package.json").read_text(encoding="utf-8"))
            self.assertEqual(package_data["dependencies"]["reactflow"], "^11.11.4")
            self.assertEqual(package_data["dependencies"]["next"], "16.1.6")
            self.assertEqual(package_data["name"], "smartfarm-dashboard")
            self.assertEqual(package_data["scripts"]["visual-review"], "node scripts/visual-review.mjs")

    def test_retry_context_expands_nextjs_route_bundle_focus_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "app/dashboard").mkdir(parents=True)
            for relative_path in ["app/dashboard/page.tsx", "app/dashboard/loading.tsx", "app/dashboard/error.tsx"]:
                (workspace / relative_path).write_text("export default function Page() { return null; }\n", encoding="utf-8")

            coder = CoderAgent(AgentConfig(workspace_dir=temp_dir), CapturingCoderLLM())
            context = coder._remediation_context(
                {
                    "workspace_dir": temp_dir,
                    "workspace_profile": {
                        "nextjs": {
                            "router_type": "app",
                            "app_dir": "app",
                            "pages_dir": None,
                            "component_directories": ["components"],
                        }
                    },
                    "retry_count": 1,
                    "review_summary": {
                        "status": "changes_required",
                        "remediation": {
                            "required": True,
                            "failed_validation_labels": ["script:test"],
                            "blocked_file_paths": [],
                            "failed_operations": [],
                            "focus_areas": ["app/dashboard/page.tsx"],
                            "guidance": ["Repair the dashboard route bundle."],
                        },
                    },
                }
            )

            self.assertEqual(context["focus_areas"], ["app/dashboard/page.tsx", "app/dashboard/loading.tsx", "app/dashboard/error.tsx"])

    def test_retry_scaffold_does_not_touch_package_json_without_explicit_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "app/github").mkdir(parents=True)
            (workspace / "app/github/page.tsx").write_text("export default function Page() { return null; }\n", encoding="utf-8")
            (workspace / "package.json").write_text(
                json.dumps(
                    {
                        "name": "smartfarm-dashboard",
                        "version": "0.1.0",
                        "dependencies": {"next": "16.1.6", "react": "18.3.1", "react-dom": "18.3.1"},
                    },
                    indent=2,
                ) + "\n",
                encoding="utf-8",
            )

            coder = CoderAgent(AgentConfig(workspace_dir=temp_dir), CapturingCoderLLM())
            operations = coder._build_nextjs_operations(
                {
                    "issue_description": "Add graph views and React Flow workspace",
                    "workspace_dir": temp_dir,
                    "files_to_edit": ["app/github/page.tsx", "components/graph/GraphWorkspace.tsx"],
                    "planning_context": {
                        "scope": {"in_scope": ["app/github/page.tsx", "components/graph/GraphWorkspace.tsx"], "out_of_scope": []},
                        "tasks": [{"id": "T2", "target_files": ["app/github/page.tsx", "components/graph/GraphWorkspace.tsx"]}],
                    },
                },
                {
                    "nextjs": {"router_type": "app", "app_dir": "app", "pages_dir": None, "component_directories": ["components"]}
                },
                FileEditor(temp_dir),
            )

            self.assertNotIn("package.json", {operation["file_path"] for operation in operations})


if __name__ == "__main__":
    unittest.main()