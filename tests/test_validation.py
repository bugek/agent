from __future__ import annotations

import unittest
from argparse import Namespace
from types import SimpleNamespace
from unittest.mock import patch

from ai_code_agent import validation


class ValidationTest(unittest.TestCase):
    def test_parse_args_defaults_to_full_mode(self) -> None:
        args = validation.parse_args([])

        self.assertEqual(args.mode, "full")
        self.assertFalse(args.require_docker_sandbox)

    def test_parse_args_supports_require_docker_sandbox(self) -> None:
        args = validation.parse_args(["--mode", "quick", "--require-docker-sandbox"])

        self.assertEqual(args.mode, "quick")
        self.assertTrue(args.require_docker_sandbox)

    def test_get_validation_steps_returns_quick_subset(self) -> None:
        steps = validation.get_validation_steps("quick")

        self.assertEqual([step.label for step in steps], ["compileall", "unit tests"])

    def test_get_validation_steps_returns_full_suite(self) -> None:
        steps = validation.get_validation_steps("full")

        self.assertEqual(
            [step.label for step in steps],
            ["compileall", "unit tests", "nestjs smoke", "compose smoke", "nextjs visual review smoke", "retrieval evaluation"],
        )

    def test_run_step_invokes_subprocess_in_repo_root(self) -> None:
        step = validation.ValidationStep("compileall", ["python", "-m", "compileall", "ai_code_agent"])

        with patch("ai_code_agent.validation.subprocess.run", return_value=SimpleNamespace(returncode=0)) as mock_run:
            exit_code = validation._run_step(step)

        self.assertEqual(exit_code, 0)
        mock_run.assert_called_once_with(step.command, cwd=validation.REPO_ROOT, check=False)

    def test_main_runs_all_validation_steps_on_success(self) -> None:
        preflight = {"requested_mode": "auto", "resolved_mode": "docker", "image": "demo-image", "degraded": False, "docker_sandbox_ready": True}
        with patch("ai_code_agent.validation.parse_args", return_value=Namespace(mode="full", require_docker_sandbox=False)), patch(
            "ai_code_agent.validation.sandbox_preflight", return_value=preflight
        ), patch(
            "ai_code_agent.validation._run_step", side_effect=[0, 0, 0, 0, 0, 0]
        ) as mock_run_step:
            exit_code = validation.main([])

        self.assertEqual(exit_code, 0)
        self.assertEqual(mock_run_step.call_count, 6)
        called_steps = [call.args[0] for call in mock_run_step.call_args_list]
        self.assertEqual(
            [step.label for step in called_steps],
            ["compileall", "unit tests", "nestjs smoke", "compose smoke", "nextjs visual review smoke", "retrieval evaluation"],
        )
        self.assertEqual(called_steps[0].command, [validation.sys.executable, "-m", "compileall", "ai_code_agent", "tests"])
        self.assertEqual(called_steps[1].command, [validation.sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"])
        self.assertEqual(called_steps[2].command, [validation.sys.executable, "artifact/run_nestjs_smoke.py"])
        self.assertEqual(called_steps[3].command, [validation.sys.executable, "artifact/run_compose_smoke.py"])
        self.assertEqual(called_steps[4].command, [validation.sys.executable, "artifact/run_nextjs_visual_review_smoke.py"])
        self.assertEqual(called_steps[5].command, [validation.sys.executable, "artifact/run_retrieval_eval.py"])

    def test_main_stops_after_first_failing_step(self) -> None:
        preflight = {"requested_mode": "auto", "resolved_mode": "docker", "image": "demo-image", "degraded": False, "docker_sandbox_ready": True}
        with patch("ai_code_agent.validation.parse_args", return_value=Namespace(mode="full", require_docker_sandbox=False)), patch(
            "ai_code_agent.validation.sandbox_preflight", return_value=preflight
        ), patch(
            "ai_code_agent.validation._run_step", side_effect=[0, 7, 0, 0, 0, 0]
        ) as mock_run_step:
            exit_code = validation.main([])

        self.assertEqual(exit_code, 7)
        self.assertEqual(mock_run_step.call_count, 2)
        called_steps = [call.args[0] for call in mock_run_step.call_args_list]
        self.assertEqual([step.label for step in called_steps], ["compileall", "unit tests"])

    def test_main_quick_mode_runs_only_quick_steps(self) -> None:
        preflight = {"requested_mode": "auto", "resolved_mode": "docker", "image": "demo-image", "degraded": False, "docker_sandbox_ready": True}
        with patch("ai_code_agent.validation.parse_args", return_value=Namespace(mode="quick", require_docker_sandbox=False)), patch(
            "ai_code_agent.validation.sandbox_preflight", return_value=preflight
        ), patch(
            "ai_code_agent.validation._run_step", side_effect=[0, 0]
        ) as mock_run_step:
            exit_code = validation.main([])

        self.assertEqual(exit_code, 0)
        self.assertEqual([call.args[0].label for call in mock_run_step.call_args_list], ["compileall", "unit tests"])

    def test_main_fails_early_when_docker_sandbox_is_required_but_not_ready(self) -> None:
        preflight = {
            "requested_mode": "docker",
            "resolved_mode": "local",
            "image": "demo-image",
            "degraded": True,
            "fallback_reason": "docker_image_missing",
            "docker_sandbox_ready": False,
            "recommendation": "Build the sandbox image with: docker build -t demo-image .",
        }
        with patch("ai_code_agent.validation.parse_args", return_value=Namespace(mode="quick", require_docker_sandbox=True)), patch(
            "ai_code_agent.validation.sandbox_preflight", return_value=preflight
        ), patch("ai_code_agent.validation._run_step") as mock_run_step:
            exit_code = validation.main([])

        self.assertEqual(exit_code, 2)
        mock_run_step.assert_not_called()


if __name__ == "__main__":
    unittest.main()