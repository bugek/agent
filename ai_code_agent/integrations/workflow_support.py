from __future__ import annotations

import re
from html import unescape
from typing import Any
from urllib.parse import urlparse

from ai_code_agent.config import AgentConfig
from ai_code_agent.integrations.azure_devops_client import AzureDevOpsClient
from ai_code_agent.integrations.github_client import GitHubClient


def resolve_issue_input(
    issue_input: str,
    config: AgentConfig,
    *,
    github_client: GitHubClient | None = None,
    azure_client: AzureDevOpsClient | None = None,
) -> tuple[str, dict[str, Any]]:
    reference = parse_issue_reference(issue_input)
    if reference is None:
        return issue_input, {}

    provider = reference.get("provider")
    if provider == "github":
        if not config.github_token:
            return issue_input, {**reference, "fetch_status": "skipped", "fetch_reason": "missing_github_token"}

        client = github_client or GitHubClient(config.github_token)
        repo = str(reference["repo"])
        issue_number = int(reference["issue_number"])
        issue = client.get_issue(repo, issue_number)
        comments = client.list_issue_comments(repo, issue_number)
        context = {
            **reference,
            "fetch_status": "resolved",
            "title": issue.get("title") or f"Issue #{issue_number}",
            "body": issue.get("body") or "",
            "url": issue.get("html_url") or issue_input,
            "comment_count": len(comments),
        }
        return format_issue_prompt(context, comments), context

    if provider == "azure_devops":
        org_url = str(reference.get("org_url") or config.azure_devops_org_url or "")
        if not config.azure_devops_pat or not org_url:
            return issue_input, {**reference, "fetch_status": "skipped", "fetch_reason": "missing_azure_credentials"}

        client = azure_client or AzureDevOpsClient(config.azure_devops_pat, org_url)
        project = str(reference["project"])
        work_item_id = int(reference["work_item_id"])
        work_item = client.get_work_item(project, work_item_id)
        comments = client.list_work_item_comments(project, work_item_id)
        fields = work_item.get("fields") if isinstance(work_item.get("fields"), dict) else {}
        context = {
            **reference,
            "org_url": org_url,
            "fetch_status": "resolved",
            "title": fields.get("System.Title") or f"Work item {work_item_id}",
            "body": normalize_text(fields.get("System.Description") or ""),
            "url": issue_input,
            "comment_count": len(comments),
        }
        return format_issue_prompt(context, comments), context

    return issue_input, reference


def parse_issue_reference(issue_input: str) -> dict[str, Any] | None:
    if not isinstance(issue_input, str) or not issue_input.strip():
        return None

    github_match = re.match(r"https://github\.com/(?P<repo>[^/]+/[^/]+)/issues/(?P<number>\d+)", issue_input.strip())
    if github_match:
        return {
            "provider": "github",
            "repo": github_match.group("repo"),
            "issue_number": int(github_match.group("number")),
            "issue_url": issue_input.strip(),
        }

    azure_match = re.match(
        r"https://dev\.azure\.com/(?P<org>[^/]+)/(?P<project>[^/]+)/_workitems/edit/(?P<id>\d+)",
        issue_input.strip(),
    )
    if azure_match:
        org = azure_match.group("org")
        project = azure_match.group("project")
        return {
            "provider": "azure_devops",
            "org": org,
            "org_url": f"https://dev.azure.com/{org}",
            "project": project,
            "work_item_id": int(azure_match.group("id")),
            "issue_url": issue_input.strip(),
        }

    legacy_match = re.match(
        r"https://(?P<org>[^.]+)\.visualstudio\.com/(?P<project>[^/]+)/_workitems/edit/(?P<id>\d+)",
        issue_input.strip(),
    )
    if legacy_match:
        org = legacy_match.group("org")
        project = legacy_match.group("project")
        return {
            "provider": "azure_devops",
            "org": org,
            "org_url": f"https://{org}.visualstudio.com",
            "project": project,
            "work_item_id": int(legacy_match.group("id")),
            "issue_url": issue_input.strip(),
        }

    return None


def format_issue_prompt(context: dict[str, Any], comments: list[dict[str, Any]]) -> str:
    provider = context.get("provider") or "external"
    title = normalize_text(context.get("title") or "")
    body = normalize_text(context.get("body") or "")
    url = context.get("url") or context.get("issue_url") or ""
    lines = [f"Issue provider: {provider}"]
    if url:
        lines.append(f"Source URL: {url}")
    if provider == "github" and context.get("repo") and context.get("issue_number"):
        lines.append(f"GitHub issue: {context['repo']}#{context['issue_number']}")
    if provider == "azure_devops" and context.get("project") and context.get("work_item_id"):
        lines.append(f"Azure DevOps work item: {context['project']}#{context['work_item_id']}")
    if title:
        lines.append(f"Title: {title}")
    if body:
        lines.append("")
        lines.append("Description:")
        lines.append(body)
    if comments:
        lines.append("")
        lines.append("Recent discussion:")
        for comment in comments[:3]:
            author = normalize_text(comment.get("author") or comment.get("user") or "unknown")
            content = normalize_text(comment.get("body") or comment.get("text") or "")
            if not content:
                continue
            lines.append(f"- {author}: {content}")
    return "\n".join(lines).strip()


def build_branch_name(issue_context: dict[str, Any], issue_description: str) -> str:
    parts = ["ai-code-agent"]
    provider = issue_context.get("provider")
    if provider == "github" and issue_context.get("issue_number"):
        parts.append(f"gh-{issue_context['issue_number']}")
    elif provider == "azure_devops" and issue_context.get("work_item_id"):
        parts.append(f"ado-{issue_context['work_item_id']}")

    title = issue_context.get("title") or issue_description
    slug = slugify(str(title), max_length=48)
    if slug:
        parts.append(slug)
    return "/".join([parts[0], "-".join(parts[1:]) if len(parts) > 1 else slug or "update"])


def create_remote_pr(
    state: dict[str, Any],
    config: AgentConfig,
    *,
    branch_name: str,
    github_client: GitHubClient | None = None,
    azure_client: AzureDevOpsClient | None = None,
) -> tuple[str | None, str]:
    issue_context = state.get("issue_context") if isinstance(state.get("issue_context"), dict) else {}
    provider = issue_context.get("provider")
    title = build_pr_title(state)
    body = build_pr_body(state, branch_name)

    if provider == "github" and config.github_token:
        repo = issue_context.get("repo") or config.github_repo
        if not repo:
            return None, "Pushed branch, but skipped GitHub PR creation because no repo was configured."
        client = github_client or GitHubClient(config.github_token)
        pr_url = client.create_pull_request(str(repo), branch_name, title, body, base_branch=config.github_base_branch)
        issue_number = issue_context.get("issue_number")
        if pr_url and issue_number:
            client.post_comment(str(repo), int(issue_number), f"AI Code Agent opened PR: {pr_url}")
        return pr_url or None, f"Committed, pushed, and opened GitHub PR: {pr_url}" if pr_url else "Pushed branch, but GitHub PR creation returned no URL."

    if provider == "azure_devops" and config.azure_devops_pat:
        project = issue_context.get("project") or config.azure_devops_project
        repo = issue_context.get("repo") or config.azure_devops_repo
        org_url = issue_context.get("org_url") or config.azure_devops_org_url
        if not project or not repo or not org_url:
            return None, "Pushed branch, but skipped Azure DevOps PR creation because project, repo, or org URL was missing."
        client = azure_client or AzureDevOpsClient(config.azure_devops_pat, str(org_url))
        target_ref = f"refs/heads/{config.azure_devops_target_branch}"
        source_ref = branch_name if branch_name.startswith("refs/heads/") else f"refs/heads/{branch_name}"
        pr_url = client.create_pull_request(str(project), str(repo), source_ref, target_ref, title, body)
        work_item_id = issue_context.get("work_item_id")
        if pr_url and work_item_id:
            client.post_work_item_comment(str(project), int(work_item_id), f"AI Code Agent opened PR: {pr_url}")
        return pr_url or None, f"Committed, pushed, and opened Azure DevOps PR: {pr_url}" if pr_url else "Pushed branch, but Azure DevOps PR creation returned no URL."

    return None, f"Committed and pushed changes on branch {branch_name}."


def build_pr_title(state: dict[str, Any]) -> str:
    issue_context = state.get("issue_context") if isinstance(state.get("issue_context"), dict) else {}
    title = normalize_text(issue_context.get("title") or state.get("issue_description") or "Automated update")
    return f"AI Code Agent: {title[:120]}"


def build_pr_body(state: dict[str, Any], branch_name: str) -> str:
    review_summary = state.get("review_summary") if isinstance(state.get("review_summary"), dict) else {}
    remediation = review_summary.get("remediation") if isinstance(review_summary.get("remediation"), dict) else {}
    lines = [
        "## Summary",
        f"- Run ID: {state.get('run_id') or '<unknown>'}",
        f"- Branch: {branch_name}",
        f"- Patches generated: {len(state.get('patches', []))}",
        f"- Retry count: {state.get('retry_count', 0)}",
    ]
    plan = normalize_text(state.get("plan") or "")
    if plan:
        lines.extend(["", "## Plan", plan[:1200]])
    changed_areas = review_summary.get("changed_areas") if isinstance(review_summary.get("changed_areas"), list) else []
    if changed_areas:
        lines.extend(["", "## Changed Areas"])
        lines.extend([f"- {item}" for item in changed_areas[:10]])
    if remediation.get("required"):
        lines.extend(["", "## Residual Remediation", "- Reviewer still flagged follow-up work."])
    return "\n".join(lines)


def normalize_text(value: str) -> str:
    text = unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def slugify(value: str, *, max_length: int) -> str:
    lowered = normalize_text(value).lower()
    slug = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
    return slug[:max_length].strip("-")