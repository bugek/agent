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


if __name__ == "__main__":
    unittest.main()