from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from ai_code_agent import validation


class ValidationTest(unittest.TestCase):
    def test_run_step_invokes_subprocess_in_repo_root(self) -> None:
        step = validation.ValidationStep("compileall", ["python", "-m", "compileall", "ai_code_agent"])

        with patch("ai_code_agent.validation.subprocess.run", return_value=SimpleNamespace(returncode=0)) as mock_run:
            exit_code = validation._run_step(step)

        self.assertEqual(exit_code, 0)
        mock_run.assert_called_once_with(step.command, cwd=validation.REPO_ROOT, check=False)

    def test_main_runs_all_validation_steps_on_success(self) -> None:
        with patch("ai_code_agent.validation._run_step", side_effect=[0, 0, 0]) as mock_run_step:
            exit_code = validation.main()

        self.assertEqual(exit_code, 0)
        self.assertEqual(mock_run_step.call_count, 3)
        called_steps = [call.args[0] for call in mock_run_step.call_args_list]
        self.assertEqual([step.label for step in called_steps], ["compileall", "unit tests", "retrieval evaluation"])
        self.assertEqual(called_steps[0].command, [validation.sys.executable, "-m", "compileall", "ai_code_agent", "tests"])
        self.assertEqual(called_steps[1].command, [validation.sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"])
        self.assertEqual(called_steps[2].command, [validation.sys.executable, "artifact/run_retrieval_eval.py"])

    def test_main_stops_after_first_failing_step(self) -> None:
        with patch("ai_code_agent.validation._run_step", side_effect=[0, 7, 0]) as mock_run_step:
            exit_code = validation.main()

        self.assertEqual(exit_code, 7)
        self.assertEqual(mock_run_step.call_count, 2)
        called_steps = [call.args[0] for call in mock_run_step.call_args_list]
        self.assertEqual([step.label for step in called_steps], ["compileall", "unit tests"])


if __name__ == "__main__":
    unittest.main()