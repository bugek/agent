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
            result = orchestrator.plan_node({"issue_description": "update app", "workspace_dir": "."})

        event = result["execution_events"][-1]
        self.assertEqual(event["node"], "plan")
        self.assertEqual(event["details"]["retrieval_strategy"], "hybrid")
        self.assertEqual(event["details"]["blocked_files_to_edit"], 1)
        self.assertEqual(event["details"]["graph_seed_files"], 2)

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
            result = orchestrator.code_node({"issue_description": "update docs", "workspace_dir": "."})

        event = result["execution_events"][-1]
        self.assertEqual(event["node"], "code")
        self.assertEqual(event["details"]["requested_operations"], 3)
        self.assertEqual(event["details"]["blocked_operations"], 1)
        self.assertEqual(event["details"]["failed_operations"], 1)
        self.assertEqual(event["details"]["generated_by"], "llm")

    def test_review_node_records_summary_status_and_risks(self) -> None:
        review_result = {
            "review_approved": True,
            "review_comments": ["Review passed."],
            "review_summary": {
                "status": "approved",
                "residual_risks": ["1 operation(s) were blocked by file edit policy."],
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
                }
            )

        event = result["execution_events"][-1]
        self.assertEqual(event["node"], "review")
        self.assertEqual(event["details"]["review_status"], "approved")
        self.assertEqual(event["details"]["residual_risks"], 1)


if __name__ == "__main__":
    unittest.main()