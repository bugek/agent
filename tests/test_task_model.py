"""Tests for Phase 1 task model: scope, tasks, failed_task_ids, task-aware retry."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_code_agent.agents.planner import PlannerAgent
from ai_code_agent.agents.coder import CoderAgent
from ai_code_agent.agents.reviewer import ReviewerAgent
from ai_code_agent.config import AgentConfig
from ai_code_agent.llm.prompts import ANALYSIS_SYSTEM_PROMPT, PLAN_SYSTEM_PROMPT, SCOPE_SYSTEM_PROMPT
from ai_code_agent.orchestrator import (
    _validate_state_invariants,
    code_node,
    _update_task_statuses_approved,
    _update_task_statuses_failed,
)
from ai_code_agent.metrics import _task_status_count


# -- Fake LLM helpers ----------------------------------------------------------

class PlannerLLM:
    """Returns planner response with scope and tasks."""

    def __init__(self) -> None:
        self.system_prompts: list[str] = []

    def generate_json(self, system_prompt: str, user_prompt: str, schema=None) -> dict:
        self.system_prompts.append(system_prompt)
        return {
            "plan": ["Step 1: Create graph types.", "Step 2: Build graph route."],
            "files_to_edit": ["types/graph.ts", "app/graph/page.tsx"],
            "edit_intent": [
                {"file_path": "types/graph.ts", "intent": "Define graph interfaces"},
                {"file_path": "app/graph/page.tsx", "intent": "Create graph page"},
            ],
            "scope": {
                "in_scope": ["types/", "app/graph/", "components/graph/"],
                "out_of_scope": ["app/layout.tsx", "app/page.tsx"],
            },
            "tasks": [
                {
                    "id": "T1",
                    "title": "Create graph data model",
                    "goal": "Define TypeScript interfaces for graph nodes and edges",
                    "target_files": ["types/graph.ts"],
                    "acceptance_checks": ["typecheck"],
                },
                {
                    "id": "T2",
                    "title": "Build graph route page",
                    "goal": "Create the /graph page with React Flow integration",
                    "target_files": ["app/graph/page.tsx"],
                    "acceptance_checks": ["typecheck", "build"],
                },
            ],
        }


class PlannerRetryLLM:
    """Returns planner response that only re-plans failed tasks."""

    def __init__(self):
        self.payload = None

    def generate_json(self, system_prompt: str, user_prompt: str, schema=None) -> dict:
        self.payload = json.loads(user_prompt)
        return {
            "plan": ["Step 1: Fix graph route page build error."],
            "files_to_edit": ["app/graph/page.tsx"],
            "edit_intent": [
                {"file_path": "app/graph/page.tsx", "intent": "Fix build error"},
            ],
            "scope": {
                "in_scope": ["app/graph/"],
                "out_of_scope": ["app/layout.tsx", "app/page.tsx", "types/graph.ts"],
            },
            "tasks": [
                {
                    "id": "T1",
                    "title": "Create graph data model",
                    "goal": "Already completed",
                    "target_files": ["types/graph.ts"],
                    "acceptance_checks": ["typecheck"],
                },
                {
                    "id": "T2",
                    "title": "Fix graph route page",
                    "goal": "Fix the build error in the graph page",
                    "target_files": ["app/graph/page.tsx"],
                    "acceptance_checks": ["typecheck", "build"],
                },
            ],
        }


class CoderLLM:
    """Returns coder operations."""

    def generate_json(self, system_prompt: str, user_prompt: str, schema=None) -> dict:
        payload = json.loads(user_prompt)
        self.last_payload = payload
        return {
            "operations": [
                {
                    "type": "create_file",
                    "file_path": "app/graph/page.tsx",
                    "content": "export default function GraphPage() { return <div>Graph</div>; }",
                },
            ],
        }


class ReviewerLLM:
    """Returns reviewer response with failed_task_ids."""

    def __init__(self, *, approved: bool = False, failed_task_ids: list[str] | None = None, task_remediation: list[dict] | None = None):
        self._approved = approved
        self._failed_task_ids = failed_task_ids or []
        self._task_remediation = task_remediation or []

    def generate_json(self, system_prompt: str, user_prompt: str, schema=None) -> dict:
        return {
            "review_approved": self._approved,
            "review_comments": ["Build failed on graph page."] if not self._approved else ["Looks good."],
            "failed_task_ids": self._failed_task_ids,
            "task_remediation": self._task_remediation,
        }


# -- Planner Tests -------------------------------------------------------------

class TestPlannerTaskModel(unittest.TestCase):
    def test_planner_emits_scope_and_tasks(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "types").mkdir()
            (workspace / "types/graph.ts").write_text("// placeholder\n")
            (workspace / "app/graph").mkdir(parents=True)
            (workspace / "app/graph/page.tsx").write_text("// placeholder\n")

            planner = PlannerAgent(AgentConfig(workspace_dir=temp_dir), PlannerLLM())
            result = planner.run({
                "issue_description": "Add graph views with React Flow",
                "workspace_dir": temp_dir,
            })

            ctx = result["planning_context"]
            self.assertIn("scope", ctx)
            self.assertIn("tasks", ctx)
            self.assertEqual(ctx["scope"]["out_of_scope"], ["app/layout.tsx", "app/page.tsx"])
            self.assertEqual(len(ctx["tasks"]), 2)
            self.assertEqual(ctx["tasks"][0]["id"], "T1")
            self.assertEqual(ctx["tasks"][0]["status"], "pending")
            self.assertEqual(ctx["tasks"][1]["id"], "T2")

    def test_planner_uses_separate_scope_and_plan_prompts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "types").mkdir()
            (workspace / "types/graph.ts").write_text("// placeholder\n")
            llm = PlannerLLM()

            planner = PlannerAgent(AgentConfig(workspace_dir=temp_dir), llm)
            planner.run({
                "issue_description": "Add graph views with React Flow",
                "workspace_dir": temp_dir,
            })

            self.assertGreaterEqual(len(llm.system_prompts), 2)
            self.assertEqual(llm.system_prompts[0], SCOPE_SYSTEM_PROMPT)
            self.assertEqual(llm.system_prompts[1], ANALYSIS_SYSTEM_PROMPT)
            self.assertEqual(llm.system_prompts[2], PLAN_SYSTEM_PROMPT)

    def test_planner_marks_completed_tasks_on_retry(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "app/graph").mkdir(parents=True)
            (workspace / "app/graph/page.tsx").write_text("// placeholder\n")

            llm = PlannerRetryLLM()
            planner = PlannerAgent(AgentConfig(workspace_dir=temp_dir), llm)
            result = planner.run({
                "issue_description": "Add graph views with React Flow",
                "workspace_dir": temp_dir,
                "retry_count": 1,
                "failed_task_ids": ["T2"],
                "task_statuses": {"T1": "completed", "T2": "failed"},
                "review_summary": {
                    "status": "changes_required",
                    "remediation": {
                        "required": True,
                        "failed_validation_labels": ["build"],
                        "focus_areas": ["app/graph/page.tsx"],
                        "guidance": ["Fix build error in graph page"],
                        "failed_operations": [],
                    },
                },
            })

            # Verify failed_task_ids and task_statuses were passed in prompt
            self.assertIn("failed_task_ids", llm.payload)
            self.assertEqual(llm.payload["failed_task_ids"], ["T2"])
            self.assertEqual(llm.payload["task_statuses"]["T1"], "completed")

            # T1 should be marked completed (not in failed_task_ids)
            tasks = result["planning_context"]["tasks"]
            t1 = next(t for t in tasks if t["id"] == "T1")
            self.assertEqual(t1["status"], "completed")

    def test_planner_fallback_tasks_when_llm_returns_none(self):
        class NoTasksLLM:
            def generate_json(self, *a, **kw):
                return {"plan": "Do stuff", "files_to_edit": ["src/app.py"]}

        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "src").mkdir()
            (workspace / "src/app.py").write_text("# app\n")

            planner = PlannerAgent(AgentConfig(workspace_dir=temp_dir), NoTasksLLM())
            result = planner.run({
                "issue_description": "Add logging",
                "workspace_dir": temp_dir,
            })

            tasks = result["planning_context"]["tasks"]
            self.assertEqual(len(tasks), 1)
            self.assertEqual(tasks[0]["id"], "T1")
            self.assertEqual(tasks[0]["status"], "pending")

    def test_planner_normalize_scope_defaults(self):
        """When LLM returns no scope, in_scope falls back to files_to_edit."""
        class NoScopeLLM:
            def generate_json(self, *a, **kw):
                return {"plan": "Do stuff", "files_to_edit": ["src/app.py"]}

        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "src").mkdir()
            (workspace / "src/app.py").write_text("# app\n")

            planner = PlannerAgent(AgentConfig(workspace_dir=temp_dir), NoScopeLLM())
            result = planner.run({
                "issue_description": "Add logging",
                "workspace_dir": temp_dir,
            })

            scope = result["planning_context"]["scope"]
            self.assertIn("src/app.py", scope["in_scope"])
            self.assertEqual(scope["out_of_scope"], [])

    def test_planner_normalize_scope_removes_cross_list_duplicates(self):
        class OverlappingScopeLLM:
            def generate_json(self, *a, **kw):
                return {
                    "plan": "Build graph route",
                    "files_to_edit": ["components/graph/index.tsx"],
                    "scope": {
                        "in_scope": ["components/graph", "components/ui/graph", "components/graph/"],
                        "out_of_scope": ["components/graph/", "components/ui/graph", "app/page.tsx"],
                    },
                }

        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "components/graph").mkdir(parents=True)
            (workspace / "components/ui").mkdir(parents=True)
            (workspace / "components/graph/index.tsx").write_text("export const Graph = null\n")

            planner = PlannerAgent(AgentConfig(workspace_dir=temp_dir), OverlappingScopeLLM())
            result = planner.run(
                {
                    "issue_description": "Add graph views with React Flow",
                    "workspace_dir": temp_dir,
                }
            )

            scope = result["planning_context"]["scope"]
            self.assertEqual(scope["in_scope"], ["components/graph", "components/ui/graph", "components/graph/index.tsx"])
            self.assertEqual(scope["out_of_scope"], ["app/page.tsx"])
            state = {"planning_context": {"scope": scope}}
            self.assertEqual(_validate_state_invariants(state, "scope"), [])

    def test_planner_expands_nextjs_scaffold_companion_files(self):
        class NextRouteLLM:
            def generate_json(self, system_prompt, user_prompt, schema=None):
                if system_prompt == SCOPE_SYSTEM_PROMPT:
                    return {
                        "scope": {
                            "in_scope": ["app/github/page.tsx"],
                            "out_of_scope": [],
                        }
                    }
                if system_prompt == ANALYSIS_SYSTEM_PROMPT:
                    return {
                        "candidate_files": ["app/github/page.tsx"],
                        "retrieval_strategy": "hybrid",
                    }
                return {
                    "plan": ["Create a GitHub graph experience."],
                    "files_to_edit": ["app/github/page.tsx"],
                    "scope": {
                        "in_scope": ["app/github/page.tsx"],
                        "out_of_scope": [],
                    },
                }

        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "app/github").mkdir(parents=True)
            (workspace / "components").mkdir()
            (workspace / "app/github/page.tsx").write_text("export default function Page() { return null; }\n")
            (workspace / "package.json").write_text(
                json.dumps(
                    {
                        "name": "demo",
                        "version": "0.1.0",
                        "dependencies": {
                            "next": "14.2.16",
                            "react": "18.3.1",
                            "react-dom": "18.3.1"
                        }
                    }
                ) + "\n"
            )

            planner = PlannerAgent(AgentConfig(workspace_dir=temp_dir), NextRouteLLM())
            result = planner.run(
                {
                    "issue_description": "Create a github page layout and section with an interactive graph workspace",
                    "workspace_dir": temp_dir,
                }
            )

            self.assertIn("app/github/layout.tsx", result["files_to_edit"])
            component_targets = [file_path for file_path in result["files_to_edit"] if file_path.startswith("components/")]
            self.assertTrue(component_targets)
            self.assertIn("app/github/layout.tsx", result["planning_context"]["scope"]["in_scope"])
            self.assertTrue(
                any(file_path.startswith("components/") for file_path in result["planning_context"]["scope"]["in_scope"])
            )

    def test_planner_scope_keeps_all_files_to_edit_without_truncation(self):
        class ManyFilesLLM:
            def generate_json(self, *a, **kw):
                return {
                    "plan": "Touch many files",
                    "files_to_edit": [f"src/file-{index}.ts" for index in range(11)],
                    "scope": {
                        "in_scope": ["src/file-0.ts"],
                        "out_of_scope": [],
                    },
                }

        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "src").mkdir()
            for index in range(11):
                (workspace / f"src/file-{index}.ts").write_text("export const value = 1;\n")

            planner = PlannerAgent(AgentConfig(workspace_dir=temp_dir), ManyFilesLLM())
            result = planner.run(
                {
                    "issue_description": "Update several TypeScript files",
                    "workspace_dir": temp_dir,
                }
            )

            scope_in = result["planning_context"]["scope"]["in_scope"]
            self.assertEqual(len(scope_in), 11)
            self.assertIn("src/file-10.ts", scope_in)

    def test_scope_agent_seeds_existing_nextjs_route_hints(self):
        class EmptyScopeLLM:
            def generate_json(self, system_prompt, user_prompt, schema=None):
                if system_prompt == SCOPE_SYSTEM_PROMPT:
                    return {"goal": "Build the GitHub graph page.", "scope": {}}
                if system_prompt == ANALYSIS_SYSTEM_PROMPT:
                    return {"candidate_files": ["app/github/page.tsx"]}
                return {"plan": ["Update the GitHub route."], "files_to_edit": ["app/github/page.tsx"]}

        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "app/github").mkdir(parents=True)
            (workspace / "components").mkdir()
            (workspace / "app/github/page.tsx").write_text("export default function Page() { return null; }\n")
            (workspace / "app/github/loading.tsx").write_text("export default function Loading() { return null; }\n")
            (workspace / "app/github/error.tsx").write_text("export default function Error() { return null; }\n")
            (workspace / "package.json").write_text(
                json.dumps(
                    {
                        "name": "demo",
                        "version": "0.1.0",
                        "dependencies": {"next": "14.2.16", "react": "18.3.1", "react-dom": "18.3.1"},
                    }
                ) + "\n"
            )

            planner = PlannerAgent(AgentConfig(workspace_dir=temp_dir), EmptyScopeLLM())
            result = planner.run(
                {
                    "issue_description": "Title: Create github page layout and section with an interactive graph workspace\n\nDescription:\nAdd React Flow content inside the github page.",
                    "workspace_dir": temp_dir,
                }
            )

            scope = result["scope_context"] if "scope_context" in result else result["planning_context"]["scope"]
            in_scope = scope.get("in_scope", [])
            self.assertIn("app/github/page.tsx", in_scope)
            self.assertIn("app/github/loading.tsx", in_scope)
            self.assertIn("app/github/error.tsx", in_scope)


# -- Coder Tests ---------------------------------------------------------------

class TestCoderTaskModel(unittest.TestCase):
    def test_coder_filters_out_of_scope_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "app").mkdir()
            (workspace / "app/layout.tsx").write_text("export default function Layout() {}\n")
            (workspace / "app/graph").mkdir()
            (workspace / "app/graph/page.tsx").write_text("// placeholder\n")

            coder = CoderAgent(AgentConfig(workspace_dir=temp_dir), CoderLLM())
            state = {
                "issue_description": "Add graph views",
                "workspace_dir": temp_dir,
                "plan": "Create graph page",
                "files_to_edit": ["app/graph/page.tsx", "app/layout.tsx"],
                "planning_context": {
                    "scope": {
                        "in_scope": ["app/graph/"],
                        "out_of_scope": ["app/layout.tsx"],
                    },
                    "tasks": [
                        {
                            "id": "T1",
                            "title": "Build graph page",
                            "target_files": ["app/graph/page.tsx"],
                            "acceptance_checks": ["typecheck"],
                            "status": "pending",
                        },
                    ],
                    "edit_intent": [],
                },
            }

            # Verify _is_out_of_scope works
            self.assertTrue(coder._is_out_of_scope("app/layout.tsx", state))
            self.assertFalse(coder._is_out_of_scope("app/graph/page.tsx", state))

    def test_coder_active_tasks_filters_completed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            coder = CoderAgent(AgentConfig(workspace_dir=temp_dir), CoderLLM())
            state = {
                "workspace_dir": temp_dir,
                "issue_description": "test",
                "planning_context": {
                    "tasks": [
                        {"id": "T1", "title": "Done task", "status": "completed", "target_files": ["a.ts"]},
                        {"id": "T2", "title": "Pending task", "status": "pending", "target_files": ["b.ts"]},
                    ],
                },
                "task_statuses": {"T1": "completed", "T2": "pending"},
            }

            active = coder._active_tasks(state)
            self.assertEqual(len(active), 1)
            self.assertEqual(active[0]["id"], "T2")

    def test_coder_prefers_scoped_route_files_over_issue_fallback(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "app/github").mkdir(parents=True)
            (workspace / "app/graph").mkdir(parents=True)
            (workspace / "app/github/page.tsx").write_text("export default function Page() { return null; }\n")
            (workspace / "app/graph/page.tsx").write_text("export default function Page() { return null; }\n")

            coder = CoderAgent(AgentConfig(workspace_dir=temp_dir), CoderLLM())
            route_slug = coder._preferred_next_route_slug(
                {
                    "issue_description": "Create a graph workspace page",
                    "workspace_dir": temp_dir,
                    "files_to_edit": ["app/github/page.tsx"],
                    "planning_context": {
                        "scope": {"in_scope": ["app/github/page.tsx"], "out_of_scope": []},
                        "tasks": [{"id": "T1", "target_files": ["app/github/page.tsx"]}],
                    },
                },
                {"router_type": "app", "app_dir": "app", "pages_dir": None},
            )

            self.assertEqual(route_slug, "github")

    def test_coder_prefers_scoped_route_directory_over_issue_fallback(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "app/github").mkdir(parents=True)
            (workspace / "app/graph").mkdir(parents=True)

            coder = CoderAgent(AgentConfig(workspace_dir=temp_dir), CoderLLM())
            route_slug = coder._preferred_next_route_slug(
                {
                    "issue_description": "Create a graph workspace page",
                    "workspace_dir": temp_dir,
                    "files_to_edit": ["package.json"],
                    "planning_context": {
                        "scope": {"in_scope": ["app/github/", "components/graph"], "out_of_scope": []},
                        "tasks": [{"id": "T1", "target_files": ["app/github/", "components/graph"]}],
                    },
                },
                {"router_type": "app", "app_dir": "app", "pages_dir": None},
            )

            self.assertEqual(route_slug, "github")

    def test_coder_prefers_nested_route_over_root_page(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            coder = CoderAgent(AgentConfig(workspace_dir=temp_dir), CoderLLM())
            route_slug = coder._route_slug_from_files(
                ["app/page.tsx", "app/github/page.tsx", "components/graph/GraphWorkspace.tsx"],
                {"router_type": "app", "app_dir": "app", "pages_dir": None},
            )

            self.assertEqual(route_slug, "github")

    def test_planner_scaffold_expansion_keeps_existing_route_anchor(self):
        class NextRouteLLM:
            def generate_json(self, system_prompt, user_prompt, schema=None):
                if system_prompt == SCOPE_SYSTEM_PROMPT:
                    return {
                        "scope": {
                            "in_scope": ["app/github/page.tsx"],
                            "out_of_scope": ["app/graph/", "app/next-test-agent/"],
                        }
                    }
                if system_prompt == ANALYSIS_SYSTEM_PROMPT:
                    return {
                        "candidate_files": ["app/github/page.tsx", "app/page.tsx"],
                        "retrieval_strategy": "hybrid",
                    }
                return {
                    "plan": ["Refresh the GitHub graph route."],
                    "files_to_edit": ["app/github/page.tsx", "app/page.tsx"],
                    "scope": {
                        "in_scope": ["app/github/page.tsx", "app/page.tsx"],
                        "out_of_scope": ["app/graph/", "app/next-test-agent/"],
                    },
                }

        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "app/github").mkdir(parents=True)
            (workspace / "app").mkdir(exist_ok=True)
            (workspace / "components").mkdir(exist_ok=True)
            (workspace / "app/github/page.tsx").write_text("export default function Page() { return null; }\n")
            (workspace / "app/page.tsx").write_text("export default function Home() { return null; }\n")
            (workspace / "package.json").write_text(
                json.dumps({
                    "name": "demo",
                    "version": "0.1.0",
                    "dependencies": {"next": "14.2.16", "react": "18.3.1", "react-dom": "18.3.1"},
                }) + "\n"
            )

            planner = PlannerAgent(AgentConfig(workspace_dir=temp_dir), NextRouteLLM())
            result = planner.run(
                {
                    "issue_description": "Issue provider: github\nTitle: Add graph views and React Flow workspace",
                    "workspace_dir": temp_dir,
                    "analysis_context": {
                        "workspace_profile": {"frameworks": ["nextjs"], "nextjs": {"router_type": "app", "app_dir": "app", "pages_dir": None}},
                        "candidate_files": ["app/github/page.tsx", "app/page.tsx"],
                        "retrieval_strategy": "hybrid",
                    },
                    "scope_context": {"scope": {"in_scope": ["app/github/page.tsx"], "out_of_scope": ["app/graph/", "app/next-test-agent/"]}},
                }
            )

            self.assertNotIn("app/graph/page.tsx", result["files_to_edit"])
            self.assertFalse(any(file_path.startswith("app/next-test-agent/") for file_path in result["files_to_edit"]))

    def test_planner_route_lock_ignores_repo_slug_anchor(self):
        class NextRouteLLM:
            def generate_json(self, system_prompt, user_prompt, schema=None):
                if system_prompt == SCOPE_SYSTEM_PROMPT:
                    return {
                        "scope": {
                            "in_scope": ["app/next-test-agent/page.tsx", "app/github/page.tsx"],
                            "out_of_scope": ["app/graph/"],
                        }
                    }
                if system_prompt == ANALYSIS_SYSTEM_PROMPT:
                    return {
                        "candidate_files": ["app/next-test-agent/page.tsx", "app/github/page.tsx"],
                        "retrieval_strategy": "hybrid",
                    }
                return {
                    "plan": ["Create a graph workspace route."],
                    "files_to_edit": ["app/graph/page.tsx"],
                    "scope": {
                        "in_scope": ["app/graph/page.tsx"],
                        "out_of_scope": ["app/next-test-agent/"],
                    },
                }

        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "app/next-test-agent").mkdir(parents=True)
            (workspace / "app/github").mkdir(parents=True)
            (workspace / "app/next-test-agent/page.tsx").write_text("export default function Page() { return null; }\n")
            (workspace / "app/github/page.tsx").write_text("export default function Page() { return null; }\n")
            (workspace / "package.json").write_text(
                json.dumps({
                    "name": "demo",
                    "version": "0.1.0",
                    "dependencies": {"next": "14.2.16", "react": "18.3.1", "react-dom": "18.3.1"},
                }) + "\n"
            )

            planner = PlannerAgent(AgentConfig(workspace_dir=temp_dir), NextRouteLLM())
            result = planner.run(
                {
                    "issue_description": "Issue provider: github\nGitHub issue: bugek/next-test-agent#12\nTitle: Add graph views and React Flow workspace",
                    "workspace_dir": temp_dir,
                    "analysis_context": {
                        "workspace_profile": {"frameworks": ["nextjs"], "nextjs": {"router_type": "app", "app_dir": "app", "pages_dir": None}},
                        "candidate_files": ["app/next-test-agent/page.tsx", "app/github/page.tsx"],
                        "candidate_scores": [
                            {"file_path": "app/github/page.tsx", "score": 50},
                            {"file_path": "app/next-test-agent/page.tsx", "score": 30},
                        ],
                        "retrieval_strategy": "hybrid",
                    },
                    "scope_context": {"scope": {"in_scope": ["app/next-test-agent/page.tsx", "app/github/page.tsx"], "out_of_scope": ["app/graph/"]}},
                }
            )

            self.assertIn("app/github/page.tsx", result["files_to_edit"])
            self.assertNotIn("app/next-test-agent/page.tsx", result["files_to_edit"])
            self.assertNotIn("app/graph/page.tsx", result["files_to_edit"])

    def test_planner_canonicalizes_graph_alias_targets(self):
        class GraphAliasLLM:
            def generate_json(self, system_prompt, user_prompt, schema=None):
                if system_prompt == SCOPE_SYSTEM_PROMPT:
                    return {"scope": {"in_scope": ["app/github/page.tsx"], "out_of_scope": []}}
                if system_prompt == ANALYSIS_SYSTEM_PROMPT:
                    return {"candidate_files": ["app/github/page.tsx"], "retrieval_strategy": "hybrid"}
                return {
                    "plan": ["Add graph workspace assets."],
                    "files_to_edit": ["lib/graph/data.ts", "components/graph-workspace.tsx", "components/graph/react-flow-workspace.tsx", "app/github/page.tsx"],
                    "tasks": [
                        {"id": "T1", "title": "Graph data", "target_files": ["lib/graph/data.ts", "lib/graph/types.ts"]},
                        {"id": "T2", "title": "Graph ui", "target_files": ["components/graph-workspace.tsx", "components/graph-detail-panel.tsx"]},
                    ],
                    "scope": {"in_scope": ["lib/graph/data.ts", "components/graph-workspace.tsx", "app/github/page.tsx"], "out_of_scope": []},
                }

        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "app/github").mkdir(parents=True)
            (workspace / "app/github/page.tsx").write_text("export default function Page() { return null; }\n")
            (workspace / "package.json").write_text(
                json.dumps({"name": "demo", "version": "0.1.0", "dependencies": {"next": "14.2.16", "react": "18.3.1", "react-dom": "18.3.1"}}) + "\n"
            )

            planner = PlannerAgent(AgentConfig(workspace_dir=temp_dir), GraphAliasLLM())
            result = planner.run({"issue_description": "Add graph views and React Flow workspace", "workspace_dir": temp_dir})

            self.assertIn("components/graph/graph-data.ts", result["files_to_edit"])
            self.assertIn("components/graph/GraphWorkspace.tsx", result["files_to_edit"])
            self.assertIn("components/react-flow/github-react-flow-workspace.tsx", result["files_to_edit"])
            task_targets = [target for task in result["planning_context"]["tasks"] for target in task.get("target_files", [])]
            self.assertIn("components/graph/types.ts", task_targets)
            self.assertNotIn("lib/graph/data.ts", task_targets)

    def test_coder_reactivates_failed_tasks(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            coder = CoderAgent(AgentConfig(workspace_dir=temp_dir), CoderLLM())
            state = {
                "workspace_dir": temp_dir,
                "issue_description": "test",
                "planning_context": {
                    "tasks": [
                        {"id": "T1", "title": "Done task", "status": "completed", "target_files": ["a.ts"]},
                        {"id": "T2", "title": "Failed task", "status": "failed", "target_files": ["b.ts"]},
                    ],
                },
                "task_statuses": {"T1": "completed", "T2": "failed"},
                "failed_task_ids": ["T2"],
            }

            active = coder._active_tasks(state)
            self.assertEqual(len(active), 1)
            self.assertEqual(active[0]["id"], "T2")

    def test_coder_blocks_out_of_scope_operations(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "app").mkdir()
            (workspace / "app/layout.tsx").write_text("export default function Layout() {}\n")
            (workspace / "app/graph").mkdir()

            class OutOfScopeCoderLLM:
                def generate_json(self, *a, **kw):
                    return {
                        "operations": [
                            {"type": "create_file", "file_path": "app/graph/page.tsx", "content": "// ok"},
                            {"type": "write_file", "file_path": "app/layout.tsx", "content": "// bad"},
                        ]
                    }

            coder = CoderAgent(AgentConfig(workspace_dir=temp_dir), OutOfScopeCoderLLM())
            result = coder.run({
                "issue_description": "Add graph views",
                "workspace_dir": temp_dir,
                "plan": "Create graph page",
                "files_to_edit": ["app/graph/page.tsx"],
                "planning_context": {
                    "scope": {
                        "in_scope": ["app/graph/"],
                        "out_of_scope": ["app/layout.tsx"],
                    },
                    "tasks": [{"id": "T1", "title": "Graph", "target_files": ["app/graph/page.tsx"], "status": "pending"}],
                    "edit_intent": [],
                },
            })

            # app/layout.tsx should be blocked by scope
            blocked = result.get("codegen_summary", {}).get("blocked_operations", [])
            blocked_files = [op["file_path"] for op in blocked]
            self.assertIn("app/layout.tsx", blocked_files)

            # app/graph/page.tsx should be applied
            patched_files = [p["file"] for p in result.get("patches", [])]
            self.assertIn("app/graph/page.tsx", patched_files)

    def test_coder_sends_scope_and_tasks_in_prompt(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "app/graph").mkdir(parents=True)
            (workspace / "app/graph/page.tsx").write_text("// placeholder\n")

            llm = CoderLLM()
            coder = CoderAgent(AgentConfig(workspace_dir=temp_dir), llm)
            coder.run({
                "issue_description": "Add graph views",
                "workspace_dir": temp_dir,
                "plan": "Create graph page",
                "files_to_edit": ["app/graph/page.tsx"],
                "planning_context": {
                    "scope": {
                        "in_scope": ["app/graph/"],
                        "out_of_scope": ["app/layout.tsx"],
                    },
                    "tasks": [
                        {"id": "T1", "title": "Graph page", "target_files": ["app/graph/page.tsx"], "status": "pending"},
                    ],
                    "edit_intent": [],
                },
            })

            self.assertIn("scope", llm.last_payload)
            self.assertIn("tasks", llm.last_payload)
            self.assertEqual(llm.last_payload["scope"]["out_of_scope"], ["app/layout.tsx"])
            self.assertEqual(len(llm.last_payload["tasks"]), 1)


# -- Reviewer Tests ------------------------------------------------------------

class TestReviewerTaskModel(unittest.TestCase):
    def test_reviewer_emits_failed_task_ids(self):
        llm = ReviewerLLM(
            approved=False,
            failed_task_ids=["T2"],
            task_remediation=[
                {
                    "task_id": "T2",
                    "blocker_types": ["build_breakage"],
                    "focus_areas": ["app/graph/page.tsx"],
                    "guidance": ["Repair the failing build in the graph page."],
                }
            ],
        )
        reviewer = ReviewerAgent(AgentConfig(), llm)
        result = reviewer.run({
            "issue_description": "Add graph views",
            "workspace_dir": "/tmp/test",
            "patches": [{"file": "app/graph/page.tsx", "diff": "+page", "operation": "create_file"}],
            "test_results": "build(exit=1): Error in app/graph/page.tsx\ntypecheck(exit=0): OK",
            "test_passed": False,
            "planning_context": {
                "tasks": [
                    {"id": "T1", "title": "Graph types", "target_files": ["types/graph.ts"], "acceptance_checks": ["typecheck"]},
                    {"id": "T2", "title": "Graph page", "target_files": ["app/graph/page.tsx"], "acceptance_checks": ["typecheck", "build"]},
                ],
            },
        })

        self.assertIn("failed_task_ids", result)
        self.assertIn("T2", result["failed_task_ids"])
        # T1 had typecheck which passed, so should not be failed
        self.assertNotIn("T1", result["failed_task_ids"])
        # review_summary should also have failed_task_ids
        self.assertIn("failed_task_ids", result["review_summary"])
        self.assertEqual(result["task_remediation"][0]["task_id"], "T2")
        self.assertIn("build_breakage", result["task_remediation"][0]["blocker_types"])
        self.assertNotIn("operation_failure", result["task_remediation"][0]["blocker_types"])

    def test_reviewer_no_failed_tasks_when_approved(self):
        llm = ReviewerLLM(approved=True)
        reviewer = ReviewerAgent(AgentConfig(), llm)
        result = reviewer.run({
            "issue_description": "Add graph views",
            "workspace_dir": "/tmp/test",
            "patches": [{"file": "app/graph/page.tsx", "diff": "+page", "operation": "create_file"}],
            "test_results": "build(exit=0): OK\ntypecheck(exit=0): OK",
            "test_passed": True,
            "planning_context": {
                "tasks": [
                    {"id": "T1", "title": "Graph types", "target_files": ["types/graph.ts"], "acceptance_checks": ["typecheck"]},
                    {"id": "T2", "title": "Graph page", "target_files": ["app/graph/page.tsx"], "acceptance_checks": ["build"]},
                ],
            },
        })

        self.assertEqual(result["failed_task_ids"], [])

    def test_reviewer_deterministic_failed_tasks_from_signals(self):
        """Even if LLM doesn't return failed_task_ids, deterministic logic catches them."""
        class NoTaskIdLLM:
            def generate_json(self, *a, **kw):
                return {"review_approved": False, "review_comments": ["Build failed."]}

        reviewer = ReviewerAgent(AgentConfig(), NoTaskIdLLM())
        result = reviewer.run({
            "issue_description": "Add graph views",
            "workspace_dir": "/tmp/test",
            "patches": [
                {"file": "types/graph.ts", "diff": "+types", "operation": "create_file"},
                {"file": "app/graph/page.tsx", "diff": "+page", "operation": "create_file"},
            ],
            "test_results": "typecheck(exit=0): OK\nbuild(exit=1): Error",
            "test_passed": False,
            "planning_context": {
                "tasks": [
                    {"id": "T1", "title": "Types", "target_files": ["types/graph.ts"], "acceptance_checks": ["typecheck"]},
                    {"id": "T2", "title": "Page", "target_files": ["app/graph/page.tsx"], "acceptance_checks": ["typecheck", "build"]},
                ],
            },
        })

        # T2 has "build" as acceptance check, and build failed
        self.assertIn("T2", result["failed_task_ids"])
        # T1 only has typecheck which passed
        self.assertNotIn("T1", result["failed_task_ids"])
        self.assertEqual(result["task_remediation"][0]["task_id"], "T2")
        self.assertEqual(result["task_remediation"][0]["blocker_types"], ["build_breakage"])


# -- Orchestrator Tests --------------------------------------------------------

class TestOrchestratorTaskStatuses(unittest.TestCase):
    def test_update_task_statuses_approved(self):
        state = {
            "planning_context": {
                "tasks": [
                    {"id": "T1", "title": "A"},
                    {"id": "T2", "title": "B"},
                ],
            },
            "task_statuses": {"T1": "completed", "T2": "failed"},
        }
        result = _update_task_statuses_approved(state)
        self.assertEqual(result["T1"], "completed")
        self.assertEqual(result["T2"], "completed")

    def test_update_task_statuses_failed(self):
        state = {
            "planning_context": {
                "tasks": [
                    {"id": "T1", "title": "A"},
                    {"id": "T2", "title": "B"},
                    {"id": "T3", "title": "C"},
                ],
            },
            "task_statuses": {},
        }
        result = _update_task_statuses_failed(state, ["T2"])
        self.assertEqual(result["T2"], "failed")
        # T1 and T3 not in failed list = completed when failed_set is non-empty
        # Actually: they are "pending" since we don't auto-promote to completed
        # The logic: if task_id not in failed_set and statuses.get(task_id) != "completed"
        # then keep existing status (default "pending")
        # This is correct — only explicitly mark failed ones, leave others as-is

    def test_update_task_statuses_preserves_completed(self):
        state = {
            "planning_context": {
                "tasks": [
                    {"id": "T1", "title": "A"},
                    {"id": "T2", "title": "B"},
                ],
            },
            "task_statuses": {"T1": "completed"},
        }
        result = _update_task_statuses_failed(state, ["T2"])
        self.assertEqual(result["T1"], "completed")
        self.assertEqual(result["T2"], "failed")


# -- Metrics Tests -------------------------------------------------------------

class TestTaskMetrics(unittest.TestCase):
    def test_task_status_count(self):
        statuses = {"T1": "completed", "T2": "failed", "T3": "completed"}
        self.assertEqual(_task_status_count(statuses, "completed"), 2)
        self.assertEqual(_task_status_count(statuses, "failed"), 1)
        self.assertEqual(_task_status_count(statuses, "pending"), 0)

    def test_task_status_count_empty(self):
        self.assertEqual(_task_status_count(None, "completed"), 0)
        self.assertEqual(_task_status_count({}, "completed"), 0)


class TestStateInvariantValidation(unittest.TestCase):
    def test_plan_validator_rejects_out_of_scope_files(self):
        state = {
            "files_to_edit": ["app/layout.tsx"],
            "planning_context": {
                "scope": {
                    "in_scope": ["app/graph/"],
                    "out_of_scope": ["app/layout.tsx"],
                },
                "tasks": [{"id": "T1", "title": "Graph", "target_files": ["app/graph/page.tsx"]}],
            },
        }

        errors = _validate_state_invariants(state, "plan")
        self.assertTrue(errors)
        self.assertIn("out_of_scope", errors[0])

    def test_code_validator_rejects_patch_outside_scope_and_tasks(self):
        state = {
            "patches": [{"file": "app/page.tsx", "diff": "...", "operation": "write_file"}],
            "planning_context": {
                "scope": {
                    "in_scope": ["app/graph/"],
                    "out_of_scope": ["app/layout.tsx"],
                },
                "tasks": [{"id": "T1", "title": "Graph", "target_files": ["app/graph/page.tsx"]}],
            },
        }

        errors = _validate_state_invariants(state, "code")
        self.assertTrue(errors)
        self.assertIn("outside scope and task targets", errors[0])

    def test_review_validator_rejects_unknown_failed_task_ids(self):
        state = {
            "failed_task_ids": ["T9"],
            "planning_context": {
                "tasks": [{"id": "T1", "title": "Graph"}],
            },
        }

        errors = _validate_state_invariants(state, "review")
        self.assertTrue(errors)
        self.assertIn("unknown task", errors[0])

    def test_code_node_surfaces_invariant_failure_as_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "app").mkdir(parents=True)
            coder_result = {
                "patches": [{"file": "app/page.tsx", "diff": "+bad", "operation": "create_file"}],
                "codegen_summary": {
                    "requested_operations": 1,
                    "applied_operations": 1,
                    "failed_operations": [],
                    "blocked_operations": [],
                    "generated_by": "llm",
                },
            }

            with patch("ai_code_agent.agents.coder.CoderAgent") as mock_coder, patch(
                "ai_code_agent.llm.client.LLMClient.from_config", return_value=object()
            ):
                mock_coder.return_value.run.return_value = coder_result
                result = code_node(
                    {
                        "issue_description": "Add graph views",
                        "workspace_dir": temp_dir,
                        "run_id": "run-123",
                        "workflow_started_at": "2026-03-10T10:00:00Z",
                        "planning_context": {
                            "scope": {"in_scope": ["app/graph/"], "out_of_scope": ["app/layout.tsx"]},
                            "tasks": [{"id": "T1", "title": "Graph", "target_files": ["app/graph/page.tsx"]}],
                            "edit_intent": [],
                        },
                        "files_to_edit": ["app/graph/page.tsx"],
                        "execution_events": [],
                        "patches": [],
                    }
                )

            self.assertTrue(result["state_validation_failed"])
            self.assertIn("state invariant violation after code", result["error_message"])
            self.assertEqual(result["execution_events"][-1]["status"], "failed")

    def test_code_invariant_failure_taxonomy_in_metrics(self):
        from ai_code_agent.metrics import build_execution_metrics

        metrics = build_execution_metrics(
            {
                "issue_description": "Add graph views",
                "workspace_dir": ".",
                "state_validation_failed": True,
                "error_message": "state invariant violation after code: patch path is outside scope and task targets: app/page.tsx",
                "execution_events": [],
                "test_passed": False,
                "review_approved": False,
            }
        )

        self.assertEqual(metrics["failures"]["primary_category"], "orchestration")
        self.assertEqual(metrics["failures"]["subcategory"], "state_invariant_violation")


if __name__ == "__main__":
    unittest.main()
