from __future__ import annotations

import unittest
from unittest.mock import patch

from ai_code_agent import orchestrator


class OrchestratorAuditTrailTest(unittest.TestCase):
    def test_plan_node_records_retrieval_and_policy_details(self) -> None:
        planner_result = {
            "plan": "Inspect allowed files.",
            "files_to_edit": ["ai_code_agent/main.py"],
            "planning_context": {
                "retrieval_strategy": "hybrid",
                "blocked_files_to_edit": [{"file_path": "artifact/fixtures/demo.txt", "reason": "matched deny rule"}],
                "graph_seed_files": ["ai_code_agent/main.py", "ai_code_agent/orchestrator.py"],
            },
        }

        with patch("ai_code_agent.agents.planner.PlannerAgent") as mock_planner, patch(
            "ai_code_agent.llm.client.LLMClient.from_config", return_value=object()
        ):
            mock_planner.return_value.run.return_value = planner_result
            result = orchestrator.plan_node(
                {
                    "issue_description": "update app",
                    "workspace_dir": ".",
                    "run_id": "run-123",
                    "workflow_started_at": "2026-03-08T10:22:33Z",
                }
            )

        event = result["execution_events"][-1]
        start_event = result["execution_events"][-2]
        self.assertEqual(start_event["event_type"], "node_started")
        self.assertEqual(start_event["status"], "started")
        self.assertEqual(start_event["attempt"], 1)
        self.assertEqual(event["node"], "plan")
        self.assertEqual(event["run_id"], "run-123")
        self.assertEqual(event["sequence"], 2)
        self.assertEqual(event["attempt"], 1)
        self.assertEqual(event["event_type"], "node_completed")
        self.assertEqual(event["details"]["retrieval_strategy"], "hybrid")
        self.assertEqual(event["details"]["blocked_files_to_edit"], 1)
        self.assertEqual(event["details"]["graph_seed_files"], 2)
        self.assertEqual(event["details"]["edit_intent_count"], 0)
        self.assertEqual(result["execution_metrics"]["planning"]["blocked_file_count"], 1)

    def test_code_node_records_codegen_decision_details(self) -> None:
        coder_result = {
            "patches": [{"file": "docs/readme.md"}],
            "codegen_summary": {
                "requested_operations": 3,
                "blocked_operations": [{"file_path": "artifact/fixtures/demo.txt", "reason": "matched deny rule"}],
                "failed_operations": ["replace_text failed for docs/missing.md"],
                "generated_by": "llm",
            },
        }

        with patch("ai_code_agent.agents.coder.CoderAgent") as mock_coder, patch(
            "ai_code_agent.llm.client.LLMClient.from_config", return_value=object()
        ):
            mock_coder.return_value.run.return_value = coder_result
            result = orchestrator.code_node(
                {
                    "issue_description": "update docs",
                    "workspace_dir": ".",
                    "run_id": "run-123",
                    "workflow_started_at": "2026-03-08T10:22:33Z",
                    "execution_events": [
                        {
                            "run_id": "run-123",
                            "sequence": 1,
                            "timestamp": "2026-03-08T10:22:33Z",
                            "node": "plan",
                            "event_type": "node_started",
                            "attempt": 1,
                            "status": "started",
                            "duration_ms": 0,
                        },
                        {
                            "run_id": "run-123",
                            "sequence": 2,
                            "timestamp": "2026-03-08T10:22:40Z",
                            "node": "plan",
                            "event_type": "node_completed",
                            "attempt": 1,
                            "status": "completed",
                            "duration_ms": 7000,
                        }
                    ],
                }
            )

        event = result["execution_events"][-1]
        start_event = result["execution_events"][-2]
        self.assertEqual(start_event["event_type"], "node_started")
        self.assertEqual(start_event["attempt"], 1)
        self.assertEqual(event["node"], "code")
        self.assertEqual(event["sequence"], 4)
        self.assertEqual(event["attempt"], 1)
        self.assertGreaterEqual(event["duration_ms"], 0)
        self.assertEqual(event["details"]["requested_operations"], 3)
        self.assertEqual(event["details"]["blocked_operations"], 1)
        self.assertEqual(event["details"]["failed_operations"], 1)
        self.assertEqual(event["details"]["generated_by"], "llm")
        self.assertEqual(event["details"]["remediation_applied"], False)
        self.assertEqual(event["details"]["remediation_focus_count"], 0)
        self.assertEqual(result["execution_metrics"]["coding"]["blocked_operation_count"], 1)

    def test_review_node_records_summary_status_and_risks(self) -> None:
        review_result = {
            "review_approved": True,
            "review_comments": ["Review passed."],
            "review_summary": {
                "status": "approved",
                "residual_risks": ["1 operation(s) were blocked by file edit policy."],
                "remediation": {
                    "required": False,
                    "focus_areas": [],
                },
            },
        }

        with patch("ai_code_agent.agents.reviewer.ReviewerAgent") as mock_reviewer, patch(
            "ai_code_agent.llm.client.LLMClient.from_config", return_value=object()
        ):
            mock_reviewer.return_value.run.return_value = review_result
            result = orchestrator.review_node(
                {
                    "issue_description": "update docs",
                    "workspace_dir": ".",
                    "test_passed": True,
                    "retry_count": 0,
                    "run_id": "run-123",
                    "workflow_started_at": "2026-03-08T10:22:33Z",
                }
            )

        event = result["execution_events"][-1]
        start_event = result["execution_events"][-2]
        self.assertEqual(start_event["event_type"], "node_started")
        self.assertEqual(event["node"], "review")
        self.assertEqual(event["status"], "approved")
        self.assertEqual(event["details"]["review_status"], "approved")
        self.assertEqual(event["details"]["residual_risks"], 1)
        self.assertEqual(event["details"]["remediation_required"], False)
        self.assertEqual(event["details"]["remediation_focus_count"], 0)
        self.assertEqual(result["execution_metrics"]["review"]["status"], "approved")

    def test_test_node_marks_failed_status_and_second_attempt(self) -> None:
        tester_result = {
            "test_passed": False,
            "test_results": "compileall(exit=1):\nboom\n",
            "testing_summary": {
                "validation_strategy": "targeted_retry",
                "selected_command_labels": ["compileall"],
                "skipped_command_labels": ["script:test"],
                "requested_retry_labels": ["script:test"],
                "retry_policy_reason": "default_targeted_retry",
                "retry_policy_history_source": None,
                "retry_policy_confidence": "weak",
                "stop_retry_after_failure": False,
                "retry_policy_stop_reason": None,
            },
            "visual_review": None,
        }

        with patch("ai_code_agent.agents.tester.TesterAgent") as mock_tester, patch(
            "ai_code_agent.llm.client.LLMClient.from_config", return_value=object()
        ):
            mock_tester.return_value.run.return_value = tester_result
            result = orchestrator.test_node(
                {
                    "issue_description": "update docs",
                    "workspace_dir": ".",
                    "run_id": "run-123",
                    "workflow_started_at": "2026-03-08T10:22:33Z",
                    "execution_events": [
                        {
                            "run_id": "run-123",
                            "sequence": 1,
                            "timestamp": "2026-03-08T10:22:33Z",
                            "node": "plan",
                            "event_type": "node_started",
                            "attempt": 1,
                            "status": "started",
                            "duration_ms": 0,
                        },
                        {
                            "run_id": "run-123",
                            "sequence": 2,
                            "timestamp": "2026-03-08T10:22:40Z",
                            "node": "plan",
                            "event_type": "node_completed",
                            "attempt": 1,
                            "status": "completed",
                            "duration_ms": 7000,
                        },
                        {
                            "run_id": "run-123",
                            "sequence": 3,
                            "timestamp": "2026-03-08T10:22:45Z",
                            "node": "test",
                            "event_type": "node_started",
                            "attempt": 1,
                            "status": "started",
                            "duration_ms": 0,
                        },
                        {
                            "run_id": "run-123",
                            "sequence": 4,
                            "timestamp": "2026-03-08T10:23:10Z",
                            "node": "test",
                            "event_type": "node_completed",
                            "attempt": 1,
                            "status": "failed",
                            "duration_ms": 30000,
                        },
                    ],
                }
            )

        event = result["execution_events"][-1]
        start_event = result["execution_events"][-2]
        self.assertEqual(start_event["event_type"], "node_started")
        self.assertEqual(start_event["attempt"], 2)
        self.assertEqual(event["node"], "test")
        self.assertEqual(event["status"], "failed")
        self.assertEqual(event["attempt"], 2)
        self.assertEqual(event["details"]["validation_strategy"], "targeted_retry")
        self.assertEqual(event["details"]["selected_command_count"], 1)
        self.assertEqual(event["details"]["skipped_command_count"], 1)
        self.assertEqual(event["details"]["requested_retry_count"], 1)
        self.assertEqual(event["details"]["retry_policy_reason"], "default_targeted_retry")
        self.assertEqual(event["details"]["retry_policy_history_source"], None)
        self.assertEqual(event["details"]["retry_policy_confidence"], "weak")
        self.assertEqual(event["details"]["stop_retry_after_failure"], False)
        self.assertEqual(event["details"]["retry_policy_stop_reason"], None)
        self.assertEqual(result["execution_metrics"]["testing"]["status"], "failed")

    def test_should_continue_stops_after_full_fallback_failure(self) -> None:
        route = orchestrator.should_continue(
            {
                "review_approved": False,
                "test_passed": False,
                "retry_count": 1,
                "testing_summary": {"stop_retry_after_failure": True},
            }
        )

        self.assertEqual(route, "fail")

    def test_create_pr_node_records_branch_and_remote_url(self) -> None:
        with patch("ai_code_agent.orchestrator._build_runtime") as mock_runtime, patch(
            "ai_code_agent.tools.git_ops.GitOps"
        ) as mock_git_ops, patch("ai_code_agent.orchestrator.create_remote_pr") as mock_create_remote_pr:
            mock_runtime.return_value = (
                type("Config", (), {"auto_commit": True, "auto_push": True})(),
                object(),
            )
            mock_git_ops.return_value.create_branch.return_value = True
            mock_git_ops.return_value.commit_changes.return_value = True
            mock_git_ops.return_value.push_branch.return_value = True
            mock_create_remote_pr.return_value = {
                "outcome": "created",
                "reason": "opened_github_pr",
                "provider": "github",
                "branch_name": "ai-code-agent/gh-42-fix-flaky-validation",
                "base_branch": "main",
                "pr_url": "https://github.com/octo/repo/pull/9",
                "message": "Committed, pushed, and opened GitHub PR: https://github.com/octo/repo/pull/9",
                "error": None,
            }

            result = orchestrator.create_pr_node(
                {
                    "workspace_dir": ".",
                    "issue_description": "Fix flaky validation",
                    "issue_context": {"provider": "github", "issue_number": 42, "title": "Fix flaky validation"},
                    "patches": [{"file": "docs/readme.md"}],
                    "run_id": "run-123",
                    "workflow_started_at": "2026-03-08T10:22:33Z",
                }
            )

        event = result["execution_events"][-1]
        self.assertEqual(result["created_pr_url"], "https://github.com/octo/repo/pull/9")
        self.assertEqual(result["create_pr_result"]["outcome"], "created")
        self.assertEqual(event["details"]["created_pr_url"], "https://github.com/octo/repo/pull/9")
        self.assertEqual(event["details"]["outcome"], "created")
        self.assertEqual(event["details"]["reason"], "opened_github_pr")
        self.assertEqual(event["details"]["issue_provider"], "github")
        self.assertIn("ai-code-agent/gh-42-fix-flaky-validation", event["details"]["branch_name"])

    def test_create_pr_node_skips_git_automation_for_non_git_workspace(self) -> None:
        with patch("ai_code_agent.orchestrator._build_runtime", return_value=(type("Config", (), {"auto_commit": True, "auto_push": True})(), None)), patch(
            "ai_code_agent.tools.git_ops.GitOps"
        ) as mock_git_ops:
            mock_git_ops.return_value.is_repository.return_value = False

            result = orchestrator.create_pr_node(
                {
                    "workspace_dir": ".",
                    "issue_description": "add users endpoint",
                    "patches": [{"file": "src/users/users.controller.ts", "diff": "..."}],
                    "issue_context": {"provider": "github", "issue_number": 42, "title": "Add users endpoint"},
                    "execution_log": [],
                    "execution_events": [],
                }
            )

        self.assertIsNone(result["created_pr_url"])
        self.assertEqual(result["create_pr_result"]["outcome"], "skipped")
        self.assertEqual(result["create_pr_result"]["reason"], "non_git_workspace")
        self.assertIn("not a git repository", result["create_pr_result"]["message"])


if __name__ == "__main__":
    unittest.main()