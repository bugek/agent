from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from ai_code_agent.config import AgentConfig
from ai_code_agent.integrations.github_client import GitHubRequestError
from ai_code_agent.integrations.workflow_support import build_branch_name, build_pr_body, create_remote_pr, parse_issue_reference, resolve_issue_input


class WorkflowSupportTest(unittest.TestCase):
    def test_parse_github_issue_reference(self) -> None:
        parsed = parse_issue_reference("https://github.com/octo/repo/issues/42")

        self.assertEqual(parsed["provider"], "github")
        self.assertEqual(parsed["repo"], "octo/repo")
        self.assertEqual(parsed["issue_number"], 42)

    def test_resolve_github_issue_uses_remote_details(self) -> None:
        client = Mock()
        client.get_issue.return_value = {
            "title": "Fix flaky validation",
            "body": "Investigate the retry path",
            "html_url": "https://github.com/octo/repo/issues/42",
        }
        client.list_issue_comments.return_value = [{"author": "alice", "body": "Fails on CI"}]

        description, context = resolve_issue_input(
            "https://github.com/octo/repo/issues/42",
            AgentConfig(github_token="token"),
            github_client=client,
        )

        self.assertIn("GitHub issue: octo/repo#42", description)
        self.assertIn("alice: Fails on CI", description)
        self.assertEqual(context["fetch_status"], "resolved")
        self.assertEqual(context["title"], "Fix flaky validation")

    def test_resolve_azure_work_item_uses_remote_details(self) -> None:
        client = Mock()
        client.get_work_item.return_value = {
            "fields": {
                "System.Title": "Ship CI diagnostics",
                "System.Description": "<p>Need artifact uploads</p>",
            }
        }
        client.list_work_item_comments.return_value = [{"author": "Bob", "text": "Please link the PR"}]

        description, context = resolve_issue_input(
            "https://dev.azure.com/demo/project/_workitems/edit/77",
            AgentConfig(azure_devops_pat="pat", azure_devops_org_url="https://dev.azure.com/demo"),
            azure_client=client,
        )

        self.assertIn("Azure DevOps work item: project#77", description)
        self.assertIn("Ship CI diagnostics", description)
        self.assertIn("Bob: Please link the PR", description)
        self.assertEqual(context["fetch_status"], "resolved")

    def test_build_branch_name_uses_issue_identity(self) -> None:
        branch_name = build_branch_name({"provider": "github", "issue_number": 42, "title": "Fix flaky validation path"}, "fallback")

        self.assertEqual(branch_name, "ai-code-agent/gh-42-fix-flaky-validation-path")

    def test_build_pr_body_preserves_multiline_plan_as_bullets(self) -> None:
        body = build_pr_body(
            {
                "run_id": "run-123",
                "patches": [{"file": "app/pricing/page.tsx"}],
                "retry_count": 0,
                "plan": "- Inspect the existing homepage.\n- Create a pricing route.\n- Run typecheck/build.",
                "review_summary": {"changed_areas": ["app/pricing"]},
            },
            "ai-code-agent/gh-3-create-smartfarm-pricing-page",
        )

        self.assertIn("## Plan", body)
        self.assertIn("- Inspect the existing homepage.", body)
        self.assertIn("- Create a pricing route.", body)
        self.assertIn("- Run typecheck/build.", body)
        self.assertNotIn("homepage. - Create", body)

    def test_build_pr_body_splits_flat_dash_separated_plan_when_needed(self) -> None:
        body = build_pr_body(
            {
                "run_id": "run-123",
                "patches": [{"file": "app/pricing/page.tsx"}],
                "retry_count": 0,
                "plan": "Inspect the existing homepage. - Create a pricing route. - Run typecheck/build.",
                "review_summary": {},
            },
            "ai-code-agent/gh-3-create-smartfarm-pricing-page",
        )

        self.assertIn("- Inspect the existing homepage.", body)
        self.assertIn("- Create a pricing route.", body)
        self.assertIn("- Run typecheck/build.", body)

    def test_create_remote_pr_for_github_posts_issue_comment(self) -> None:
        client = Mock()
        client.find_open_pull_request.return_value = None
        client.create_pull_request.return_value = "https://github.com/octo/repo/pull/9"
        state = {
            "issue_context": {"provider": "github", "repo": "octo/repo", "issue_number": 42, "title": "Fix flaky validation"},
            "run_id": "run-123",
            "patches": [{"file": "x"}],
            "retry_count": 1,
            "plan": "Fix the validation path.",
            "review_summary": {"changed_areas": ["validation"]},
        }

        result = create_remote_pr(
            state,
            AgentConfig(github_token="token", github_base_branch="main"),
            branch_name="ai-code-agent/gh-42-fix-flaky-validation",
            github_client=client,
        )

        self.assertEqual(result["pr_url"], "https://github.com/octo/repo/pull/9")
        self.assertEqual(result["outcome"], "created")
        self.assertIn("GitHub PR", result["message"])
        client.post_comment.assert_called_once()

    def test_create_remote_pr_for_github_returns_existing_pr_when_open(self) -> None:
        client = Mock()
        client.find_open_pull_request.return_value = {"html_url": "https://github.com/octo/repo/pull/4"}
        state = {
            "issue_context": {"provider": "github", "repo": "octo/repo", "issue_number": 42, "title": "Fix flaky validation"},
            "run_id": "run-123",
            "patches": [{"file": "x"}],
            "retry_count": 0,
        }

        result = create_remote_pr(
            state,
            AgentConfig(github_token="token", github_base_branch="main"),
            branch_name="ai-code-agent/gh-42-fix-flaky-validation",
            github_client=client,
        )

        self.assertEqual(result["outcome"], "existing")
        self.assertEqual(result["reason"], "existing_open_pr")
        self.assertEqual(result["pr_url"], "https://github.com/octo/repo/pull/4")
        client.create_pull_request.assert_not_called()

    def test_create_remote_pr_for_github_reports_http_failure(self) -> None:
        client = Mock()
        client.find_open_pull_request.return_value = None
        client.create_pull_request.side_effect = GitHubRequestError(
            422,
            "Validation Failed",
            payload={"errors": [{"message": "branch has no history in common"}]},
        )
        state = {
            "issue_context": {"provider": "github", "repo": "octo/repo", "issue_number": 42, "title": "Fix flaky validation"},
            "run_id": "run-123",
            "patches": [{"file": "x"}],
            "retry_count": 0,
        }

        result = create_remote_pr(
            state,
            AgentConfig(github_token="token", github_base_branch="main"),
            branch_name="ai-code-agent/gh-42-fix-flaky-validation",
            github_client=client,
        )

        self.assertEqual(result["outcome"], "failed")
        self.assertEqual(result["reason"], "github_http_422")
        self.assertIn("branch has no history in common", result["error"])

    def test_create_remote_pr_for_github_skips_when_remote_is_not_github_repo(self) -> None:
        client = Mock()
        state = {
            "issue_context": {"provider": "github", "repo": "octo/repo", "issue_number": 42, "title": "Fix flaky validation"},
            "run_id": "run-123",
            "patches": [{"file": "x"}],
            "retry_count": 0,
        }

        result = create_remote_pr(
            state,
            AgentConfig(github_token="token", github_base_branch="main"),
            branch_name="ai-code-agent/gh-42-fix-flaky-validation",
            remote_url=r"D:\work\next-test-agent-live",
            github_client=client,
        )

        self.assertEqual(result["outcome"], "skipped")
        self.assertEqual(result["reason"], "remote_not_github_repo")
        client.find_open_pull_request.assert_not_called()
        client.create_pull_request.assert_not_called()

    def test_create_remote_pr_for_azure_comments_on_work_item(self) -> None:
        client = Mock()
        client.create_pull_request.return_value = "https://dev.azure.com/demo/project/_git/repo/pullrequest/5"
        state = {
            "issue_context": {
                "provider": "azure_devops",
                "project": "project",
                "repo": "repo",
                "org_url": "https://dev.azure.com/demo",
                "work_item_id": 77,
                "title": "Ship CI diagnostics",
            },
            "run_id": "run-123",
            "patches": [{"file": "x"}],
            "retry_count": 0,
        }

        result = create_remote_pr(
            state,
            AgentConfig(
                azure_devops_pat="pat",
                azure_devops_org_url="https://dev.azure.com/demo",
                azure_devops_project="project",
                azure_devops_repo="repo",
            ),
            branch_name="ai-code-agent/ado-77-ship-ci-diagnostics",
            azure_client=client,
        )

        self.assertEqual(result["pr_url"], "https://dev.azure.com/demo/project/_git/repo/pullrequest/5")
        self.assertEqual(result["outcome"], "created")
        self.assertIn("Azure DevOps PR", result["message"])
        client.post_work_item_comment.assert_called_once()


if __name__ == "__main__":
    unittest.main()