from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ai_code_agent.agents.coder import CoderAgent
from ai_code_agent.config import AgentConfig
from ai_code_agent.tools.file_editor import FileEditor


class StubLLM:
    def generate_json(self, system_prompt: str, user_prompt: str, schema: dict | None = None) -> dict:
        return {}


class CoderFrontendQualityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.coder = CoderAgent(AgentConfig(workspace_dir="."), StubLLM())

    def test_next_component_template_includes_state_coverage(self) -> None:
        content = self.coder._next_component_template("analytics hero", "revamp dashboard hero section", "dashboard")

        self.assertIn('type AnalyticsHeroState = "loading" | "empty" | "error" | "ready";', content)
        self.assertIn('if (state === "loading") {', content)
        self.assertIn('if (state === "error") {', content)
        self.assertIn('if (state === "empty" || items.length === 0) {', content)
        self.assertIn('Designed to surface the strongest numbers first', content)
        self.assertIn('Signal-rich dashboard', content)

    def test_next_page_template_uses_preview_items_and_ready_state(self) -> None:
        content = self.coder._next_page_template(
            "app/dashboard/page.tsx",
            "dashboard",
            "components/analytics-hero.tsx",
            "analytics hero",
            "revamp dashboard hero section",
        )

        self.assertIn('import { AnalyticsHero } from "../../components/analytics-hero";', content)
        self.assertIn('const sampleItems = [', content)
        self.assertIn('label: "Sample metric"', content)
        self.assertIn('Demo content preview until live data is connected.', content)
        self.assertIn('<AnalyticsHero state="ready" items={sampleItems} />', content)
        self.assertIn('Signal-rich dashboard', content)

    def test_next_loading_template_includes_dashboard_loading_copy(self) -> None:
        content = self.coder._next_loading_template("revamp dashboard hero section", "dashboard")

        self.assertIn("export default function Loading()", content)
        self.assertIn("Loading the latest signals and arranging the board.", content)
        self.assertIn('minHeight: "40vh"', content)
        self.assertIn('background: "#fffaf0"', content)

    def test_next_error_template_is_client_component_with_retry_action(self) -> None:
        content = self.coder._next_error_template("revamp dashboard hero section", "dashboard")

        self.assertIn('"use client";', content)
        self.assertIn("type ErrorProps = {", content)
        self.assertIn("reset: () => void;", content)
        self.assertIn("Something interrupted the signal feed", content)
        self.assertIn("The page is intact, but the live content could not be refreshed just now.", content)
        self.assertIn('onClick={reset}', content)
        self.assertIn("Try again", content)
        self.assertNotIn("error.message", content)

    def test_dashboard_templates_avoid_authoritative_placeholder_metrics(self) -> None:
        content = self.coder._next_page_template(
            "app/dashboard/page.tsx",
            "dashboard",
            "components/analytics-hero.tsx",
            "analytics hero",
            "revamp dashboard hero section",
        )

        self.assertIn('value: "Example value"', content)
        self.assertIn('value: "Sample trend"', content)
        self.assertNotIn('$128k', content)
        self.assertNotIn('+18%', content)

    def test_design_brief_can_override_visual_direction(self) -> None:
        design_brief = {
            "style_family": "calm",
            "visual_tone": "quiet studio",
            "palette_hint": "cool",
        }

        content = self.coder._next_component_template(
            "profile panel",
            "create account settings page",
            "settings",
            design_brief,
        )

        self.assertIn("Quiet Studio", content)
        self.assertIn('background: "#f7fbfc"', content)
        self.assertIn('color: "#16343a"', content)
        self.assertIn("Built for focus-heavy product flows", content)

    def test_nextjs_app_route_operations_include_loading_and_error_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            editor = FileEditor(str(root))
            workspace_profile = {
                "nextjs": {
                    "router_type": "app",
                    "app_dir": "app",
                    "pages_dir": None,
                    "component_directories": ["components"],
                }
            }
            state = {
                "issue_description": "revamp dashboard page with hero section",
                "workspace_dir": str(root),
            }

            operations = self.coder._build_nextjs_operations(state, workspace_profile, editor)

        file_paths = {operation["file_path"] for operation in operations}
        self.assertIn(".gitignore", file_paths)
        self.assertIn("app/dashboard/page.tsx", file_paths)
        self.assertIn("components/dashboard-hero.tsx", file_paths)
        self.assertIn("app/dashboard/loading.tsx", file_paths)
        self.assertIn("app/dashboard/error.tsx", file_paths)

    def test_nextjs_reactflow_operations_include_dependency_and_typed_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            editor = FileEditor(str(root))
            (root / "app").mkdir(parents=True)
            (root / "app/page.tsx").write_text("export default function HomePage() { return null; }\n", encoding="utf-8")
            (root / "package.json").write_text(
                json.dumps(
                    {
                        "name": "demo",
                        "version": "0.1.0",
                        "private": True,
                        "scripts": {"visual-review": "node scripts/visual-review.mjs"},
                        "dependencies": {"next": "16.1.6", "react": "18.3.1", "react-dom": "18.3.1"},
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            workspace_profile = {
                "nextjs": {
                    "router_type": "app",
                    "app_dir": "app",
                    "pages_dir": None,
                    "component_directories": ["components"],
                }
            }
            state = {
                "issue_description": "Create a github page layout and section with an interactive graph workspace powered by React Flow",
                "workspace_dir": str(root),
                "files_to_edit": ["package.json", "app/github/page.tsx"],
                "planning_context": {
                    "scope": {"in_scope": ["package.json", "app/github/page.tsx"], "out_of_scope": []},
                    "tasks": [{"id": "T1", "target_files": ["package.json", "app/github/page.tsx"]}],
                },
            }

            operations = self.coder._build_nextjs_operations(state, workspace_profile, editor)

        file_paths = {operation["file_path"] for operation in operations}
        self.assertIn("package.json", file_paths)
        self.assertIn("app/github/page.tsx", file_paths)
        self.assertIn("app/github/loading.tsx", file_paths)
        self.assertIn("app/github/error.tsx", file_paths)
        self.assertIn("components/github-section.tsx", file_paths)
        self.assertIn("components/react-flow/github-react-flow-workspace.tsx", file_paths)
        self.assertIn("components/graph/types.ts", file_paths)
        self.assertIn("components/graph/graph-data.ts", file_paths)
        self.assertIn("components/graph/GraphWorkspace.tsx", file_paths)
        self.assertIn("components/graph/GraphLegend.tsx", file_paths)
        self.assertIn("components/graph/GraphSummary.tsx", file_paths)
        self.assertIn("components/graph/GraphEmptyState.tsx", file_paths)

        package_operation = next(operation for operation in operations if operation["file_path"] == "package.json")
        self.assertEqual(json.loads(package_operation["content"])["dependencies"]["reactflow"], "^11.11.4")

        workspace_operation = next(
            operation for operation in operations if operation["file_path"] == "components/react-flow/github-react-flow-workspace.tsx"
        )
        self.assertIn('import ReactFlow, { Background, Controls, MarkerType, MiniMap, type Edge, type Node, type OnSelectionChangeParams } from "reactflow";', workspace_operation["content"])
        self.assertIn('import { graphEdges, graphNodes, graphToneByKind } from "../graph/graph-data";', workspace_operation["content"])
        self.assertIn('const fallbackNode = nodes.find((node) => node.id === "pipeline") ?? nodes[0];', workspace_operation["content"])
        self.assertNotIn('?? nodes[1]', workspace_operation["content"])
        self.assertIn('const nodes: Node<GraphNodeData>[] = graphNodes.map((node) => ({', workspace_operation["content"])
        self.assertIn('const edges: Edge[] = graphEdges.map((edge) => ({', workspace_operation["content"])
        self.assertIn('const handleSelectionChange = ({ nodes: selectedNodes }: OnSelectionChangeParams) => {', workspace_operation["content"])

        graph_data_operation = next(
            operation for operation in operations if operation["file_path"] == "components/graph/graph-data.ts"
        )
        self.assertIn('import type { GraphNodeData, GraphNodeKind, GraphSummaryItem } from "./types";', graph_data_operation["content"])

    def test_build_nextjs_operations_only_updates_home_preview_when_explicitly_targeted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "app/github").mkdir(parents=True)
            (root / "app/page.tsx").write_text("export default function HomePage() { return null; }\n", encoding="utf-8")
            (root / "package.json").write_text(
                json.dumps(
                    {
                        "name": "next-test-agent",
                        "version": "0.1.0",
                        "dependencies": {"next": "16.1.6", "react": "18.3.1", "react-dom": "18.3.1"},
                    },
                    indent=2,
                ) + "\n",
                encoding="utf-8",
            )
            editor = FileEditor(str(root))
            workspace_profile = {
                "nextjs": {
                    "router_type": "app",
                    "app_dir": "app",
                    "pages_dir": None,
                    "component_directories": ["components"],
                }
            }
            state = {
                "issue_description": "Create a github page layout and section with an interactive graph workspace powered by React Flow",
                "workspace_dir": str(root),
                "files_to_edit": ["app/github/page.tsx", "app/page.tsx"],
                "planning_context": {
                    "scope": {"in_scope": ["app/github/page.tsx", "app/page.tsx"], "out_of_scope": []},
                    "tasks": [{"id": "T1", "target_files": ["app/github/page.tsx", "app/page.tsx"]}],
                },
            }

            operations = self.coder._build_nextjs_operations(state, workspace_profile, editor)

        home_preview_operation = next(operation for operation in operations if operation["file_path"] == "app/page.tsx")
        self.assertIn('Open graph experience', home_preview_operation["content"])

    def test_normalize_package_json_content_removes_duplicate_dependency_keys(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            editor = FileEditor(str(root))
            (root / "package.json").write_text('{"name":"demo","dependencies":{"next":"16.1.6"}}\n', encoding="utf-8")

            normalized = self.coder._normalize_package_json_content(
                '{\n  "name": "demo",\n  "dependencies": {\n    "reactflow": "^11.11.4",\n    "reactflow": "^11.11.4",\n    "next": "16.1.6"\n  }\n}',
                editor,
            )

        package_data = json.loads(normalized)
        self.assertEqual(package_data["dependencies"]["reactflow"], "^11.11.4")
        self.assertEqual(list(package_data["dependencies"].keys()).count("reactflow"), 1)

    def test_normalize_package_json_content_preserves_existing_versions_on_non_upgrade_requests(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            editor = FileEditor(str(root))
            (root / "package.json").write_text(
                json.dumps(
                    {
                        "name": "smartfarm-dashboard",
                        "version": "0.1.0",
                        "private": True,
                        "scripts": {"visual-review": "node scripts/visual-review.mjs"},
                        "dependencies": {"next": "16.1.6", "react": "18.3.1", "react-dom": "18.3.1"},
                        "devDependencies": {"typescript": "5.6.3"},
                    },
                    indent=2,
                ) + "\n",
                encoding="utf-8",
            )

            normalized = self.coder._normalize_package_json_content(
                json.dumps(
                    {
                        "name": "next-test-agent",
                        "version": "0.1.0",
                        "private": True,
                        "scripts": {"visual-review": "next build"},
                        "dependencies": {"next": "latest", "react": "latest", "react-dom": "latest", "reactflow": "^11.11.4"},
                        "devDependencies": {"typescript": "latest"},
                    },
                    indent=2,
                ),
                editor,
                allow_version_changes=False,
            )

        package_data = json.loads(normalized)
        self.assertEqual(package_data["name"], "smartfarm-dashboard")
        self.assertEqual(package_data["scripts"]["visual-review"], "node scripts/visual-review.mjs")
        self.assertEqual(package_data["dependencies"]["next"], "16.1.6")
        self.assertEqual(package_data["dependencies"]["reactflow"], "^11.11.4")

    def test_nextjs_scaffold_is_skipped_for_dependency_upgrade_requests(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            editor = FileEditor(str(root))
            workspace_profile = {
                "nextjs": {
                    "router_type": "app",
                    "app_dir": "app",
                    "pages_dir": None,
                    "component_directories": ["components"],
                }
            }
            state = {
                "issue_description": "upgrade Next.js and display app version from package.json",
                "workspace_dir": str(root),
            }

            operations = self.coder._build_nextjs_operations(state, workspace_profile, editor)

        self.assertEqual(operations, [])

    def test_extract_route_slug_ignores_github_issue_url_noise(self) -> None:
        issue = (
            "Issue provider: github\n"
            "Source URL: https://github.com/bugek/next-test-agent/issues/1\n"
            "GitHub issue: bugek/next-test-agent#1\n"
            "Title: Create SmartFarm sample dashboard\n\n"
            "Description:\nBuild a SmartFarm sample dashboard in Next.js."
        )

        self.assertEqual(self.coder._extract_route_slug(issue), "dashboard")

    def test_extract_route_slug_prefers_structured_title_over_graph_body_terms(self) -> None:
        issue = (
            "Issue provider: github\n"
            "Source URL: https://github.com/bugek/next-test-agent/issues/12\n"
            "GitHub issue: bugek/next-test-agent#12\n"
            "Title: Create github page layout and section with an interactive graph workspace\n\n"
            "Description:\n"
            "Add a graph workspace with React Flow, loading state, error state, and visual review coverage."
        )

        self.assertEqual(self.coder._extract_route_slug(issue), "github")


if __name__ == "__main__":
    unittest.main()