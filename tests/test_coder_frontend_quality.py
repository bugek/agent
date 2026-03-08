from __future__ import annotations

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
        self.assertIn('const previewItems = [', content)
        self.assertIn('<AnalyticsHero state="ready" items={previewItems} />', content)
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


if __name__ == "__main__":
    unittest.main()