from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

from ai_code_agent.tools.git_ops import GitOps


class GitOpsTest(unittest.TestCase):
    def test_is_repository_returns_false_when_git_rev_parse_fails(self) -> None:
        completed = subprocess.CompletedProcess(["git", "rev-parse"], 1, stdout="", stderr="fatal: not a git repository")
        with patch("ai_code_agent.tools.git_ops.subprocess.run", return_value=completed):
            git_ops = GitOps(".")

            result = git_ops.is_repository()

        self.assertFalse(result)

    def test_git_commands_use_utf8_replace_decoding(self) -> None:
        completed = subprocess.CompletedProcess(["git", "status"], 0, stdout="", stderr="")
        with patch("ai_code_agent.tools.git_ops.subprocess.run", return_value=completed) as mock_run:
            git_ops = GitOps(".")

            git_ops.current_branch()

        kwargs = mock_run.call_args.kwargs
        self.assertEqual(kwargs["encoding"], "utf-8")
        self.assertEqual(kwargs["errors"], "replace")

    def test_create_branch_is_idempotent_when_already_checked_out(self) -> None:
        completed = subprocess.CompletedProcess(["git", "rev-parse"], 0, stdout="feature\n", stderr="")
        with patch("ai_code_agent.tools.git_ops.subprocess.run", return_value=completed) as mock_run:
            git_ops = GitOps(".")

            result = git_ops.create_branch("feature")

        self.assertTrue(result)
        self.assertEqual(mock_run.call_count, 1)

    def test_ensure_remote_base_branch_bootstraps_empty_remote(self) -> None:
        responses = [
            subprocess.CompletedProcess(["git"], 0, stdout="feature\n", stderr=""),
            subprocess.CompletedProcess(["git"], 1, stdout="", stderr=""),
            subprocess.CompletedProcess(["git"], 1, stdout="", stderr=""),
            subprocess.CompletedProcess(["git"], 0, stdout="", stderr=""),
            subprocess.CompletedProcess(["git"], 0, stdout="", stderr=""),
            subprocess.CompletedProcess(["git"], 0, stdout="", stderr=""),
            subprocess.CompletedProcess(["git"], 0, stdout="", stderr=""),
            subprocess.CompletedProcess(["git"], 0, stdout="", stderr=""),
            subprocess.CompletedProcess(["git"], 0, stdout="", stderr=""),
        ]
        with patch("ai_code_agent.tools.git_ops.subprocess.run", side_effect=responses) as mock_run:
            git_ops = GitOps(".")

            result = git_ops.ensure_remote_base_branch("main")

        self.assertTrue(result)
        commands = [call.args[0][1:] for call in mock_run.call_args_list]
        self.assertIn(["ls-remote", "--heads", "origin", "refs/heads/main"], commands)
        self.assertIn(["checkout", "--orphan", "main"], commands)
        self.assertIn(["commit", "--allow-empty", "-m", "Initialize repository base branch"], commands)
        self.assertIn(["rebase", "--onto", "main", "--root"], commands)

    def test_has_pending_changes_reads_status_porcelain(self) -> None:
        completed = subprocess.CompletedProcess(["git", "status"], 0, stdout=" M app/page.tsx\n", stderr="")
        with patch("ai_code_agent.tools.git_ops.subprocess.run", return_value=completed):
            git_ops = GitOps(".")

            result = git_ops.has_pending_changes()

        self.assertTrue(result)

    def test_push_branch_retries_with_force_with_lease_on_non_fast_forward(self) -> None:
        responses = [
            subprocess.CompletedProcess(["git"], 1, stdout="", stderr="! [rejected] feature -> feature (non-fast-forward)"),
            subprocess.CompletedProcess(["git"], 0, stdout="", stderr=""),
        ]
        with patch("ai_code_agent.tools.git_ops.subprocess.run", side_effect=responses) as mock_run:
            git_ops = GitOps(".")

            result = git_ops.push_branch("feature")

        self.assertTrue(result)
        commands = [call.args[0][1:] for call in mock_run.call_args_list]
        self.assertEqual(commands, [["push", "-u", "origin", "feature"], ["push", "--force-with-lease", "-u", "origin", "feature"]])

    def test_ensure_remote_base_branch_rebases_when_histories_are_unrelated(self) -> None:
        responses = [
            subprocess.CompletedProcess(["git"], 0, stdout="feature\n", stderr=""),
            subprocess.CompletedProcess(["git"], 0, stdout="refs/heads/main\n", stderr=""),
            subprocess.CompletedProcess(["git"], 0, stdout="", stderr=""),
            subprocess.CompletedProcess(["git"], 1, stdout="", stderr="fatal: no merge base\n"),
            subprocess.CompletedProcess(["git"], 0, stdout="", stderr=""),
        ]
        with patch("ai_code_agent.tools.git_ops.subprocess.run", side_effect=responses) as mock_run:
            git_ops = GitOps(".")

            result = git_ops.ensure_remote_base_branch("main")

        self.assertTrue(result)
        commands = [call.args[0][1:] for call in mock_run.call_args_list]
        self.assertIn(["merge-base", "main", "feature"], commands)
        self.assertIn(["rebase", "--onto", "main", "--root"], commands)


if __name__ == "__main__":
    unittest.main()