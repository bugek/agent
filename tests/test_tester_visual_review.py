import tempfile
import unittest
from pathlib import Path

from ai_code_agent.agents.tester import TesterAgent
from ai_code_agent.config import AgentConfig


class StubLLM:
    def generate_json(self, system_prompt: str, user_prompt: str, schema: dict | None = None) -> dict:
        return {}


class TestTesterVisualReview(unittest.TestCase):
    def setUp(self) -> None:
        self.agent = TesterAgent(AgentConfig(workspace_dir="."), StubLLM())

    def test_build_nextjs_commands_includes_visual_review_script(self) -> None:
        workspace_profile = {
            "package_manager": "npm",
            "nextjs": {"router_type": "app"},
            "tsconfig_exists": True,
        }

        commands = self.agent._build_nextjs_commands(
            {"workspace_dir": "."},
            workspace_profile,
            {"lint", "typecheck", "visual-review"},
        )

        labels = [label for label, _, _, _ in commands]
        self.assertIn("script:visual-review", labels)
        visual_command = next(command for command in commands if command[0] == "script:visual-review")
        self.assertIn("AI_CODE_AGENT_VISUAL_REVIEW_MANIFEST", visual_command[3])
        self.assertIn("AI_CODE_AGENT_PLAYWRIGHT_SCREENSHOT_DIR", visual_command[3])

    def test_build_visual_review_detects_state_coverage_and_passed_signal(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            (temp_path / "app/dashboard").mkdir(parents=True)
            (temp_path / "components/dashboard").mkdir(parents=True)
            artifact_root = temp_path / ".ai-code-agent/visual-review"
            artifact_root.mkdir(parents=True)
            (temp_path / "app/dashboard/page.tsx").write_text("export default function Page() { return null; }", encoding="utf-8")
            (temp_path / "app/dashboard/loading.tsx").write_text("export default function Loading() { return null; }", encoding="utf-8")
            (temp_path / "app/dashboard/error.tsx").write_text("export default function Error() { return null; }", encoding="utf-8")
            screenshot_path = artifact_root / "screenshots/dashboard-home.png"
            screenshot_path.parent.mkdir(parents=True, exist_ok=True)
            screenshot_path.write_bytes(b"png-bytes")
            (artifact_root / "manifest.json").write_text(
                '{"tool":"playwright","generated_at":"2026-03-08T00:00:00Z","artifacts":[{"path":"screenshots/dashboard-home.png","route":"/dashboard","title":"Dashboard","viewport":{"width":1440,"height":900}}]}',
                encoding="utf-8",
            )
            (temp_path / "components/dashboard/dashboard-shell.tsx").write_text(
                '\n'.join(
                    [
                        'const state = "ready";',
                        'if (state === "loading") return null;',
                        'if (state === "empty") return null;',
                        'if (state === "error") return null;',
                        'if (state === "ready") return null;',
                    ]
                ),
                encoding="utf-8",
            )

            state = {
                "workspace_dir": temp_dir,
                "patches": [
                    {"file": "app/dashboard/page.tsx"},
                    {"file": "app/dashboard/loading.tsx"},
                    {"file": "app/dashboard/error.tsx"},
                    {"file": "components/dashboard/dashboard-shell.tsx"},
                ],
                "planning_context": {"design_brief": {"style_keywords": ["editorial"]}},
            }
            workspace_profile = {"nextjs": {"router_type": "app"}}
            command_results = [{"label": "script:visual-review", "exit_code": 0}]

            visual_review = self.agent._build_visual_review(state, workspace_profile, command_results)

            self.assertIsNotNone(visual_review)
            self.assertEqual(visual_review["screenshot_status"], "passed")
            self.assertEqual(visual_review["artifact_count"], 1)
            self.assertEqual(visual_review["artifact_manifest"], ".ai-code-agent/visual-review/manifest.json")
            self.assertEqual(visual_review["artifacts"][0]["path"], ".ai-code-agent/visual-review/screenshots/dashboard-home.png")
            self.assertEqual(visual_review["artifacts"][0]["route"], "/dashboard")
            self.assertTrue(visual_review["state_coverage"]["loading_file"])
            self.assertTrue(visual_review["state_coverage"]["error_file"])
            self.assertTrue(visual_review["state_coverage"]["loading_state"])
            self.assertTrue(visual_review["state_coverage"]["empty_state"])
            self.assertTrue(visual_review["state_coverage"]["error_state"])
            self.assertTrue(visual_review["state_coverage"]["success_state"])

    def test_build_visual_review_marks_missing_artifacts_when_script_produces_no_images(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            (temp_path / "app/dashboard").mkdir(parents=True)
            (temp_path / "components/dashboard").mkdir(parents=True)
            (temp_path / "app/dashboard/page.tsx").write_text("export default function Page() { return null; }", encoding="utf-8")
            (temp_path / "app/dashboard/loading.tsx").write_text("export default function Loading() { return null; }", encoding="utf-8")
            (temp_path / "app/dashboard/error.tsx").write_text("export default function Error() { return null; }", encoding="utf-8")
            (temp_path / "components/dashboard/dashboard-shell.tsx").write_text(
                '\n'.join(
                    [
                        'const state = "ready";',
                        'if (state === "loading") return null;',
                        'if (state === "empty") return null;',
                        'if (state === "error") return null;',
                        'if (state === "ready") return null;',
                    ]
                ),
                encoding="utf-8",
            )

            state = {
                "workspace_dir": temp_dir,
                "patches": [
                    {"file": "app/dashboard/page.tsx"},
                    {"file": "app/dashboard/loading.tsx"},
                    {"file": "app/dashboard/error.tsx"},
                    {"file": "components/dashboard/dashboard-shell.tsx"},
                ],
                "planning_context": {"design_brief": {"style_keywords": ["editorial"]}},
            }
            workspace_profile = {"nextjs": {"router_type": "app"}}
            command_results = [{"label": "script:visual-review", "exit_code": 0, "stdout": "", "stderr": ""}]

            visual_review = self.agent._build_visual_review(state, workspace_profile, command_results)

            self.assertEqual(visual_review["screenshot_status"], "missing_artifacts")
            self.assertEqual(visual_review["artifact_count"], 0)


if __name__ == "__main__":
    unittest.main()