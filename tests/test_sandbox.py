from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

from ai_code_agent.tools.sandbox import SandboxRunner


class SandboxRunnerTest(unittest.TestCase):
    def test_auto_mode_falls_back_to_local_when_docker_missing(self) -> None:
        with patch("ai_code_agent.tools.sandbox.shutil.which", return_value=None):
            runner = SandboxRunner("demo-image", workspace_dir=".", mode="auto")
            startup = runner.start_container()

        self.assertEqual(startup["requested_mode"], "auto")
        self.assertEqual(startup["resolved_mode"], "local")
        self.assertEqual(startup["fallback_reason"], "docker_unavailable")
        self.assertTrue(startup["started"])

    def test_docker_required_reports_unavailable_without_fallback(self) -> None:
        with patch("ai_code_agent.tools.sandbox.shutil.which", return_value=None):
            runner = SandboxRunner("demo-image", workspace_dir=".", mode="docker_required")
            startup = runner.start_container()
            result = runner.execute("python -V")

        self.assertEqual(startup["resolved_mode"], "unavailable")
        self.assertFalse(startup["started"])
        self.assertEqual(startup["fallback_reason"], "docker_unavailable")
        self.assertEqual(result["exit_code"], 125)
        self.assertIn("docker_unavailable", result["stderr"])

    def test_auto_mode_uses_docker_when_image_exists(self) -> None:
        inspect_result = subprocess.CompletedProcess(["docker", "image", "inspect"], 0, stdout="[]", stderr="")
        with patch("ai_code_agent.tools.sandbox.shutil.which", return_value="docker"), patch(
            "ai_code_agent.tools.sandbox.subprocess.run", return_value=inspect_result
        ):
            runner = SandboxRunner("demo-image", workspace_dir=".", mode="auto")
            startup = runner.start_container()

        self.assertEqual(startup["resolved_mode"], "docker")
        self.assertTrue(startup["started"])
        self.assertIsNone(startup["fallback_reason"])

    def test_execute_uses_utf8_replace_decoding_for_local_commands(self) -> None:
        command_result = subprocess.CompletedProcess(["python"], 0, stdout="ok", stderr="")
        with patch("ai_code_agent.tools.sandbox.subprocess.run", return_value=command_result) as mock_run:
            runner = SandboxRunner("demo-image", workspace_dir=".", mode="local")
            runner.container_started = True

            runner.execute("python -V")

        kwargs = mock_run.call_args.kwargs
        self.assertEqual(kwargs["encoding"], "utf-8")
        self.assertEqual(kwargs["errors"], "replace")

    def test_execute_translates_workspace_rooted_env_paths_for_docker(self) -> None:
        command_result = subprocess.CompletedProcess(["docker"], 0, stdout="ok", stderr="")
        workspace_dir = r"D:\work\repo"
        env = {
            "AI_CODE_AGENT_VISUAL_REVIEW_DIR": r"D:\work\repo\.ai-code-agent\visual-review",
            "AI_CODE_AGENT_VISUAL_REVIEW_MANIFEST": r"D:\work\repo\.ai-code-agent\visual-review\manifest.json",
            "AI_CODE_AGENT_PLAYWRIGHT_SCREENSHOT_DIR": r"D:\work\repo\.ai-code-agent\visual-review\screenshots",
            "UNCHANGED_PATH": r"D:\outside\artifact.png",
            "PLAIN_VALUE": "keep-me",
        }

        with patch("ai_code_agent.tools.sandbox.subprocess.run", return_value=command_result) as mock_run:
            runner = SandboxRunner("demo-image", workspace_dir=workspace_dir, mode="docker")
            runner.container_started = True

            runner.execute("python -V", env=env)

        docker_cmd = mock_run.call_args.args[0]
        self.assertIn("AI_CODE_AGENT_VISUAL_REVIEW_DIR=/workspace/.ai-code-agent/visual-review", docker_cmd)
        self.assertIn(
            "AI_CODE_AGENT_VISUAL_REVIEW_MANIFEST=/workspace/.ai-code-agent/visual-review/manifest.json",
            docker_cmd,
        )
        self.assertIn(
            "AI_CODE_AGENT_PLAYWRIGHT_SCREENSHOT_DIR=/workspace/.ai-code-agent/visual-review/screenshots",
            docker_cmd,
        )
        self.assertIn("UNCHANGED_PATH=D:\\outside\\artifact.png", docker_cmd)
        self.assertIn("PLAIN_VALUE=keep-me", docker_cmd)

    def test_probe_reports_recommendation_when_docker_image_is_missing(self) -> None:
        inspect_result = subprocess.CompletedProcess(["docker", "image", "inspect"], 1, stdout="", stderr="missing")
        with patch("ai_code_agent.tools.sandbox.shutil.which", return_value="docker"), patch(
            "ai_code_agent.tools.sandbox.subprocess.run", return_value=inspect_result
        ):
            runner = SandboxRunner("demo-image", workspace_dir=".", mode="auto")
            report = runner.probe()

        self.assertEqual(report["resolved_mode"], "local")
        self.assertEqual(report["fallback_reason"], "docker_image_missing")
        self.assertTrue(report["degraded"])
        self.assertFalse(report["docker_sandbox_ready"])
        self.assertIn("docker build -t demo-image .", report["recommendation"])


if __name__ == "__main__":
    unittest.main()