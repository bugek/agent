from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
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

    def test_compose_mode_starts_stack_when_compose_is_configured(self) -> None:
        version_result = subprocess.CompletedProcess(["docker", "compose", "version"], 0, stdout="Docker Compose v2", stderr="")
        up_result = subprocess.CompletedProcess(["docker", "compose", "up"], 0, stdout="started", stderr="")
        ps_result = subprocess.CompletedProcess(
            ["docker", "compose", "ps"],
            0,
            stdout='[{"Service": "app", "State": "running", "Health": "healthy"}]',
            stderr="",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            compose_file = f"{temp_dir}/docker-compose.yml"
            with open(compose_file, "w", encoding="utf-8") as handle:
                handle.write("services:\n  app:\n    image: busybox\n")

            with patch("ai_code_agent.tools.sandbox.shutil.which", return_value="docker"), patch(
                "ai_code_agent.tools.sandbox.subprocess.run",
                side_effect=[version_result, up_result, ps_result],
            ) as mock_run:
                runner = SandboxRunner(
                    "demo-image",
                    workspace_dir=temp_dir,
                    mode="compose",
                    compose_file=compose_file,
                    compose_service="app",
                    compose_project_name="demo-stack",
                    compose_ready_services=["app"],
                )
                startup = runner.start_container()

        self.assertEqual(startup["resolved_mode"], "compose")
        self.assertTrue(startup["started"])
        self.assertEqual(startup["compose_service"], "app")
        self.assertEqual(startup["compose_readiness_status"], "ready")
        self.assertEqual(startup["compose_ready_services"], ["app"])
        compose_up_command = mock_run.call_args_list[1].args[0]
        self.assertEqual(compose_up_command[:3], ["docker", "compose", "-f"])
        self.assertEqual(Path(compose_up_command[3]).resolve(), Path(compose_file).resolve())
        self.assertEqual(compose_up_command[4:6], ["-p", "demo-stack"])
        self.assertIn("up", compose_up_command)

    def test_execute_uses_compose_exec_with_translated_env_paths(self) -> None:
        command_result = subprocess.CompletedProcess(["docker", "compose", "exec"], 0, stdout="ok", stderr="")
        workspace_dir = r"D:\work\repo"
        env = {
            "AI_CODE_AGENT_VISUAL_REVIEW_DIR": r"D:\work\repo\.ai-code-agent\visual-review",
            "PLAIN_VALUE": "keep-me",
        }

        with patch("ai_code_agent.tools.sandbox.subprocess.run", return_value=command_result) as mock_run, patch(
            "ai_code_agent.tools.sandbox.Path.exists",
            return_value=True,
        ):
            runner = SandboxRunner(
                "demo-image",
                workspace_dir=workspace_dir,
                mode="compose",
                compose_file=r"D:\work\repo\docker-compose.yml",
                compose_service="app",
                compose_project_name="demo-stack",
            )
            runner.container_started = True

            runner.execute("python -V", env=env)

        compose_cmd = mock_run.call_args.args[0]
        self.assertEqual(compose_cmd[:6], ["docker", "compose", "-f", r"D:\work\repo\docker-compose.yml", "-p", "demo-stack"])
        self.assertIn("exec", compose_cmd)
        self.assertIn("-T", compose_cmd)
        self.assertIn("AI_CODE_AGENT_VISUAL_REVIEW_DIR=/workspace/.ai-code-agent/visual-review", compose_cmd)
        self.assertIn("PLAIN_VALUE=keep-me", compose_cmd)
        self.assertIn("app", compose_cmd)

    def test_cleanup_stops_compose_stack(self) -> None:
        command_result = subprocess.CompletedProcess(["docker", "compose", "down"], 0, stdout="", stderr="")
        with patch("ai_code_agent.tools.sandbox.subprocess.run", return_value=command_result) as mock_run, patch(
            "ai_code_agent.tools.sandbox.Path.exists",
            return_value=True,
        ):
            runner = SandboxRunner(
                "demo-image",
                workspace_dir=r"D:\work\repo",
                mode="compose",
                compose_file=r"D:\work\repo\docker-compose.yml",
                compose_service="app",
                compose_project_name="demo-stack",
            )
            runner.container_started = True

            result = runner.cleanup()

        self.assertEqual(result, {"cleaned": True, "mode": "compose"})
        compose_cmd = mock_run.call_args.args[0]
        self.assertEqual(compose_cmd[:6], ["docker", "compose", "-f", r"D:\work\repo\docker-compose.yml", "-p", "demo-stack"])
        self.assertIn("down", compose_cmd)

    def test_cleanup_stops_partially_started_compose_stack_after_readiness_failure(self) -> None:
        command_result = subprocess.CompletedProcess(["docker", "compose", "down"], 0, stdout="", stderr="")
        with patch("ai_code_agent.tools.sandbox.subprocess.run", return_value=command_result) as mock_run, patch(
            "ai_code_agent.tools.sandbox.Path.exists",
            return_value=True,
        ):
            runner = SandboxRunner(
                "demo-image",
                workspace_dir=r"D:\work\repo",
                mode="compose_required",
                compose_file=r"D:\work\repo\docker-compose.yml",
                compose_service="app",
                compose_project_name="demo-stack",
            )
            runner.compose_stack_started = True

            result = runner.cleanup()

        self.assertEqual(result, {"cleaned": True, "mode": "compose_required"})
        compose_cmd = mock_run.call_args.args[0]
        self.assertEqual(compose_cmd[:6], ["docker", "compose", "-f", r"D:\work\repo\docker-compose.yml", "-p", "demo-stack"])
        self.assertIn("down", compose_cmd)

    def test_compose_required_reports_unavailable_when_compose_file_missing(self) -> None:
        with patch("ai_code_agent.tools.sandbox.shutil.which", return_value="docker"):
            runner = SandboxRunner(
                "demo-image",
                workspace_dir=".",
                mode="compose_required",
                compose_file="missing-compose.yml",
                compose_service="app",
            )
            startup = runner.start_container()

        self.assertEqual(startup["resolved_mode"], "unavailable")
        self.assertEqual(startup["fallback_reason"], "compose_file_missing")
        self.assertFalse(startup["started"])

    def test_compose_mode_falls_back_when_service_is_not_ready_and_captures_logs(self) -> None:
        version_result = subprocess.CompletedProcess(["docker", "compose", "version"], 0, stdout="Docker Compose v2", stderr="")
        up_result = subprocess.CompletedProcess(["docker", "compose", "up"], 0, stdout="started", stderr="")
        ps_result = subprocess.CompletedProcess(
            ["docker", "compose", "ps"],
            0,
            stdout='[{"Service": "app", "State": "starting", "Health": "starting"}]',
            stderr="",
        )
        logs_result = subprocess.CompletedProcess(["docker", "compose", "logs"], 0, stdout="app | booting", stderr="")

        def mock_run(command, **kwargs):
            if command[-2:] == ["compose", "version"]:
                return version_result
            if "up" in command:
                return up_result
            if "ps" in command:
                return ps_result
            if "logs" in command:
                return logs_result
            raise AssertionError(f"Unexpected command: {command}")

        with tempfile.TemporaryDirectory() as temp_dir, patch("ai_code_agent.tools.sandbox.shutil.which", return_value="docker"), patch(
            "ai_code_agent.tools.sandbox.subprocess.run",
            side_effect=mock_run,
        ), patch("ai_code_agent.tools.sandbox.time.sleep", return_value=None):
            compose_file = f"{temp_dir}/docker-compose.yml"
            with open(compose_file, "w", encoding="utf-8") as handle:
                handle.write("services:\n  app:\n    image: busybox\n")

            runner = SandboxRunner(
                "demo-image",
                workspace_dir=temp_dir,
                mode="compose",
                compose_file=compose_file,
                compose_service="app",
                compose_project_name="demo-stack",
                compose_ready_services=["app"],
                compose_readiness_timeout_seconds=1,
            )
            startup = runner.start_container()

            self.assertEqual(startup["resolved_mode"], "local")
            self.assertEqual(startup["fallback_reason"], "compose_service_not_ready")
            self.assertEqual(startup["compose_readiness_status"], "timed_out")
            self.assertEqual(startup["compose_logs_path"], ".ai-code-agent/compose/demo-stack-logs.txt")
            self.assertTrue((Path(temp_dir) / ".ai-code-agent/compose/demo-stack-logs.txt").exists())

    def test_parse_compose_ps_output_accepts_newline_delimited_json(self) -> None:
        runner = SandboxRunner("demo-image", workspace_dir=".", mode="compose")

        parsed = runner._parse_compose_ps_output(
            '{"Service": "app", "State": "running"}\n{"Service": "sidecar", "State": "running"}'
        )

        self.assertEqual([item["Service"] for item in parsed], ["app", "sidecar"])


if __name__ == "__main__":
    unittest.main()