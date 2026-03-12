from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional, TypedDict

try:
    from langgraph.graph import END, StateGraph
except ImportError:  # pragma: no cover - optional dependency
    END = "__end__"
    StateGraph = None

from ai_code_agent.config import AgentConfig
from ai_code_agent.integrations.workflow_support import build_branch_name, create_remote_pr
from ai_code_agent.llm.client import LLMClient
from ai_code_agent.metrics import build_execution_metrics, generate_run_id, persist_execution_metrics, utc_now_iso

class AgentState(TypedDict, total=False):
    """The central state of the orchestrator."""

    issue_description: str
    workspace_dir: str

    # Populated by Planner
    plan: Optional[str]
    files_to_edit: list[str]
    scope_context: dict[str, Any]
    analysis_context: dict[str, Any]

    # Populated by Coder
    patches: list[dict[str, Any]]

    # Populated by Tester
    test_results: Optional[str]
    test_passed: bool
    testing_summary: dict[str, Any]
    visual_review: dict[str, Any]

    # Populated by Reviewer
    review_comments: list[str]
    review_approved: bool
    review_summary: dict[str, Any]
    execution_metrics: dict[str, Any]
    execution_metrics_path: str

    # Task model (populated by planner, updated by orchestrator)
    task_statuses: dict[str, str]
    failed_task_ids: list[str]
    state_validation_failed: bool

    # Internal Orchestrator
    run_id: str
    workflow_started_at: str
    retry_count: int
    error_message: Optional[str]
    created_pr_url: Optional[str]
    execution_log: list[str]
    execution_events: list[dict[str, Any]]
    planning_context: dict[str, Any]
    codegen_summary: dict[str, Any]
    workspace_profile: dict[str, Any]
    file_edit_policy: dict[str, Any]
    issue_context: dict[str, Any]


def _build_runtime() -> tuple[AgentConfig, LLMClient]:
    config = AgentConfig()
    llm = LLMClient.from_config(config)
    return config, llm


def _merge_logs(state: AgentState, message: str) -> list[str]:
    current_logs = list(state.get("execution_log", []))
    current_logs.append(message)
    return current_logs


def _append_event(state: AgentState, node: str, status: str, details: Optional[dict[str, Any]] = None) -> list[dict[str, Any]]:
    return _append_event_with_type(state, node, status, "node_completed", details)


def _append_event_with_type(
    state: AgentState,
    node: str,
    status: str,
    event_type: str,
    details: Optional[dict[str, Any]] = None,
) -> list[dict[str, Any]]:
    events = list(state.get("execution_events", []))
    timestamp = utc_now_iso()
    previous_timestamp = None
    if events:
        previous_timestamp = events[-1].get("timestamp")
    else:
        previous_timestamp = state.get("workflow_started_at")
    attempt = 1 + len(
        [existing for existing in events if existing.get("node") == node and existing.get("event_type") == event_type]
    )
    event: dict[str, Any] = {
        "run_id": state.get("run_id"),
        "sequence": len(events) + 1,
        "timestamp": timestamp,
        "node": node,
        "event_type": event_type,
        "attempt": attempt,
        "status": status,
        "duration_ms": 0 if event_type == "node_started" else _duration_ms(previous_timestamp, timestamp),
    }
    if details:
        event["details"] = details
    events.append(event)
    return events


def _with_run_identity(state: AgentState, result: dict[str, Any]) -> dict[str, Any]:
    result.setdefault("run_id", state.get("run_id") or generate_run_id())
    result.setdefault("workflow_started_at", state.get("workflow_started_at") or utc_now_iso())
    return result


def _event_state(state: AgentState, result: dict[str, Any]) -> AgentState:
    merged = dict(state)
    merged.update(result)
    return merged


def _start_node(state: AgentState, node: str) -> dict[str, Any]:
    seeded = _with_run_identity(state, {})
    started_state = dict(state)
    started_state.update(seeded)
    started_events = _append_event_with_type(started_state, node, "started", "node_started")
    return {
        "run_id": seeded["run_id"],
        "workflow_started_at": seeded["workflow_started_at"],
        "execution_events": started_events,
    }


def _duration_ms(started_at: Any, completed_at: Any) -> int:
    start = _parse_timestamp(started_at)
    end = _parse_timestamp(completed_at)
    if start is None or end is None:
        return 0
    return max(0, int((end - start).total_seconds() * 1000))


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        return datetime.fromisoformat(normalized).astimezone(timezone.utc)
    except ValueError:
        return None


def _finalize_result(state: AgentState, result: dict[str, Any]) -> dict[str, Any]:
    merged_state = dict(state)
    merged_state.update(result)
    result["execution_metrics"] = build_execution_metrics(merged_state)
    result["execution_metrics_path"] = persist_execution_metrics(
        merged_state.get("workspace_dir"),
        merged_state.get("run_id"),
        result["execution_metrics"],
    )
    return result


def _merge_patches(existing: list[dict[str, Any]] | None, new: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for patch in list(existing or []) + list(new or []):
        if not isinstance(patch, dict):
            continue
        file_path = patch.get("file") if isinstance(patch.get("file"), str) else ""
        diff = patch.get("diff") if isinstance(patch.get("diff"), str) else ""
        operation = patch.get("operation") if isinstance(patch.get("operation"), str) else ""
        key = (file_path, diff, operation)
        if key in seen:
            continue
        seen.add(key)
        merged.append(patch)
    return merged


def _update_task_statuses_approved(state: AgentState) -> dict[str, str]:
    """Mark all tasks as completed when review is approved."""
    planning_context = state.get("planning_context") if isinstance(state.get("planning_context"), dict) else {}
    tasks = planning_context.get("tasks") if isinstance(planning_context.get("tasks"), list) else []
    statuses = dict(state.get("task_statuses") or {})
    for task in tasks:
        if isinstance(task, dict) and isinstance(task.get("id"), str):
            statuses[task["id"]] = "completed"
    return statuses


def _update_task_statuses_failed(state: AgentState, failed_task_ids: list[str]) -> dict[str, str]:
    """Mark failed tasks and keep completed ones. Tasks not in failed list with patches are completed."""
    planning_context = state.get("planning_context") if isinstance(state.get("planning_context"), dict) else {}
    tasks = planning_context.get("tasks") if isinstance(planning_context.get("tasks"), list) else []
    failed_set = set(failed_task_ids) if failed_task_ids else set()
    statuses = dict(state.get("task_statuses") or {})
    for task in tasks:
        if not isinstance(task, dict) or not isinstance(task.get("id"), str):
            continue
        task_id = task["id"]
        if task_id in failed_set:
            statuses[task_id] = "failed"
        elif statuses.get(task_id) != "completed":
            statuses[task_id] = "completed" if not failed_set else statuses.get(task_id, "pending")
    return statuses


def _checkpoint_progress(state: AgentState) -> AgentState:
    checkpointed = dict(state)
    checkpointed["execution_metrics"] = build_execution_metrics(checkpointed)
    checkpointed["execution_metrics_path"] = persist_execution_metrics(
        checkpointed.get("workspace_dir"),
        checkpointed.get("run_id"),
        checkpointed["execution_metrics"],
    )
    return checkpointed


def _has_state_validation_failure(state: AgentState) -> bool:
    return bool(state.get("state_validation_failed", False))


def _path_matches_scope(file_path: str, patterns: list[str]) -> bool:
    normalized = file_path.replace("\\", "/")
    for pattern in patterns:
        if not isinstance(pattern, str) or not pattern:
            continue
        normalized_pattern = pattern.replace("\\", "/").rstrip("/")
        if normalized == normalized_pattern or normalized.startswith(normalized_pattern + "/"):
            return True
    return False


def _task_target_patterns(state: AgentState) -> list[str]:
    planning_context = state.get("planning_context") if isinstance(state.get("planning_context"), dict) else {}
    tasks = planning_context.get("tasks") if isinstance(planning_context.get("tasks"), list) else []
    patterns: list[str] = []
    for task in tasks:
        if not isinstance(task, dict):
            continue
        for file_path in task.get("target_files", []):
            if isinstance(file_path, str) and file_path:
                patterns.append(file_path.replace("\\", "/"))
    return patterns


def _validate_state_invariants(state: AgentState, node: str) -> list[str]:
    errors: list[str] = []
    scope_context = state.get("scope_context") if isinstance(state.get("scope_context"), dict) else {}
    planning_context = state.get("planning_context") if isinstance(state.get("planning_context"), dict) else {}
    scope = planning_context.get("scope") if isinstance(planning_context.get("scope"), dict) else scope_context
    in_scope = [item for item in scope.get("in_scope", []) if isinstance(item, str)] if isinstance(scope, dict) else []
    out_of_scope = [item for item in scope.get("out_of_scope", []) if isinstance(item, str)] if isinstance(scope, dict) else []

    if node == "scope":
        overlap = sorted({item for item in in_scope if _path_matches_scope(item, out_of_scope)})
        if overlap:
            errors.append(f"scope overlap detected: {', '.join(overlap[:3])}")

    if node == "plan":
        files_to_edit = [item for item in state.get("files_to_edit", []) if isinstance(item, str)]
        for file_path in files_to_edit:
            if _path_matches_scope(file_path, out_of_scope):
                errors.append(f"plan files_to_edit includes out_of_scope path: {file_path}")
        task_ids: list[str] = []
        tasks = planning_context.get("tasks") if isinstance(planning_context.get("tasks"), list) else []
        for task in tasks:
            if not isinstance(task, dict):
                continue
            task_id = task.get("id")
            if isinstance(task_id, str):
                if task_id in task_ids:
                    errors.append(f"duplicate task id: {task_id}")
                task_ids.append(task_id)
            for file_path in task.get("target_files", []):
                if isinstance(file_path, str) and _path_matches_scope(file_path, out_of_scope):
                    errors.append(f"task target_files includes out_of_scope path: {file_path}")

    if node == "code":
        patches = state.get("patches") if isinstance(state.get("patches"), list) else []
        task_patterns = _task_target_patterns(state)
        for patch in patches:
            if not isinstance(patch, dict):
                continue
            file_path = patch.get("file")
            if not isinstance(file_path, str) or not file_path:
                continue
            if _path_matches_scope(file_path, out_of_scope):
                errors.append(f"patch touches out_of_scope path: {file_path}")
            elif in_scope or task_patterns:
                if not _path_matches_scope(file_path, in_scope) and not _path_matches_scope(file_path, task_patterns):
                    errors.append(f"patch path is outside scope and task targets: {file_path}")

    if node == "review":
        valid_task_ids = {
            task.get("id")
            for task in (planning_context.get("tasks") if isinstance(planning_context.get("tasks"), list) else [])
            if isinstance(task, dict) and isinstance(task.get("id"), str)
        }
        for task_id in state.get("failed_task_ids", []):
            if isinstance(task_id, str) and task_id not in valid_task_ids:
                errors.append(f"failed_task_ids references unknown task: {task_id}")

    return errors


def _enforce_state_invariants(previous_state: AgentState, result: dict[str, Any], node: str) -> dict[str, Any]:
    merged_state = dict(previous_state)
    merged_state.update(result)
    errors = _validate_state_invariants(merged_state, node)
    if errors:
        raise ValueError(f"state invariant violation after {node}: {'; '.join(errors)}")
    return result


def _node_failure_result(current_state: AgentState, node: str, message: str) -> dict[str, Any]:
    result = {
        "error_message": message,
        "state_validation_failed": True,
        "review_approved": False,
        "test_passed": False,
        "execution_log": _merge_logs(current_state, message),
        "execution_events": _append_event(
            current_state,
            node,
            "failed",
            {
                "failure_reason": message,
                "failure_type": "state_invariant_violation",
            },
        ),
    }
    result = _with_run_identity(current_state, result)
    return _finalize_result(current_state, result)


def scope_node(state: AgentState) -> dict[str, Any]:
    """Invokes the Scope agent."""
    from ai_code_agent.agents.planner import ScopeAgent

    started = _start_node(state, "scope")
    current_state = _checkpoint_progress(_event_state(state, started))
    config = AgentConfig()
    llm = LLMClient.from_config(config, role="planner")
    agent = ScopeAgent(config, llm)
    result = agent.run(current_state)
    result = _with_run_identity(current_state, result)
    try:
        result = _enforce_state_invariants(current_state, result, "scope")
    except ValueError as exc:
        return _node_failure_result(current_state, "scope", str(exc))
    scope_context = result.get("scope_context", {}) if isinstance(result.get("scope_context"), dict) else {}
    result["execution_log"] = _merge_logs(current_state, "Scope agent completed.")
    result["execution_events"] = _append_event(
        _event_state(current_state, result),
        "scope",
        "completed",
        {
            "status": scope_context.get("status"),
            "in_scope_count": len(scope_context.get("in_scope", [])),
            "out_of_scope_count": len(scope_context.get("out_of_scope", [])),
            "ambiguity_count": len(scope_context.get("ambiguities", [])),
        },
    )
    return _finalize_result(state, result)


def analysis_node(state: AgentState) -> dict[str, Any]:
    """Invokes the Analysis agent."""
    from ai_code_agent.agents.planner import AnalysisAgent

    started = _start_node(state, "analysis")
    current_state = _checkpoint_progress(_event_state(state, started))
    config = AgentConfig()
    llm = LLMClient.from_config(config, role="planner")
    agent = AnalysisAgent(config, llm)
    result = agent.run(current_state)
    result = _with_run_identity(current_state, result)
    analysis_context = result.get("analysis_context", {}) if isinstance(result.get("analysis_context"), dict) else {}
    result["execution_log"] = _merge_logs(current_state, "Analysis agent completed.")
    result["execution_events"] = _append_event(
        _event_state(current_state, result),
        "analysis",
        "completed",
        {
            "retrieval_strategy": analysis_context.get("retrieval_strategy"),
            "candidate_file_count": len(analysis_context.get("candidate_files", [])),
            "selected_skill_count": len(analysis_context.get("selected_skills", [])),
            "blocked_skill_count": len(analysis_context.get("blocked_skills", [])),
            "graph_seed_files": len(analysis_context.get("graph_seed_files", [])),
        },
    )
    return _finalize_result(state, result)


def plan_node(state: AgentState) -> dict[str, Any]:
    """Invokes the Planner agent."""
    from ai_code_agent.agents.planner import PlanAgent

    started = _start_node(state, "plan")
    current_state = _checkpoint_progress(_event_state(state, started))
    config = AgentConfig()
    llm = LLMClient.from_config(config, role="planner")
    agent = PlanAgent(config, llm)
    result = agent.run(current_state)
    result = _with_run_identity(current_state, result)
    try:
        result = _enforce_state_invariants(current_state, result, "plan")
    except ValueError as exc:
        return _node_failure_result(current_state, "plan", str(exc))
    planning_context = result.get("planning_context", {})
    skill_invocations = planning_context.get("skill_invocations", []) if isinstance(planning_context, dict) else []
    result["execution_log"] = _merge_logs(current_state, "Planner agent completed.")
    result["execution_events"] = _append_event(
        _event_state(current_state, result),
        "plan",
        "completed",
        {
            "files_to_edit": len(result.get("files_to_edit", [])),
            "retrieval_strategy": planning_context.get("retrieval_strategy"),
            "task_count": len(planning_context.get("tasks", [])),
            "scope_in_count": len((planning_context.get("scope") or {}).get("in_scope", [])),
            "scope_out_count": len((planning_context.get("scope") or {}).get("out_of_scope", [])),
            "selected_skill_count": len(planning_context.get("selected_skills", [])),
            "selected_skills": [
                item.get("name")
                for item in planning_context.get("selected_skills", [])
                if isinstance(item, dict) and isinstance(item.get("name"), str)
            ],
            "blocked_skill_count": len(planning_context.get("blocked_skills", [])),
            "skill_invocation_count": len(skill_invocations) if isinstance(skill_invocations, list) else 0,
            "skill_invocations": [
                {
                    "name": item.get("name"),
                    "phase": item.get("phase"),
                    "outcome": item.get("outcome"),
                }
                for item in skill_invocations
                if isinstance(item, dict)
            ],
            "blocked_files_to_edit": len(planning_context.get("blocked_files_to_edit", [])),
            "graph_seed_files": len(planning_context.get("graph_seed_files", [])),
            "edit_intent_count": len(planning_context.get("edit_intent", [])),
        },
    )
    return _finalize_result(state, result)

def code_node(state: AgentState) -> dict[str, Any]:
    """Invokes the Coder agent."""
    from ai_code_agent.agents.coder import CoderAgent

    started = _start_node(state, "code")
    current_state = _checkpoint_progress(_event_state(state, started))
    config = AgentConfig()
    llm = LLMClient.from_config(config, role="coder")
    agent = CoderAgent(config, llm)
    result = agent.run(current_state)
    result["patches"] = _merge_patches(current_state.get("patches", []), result.get("patches", []))
    result = _with_run_identity(current_state, result)
    try:
        result = _enforce_state_invariants(current_state, result, "code")
    except ValueError as exc:
        return _node_failure_result(current_state, "code", str(exc))
    result["execution_log"] = _merge_logs(
        current_state,
        f"Coder agent completed with {len(result.get('patches', []))} patch(es).",
    )
    result["execution_events"] = _append_event(
        _event_state(current_state, result),
        "code",
        "completed",
        {
            "patches": len(result.get("patches", [])),
            "requested_operations": result.get("codegen_summary", {}).get("requested_operations", 0),
            "blocked_operations": len(result.get("codegen_summary", {}).get("blocked_operations", [])),
            "failed_operations": len(result.get("codegen_summary", {}).get("failed_operations", [])),
            "generated_by": result.get("codegen_summary", {}).get("generated_by"),
            "remediation_applied": bool(result.get("codegen_summary", {}).get("remediation_applied")),
            "remediation_focus_count": result.get("codegen_summary", {}).get("remediation_focus_count", 0),
        },
    )
    return _finalize_result(state, result)

def test_node(state: AgentState) -> dict[str, Any]:
    """Invokes the Tester agent."""
    from ai_code_agent.agents.tester import TesterAgent

    started = _start_node(state, "test")
    current_state = _checkpoint_progress(_event_state(state, started))
    config = AgentConfig()
    llm = LLMClient.from_config(config, role="tester")
    agent = TesterAgent(config, llm)
    result = agent.run(current_state)
    result = _with_run_identity(current_state, result)
    result["execution_log"] = _merge_logs(current_state, "Tester agent completed.")
    result["execution_events"] = _append_event(
        _event_state(current_state, result),
        "test",
        "passed" if result.get("test_passed", False) else "failed",
        {
            "test_passed": result.get("test_passed", False),
            "validation_strategy": result.get("testing_summary", {}).get("validation_strategy") or "full",
            "selected_command_count": len(result.get("testing_summary", {}).get("selected_command_labels", [])),
            "skipped_command_count": len(result.get("testing_summary", {}).get("skipped_command_labels", [])),
            "requested_retry_count": len(result.get("testing_summary", {}).get("requested_retry_labels", [])),
            "retry_policy_reason": result.get("testing_summary", {}).get("retry_policy_reason"),
            "retry_policy_history_source": result.get("testing_summary", {}).get("retry_policy_history_source"),
            "retry_policy_confidence": result.get("testing_summary", {}).get("retry_policy_confidence"),
            "stop_retry_after_failure": bool(result.get("testing_summary", {}).get("stop_retry_after_failure", False)),
            "retry_policy_stop_reason": result.get("testing_summary", {}).get("retry_policy_stop_reason"),
        },
    )
    return _finalize_result(state, result)

def review_node(state: AgentState) -> dict[str, Any]:
    """Invokes the Reviewer agent."""
    from ai_code_agent.agents.reviewer import ReviewerAgent

    started = _start_node(state, "review")
    current_state = _checkpoint_progress(_event_state(state, started))
    config = AgentConfig()
    llm = LLMClient.from_config(config, role="reviewer")
    agent = ReviewerAgent(config, llm)
    result = agent.run(current_state)
    result = _with_run_identity(current_state, result)
    approved = result.get("review_approved", False) and current_state.get("test_passed", False)
    if approved:
        result["retry_count"] = current_state.get("retry_count", 0)
        result["task_statuses"] = _update_task_statuses_approved(current_state)
        result["failed_task_ids"] = []
    else:
        result["retry_count"] = current_state.get("retry_count", 0) + 1
        failed_task_ids = result.get("failed_task_ids") or []
        result["task_statuses"] = _update_task_statuses_failed(current_state, failed_task_ids)
        result["failed_task_ids"] = failed_task_ids
    try:
        result = _enforce_state_invariants(current_state, result, "review")
    except ValueError as exc:
        return _node_failure_result(current_state, "review", str(exc))
    result["execution_log"] = _merge_logs(
        current_state,
        f"Reviewer agent completed with status: {'approved' if approved else 'changes required'}.",
    )
    result["execution_events"] = _append_event(
        _event_state(current_state, result),
        "review",
        "approved" if approved else "changes_required",
        {
            "review_approved": result.get("review_approved", False),
            "retry_count": result.get("retry_count", current_state.get("retry_count", 0)),
            "review_status": result.get("review_summary", {}).get("status"),
            "residual_risks": len(result.get("review_summary", {}).get("residual_risks", [])),
            "remediation_required": bool(result.get("review_summary", {}).get("remediation", {}).get("required")),
            "remediation_focus_count": len(result.get("review_summary", {}).get("remediation", {}).get("focus_areas", [])),
            "retry_recovered": approved and result.get("retry_count", current_state.get("retry_count", 0)) > 0,
            "failed_task_ids": result.get("failed_task_ids", []),
        },
    )
    return _finalize_result(state, result)

def create_pr_node(state: AgentState) -> dict[str, Any]:
    """Creates a Pull Request with the changes."""
    started = _start_node(state, "create_pr")
    current_state = _checkpoint_progress(_event_state(state, started))
    config, _ = _build_runtime()
    message = "Workflow completed without creating a PR."
    created_pr_url = None
    branch_name = None
    remote_url = None
    create_pr_result: dict[str, Any] = {
        "outcome": "skipped",
        "reason": "auto_commit_disabled" if not config.auto_commit else "no_patches",
        "provider": (current_state.get("issue_context") or {}).get("provider") if isinstance(current_state.get("issue_context"), dict) else None,
        "branch_name": None,
        "base_branch": None,
        "remote_url": None,
        "pr_url": None,
        "message": message,
        "error": None,
    }

    if config.auto_commit and current_state.get("patches"):
        from ai_code_agent.tools.git_ops import GitOps

        git_ops = GitOps(current_state["workspace_dir"])
        issue_context = current_state.get("issue_context") if isinstance(current_state.get("issue_context"), dict) else {}
        branch_name = build_branch_name(
            issue_context,
            current_state.get("issue_description") or datetime.utcnow().strftime('%Y%m%d%H%M%S'),
        )
        resolved_remote_url = git_ops.remote_url()
        remote_url = resolved_remote_url if isinstance(resolved_remote_url, str) and resolved_remote_url else None
        create_pr_result.update({"branch_name": branch_name, "provider": issue_context.get("provider"), "remote_url": remote_url})
        if not git_ops.is_repository():
            message = "Workflow completed, but skipped git/PR automation because the workspace is not a git repository."
            create_pr_result.update({"outcome": "skipped", "reason": "non_git_workspace"})
        elif git_ops.create_branch(branch_name):
            committed = True
            if git_ops.has_pending_changes():
                committed = git_ops.commit_changes("AI Code Agent automated update")
                if committed:
                    message = f"Committed changes on branch {branch_name}."
                else:
                    create_pr_result.update({"outcome": "failed", "reason": "commit_failed"})
            else:
                message = f"Using existing branch {branch_name} with no new local changes to commit."

            if committed and config.auto_push:
                provider = issue_context.get("provider")
                base_branch = getattr(config, "github_base_branch", "main") if provider == "github" else getattr(config, "azure_devops_target_branch", "main") if provider == "azure_devops" else None
                create_pr_result["base_branch"] = base_branch
                if base_branch and not git_ops.ensure_remote_base_branch(base_branch):
                    message = f"Prepared branch {branch_name}, but failed to bootstrap remote base branch {base_branch}."
                    create_pr_result.update({"outcome": "failed", "reason": "base_branch_bootstrap_failed"})
                elif git_ops.push_branch(branch_name):
                    create_pr_result = create_remote_pr(current_state, config, branch_name=branch_name, remote_url=remote_url)
                    created_pr_url = create_pr_result.get("pr_url") if isinstance(create_pr_result.get("pr_url"), str) else None
                    message = create_pr_result.get("message") or message
                else:
                    message = f"Prepared branch {branch_name}, but automatic push failed."
                    create_pr_result.update({"outcome": "failed", "reason": "push_failed"})
            elif committed and not config.auto_push:
                message = f"Committed changes on branch {branch_name}, but automatic push is disabled."
                create_pr_result.update({"outcome": "skipped", "reason": "auto_push_disabled"})
            elif not committed:
                message = "Workflow completed, but automatic git commit failed."
        else:
            message = "Workflow completed, but automatic branch preparation failed."
            create_pr_result.update({"outcome": "failed", "reason": "branch_preparation_failed"})

    create_pr_result["message"] = message
    create_pr_result["pr_url"] = created_pr_url

    result = {
        "created_pr_url": created_pr_url,
        "create_pr_result": create_pr_result,
        "execution_log": _merge_logs(current_state, message),
        "execution_events": _append_event(
            _event_state(current_state, {"created_pr_url": created_pr_url, "create_pr_result": create_pr_result}),
            "create_pr",
            "completed",
            {
                "created_pr_url": created_pr_url,
                "branch_name": branch_name,
                "issue_provider": (current_state.get("issue_context") or {}).get("provider") if isinstance(current_state.get("issue_context"), dict) else None,
                "outcome": create_pr_result.get("outcome"),
                "reason": create_pr_result.get("reason"),
                "base_branch": create_pr_result.get("base_branch"),
                "remote_url": create_pr_result.get("remote_url"),
                "error": create_pr_result.get("error"),
            },
        ),
        "error_message": current_state.get("error_message"),
    }
    result = _with_run_identity(current_state, result)
    return _finalize_result(current_state, result)


def should_continue(state: AgentState) -> str:
    """Routing logic after a review or test."""
    if _has_state_validation_failure(state):
        return "fail"
    if state["review_approved"] and state["test_passed"]:
        return "create_pr"

    testing_summary = state.get("testing_summary") if isinstance(state.get("testing_summary"), dict) else {}
    if bool(testing_summary.get("stop_retry_after_failure", False)) and not state.get("test_passed", False):
        return "fail"

    if state["retry_count"] >= AgentConfig().max_retries:
        return "fail"

    return "plan"


def should_continue_after_scope(state: AgentState) -> str:
    return "fail" if _has_state_validation_failure(state) else "analysis"


def should_continue_after_analysis(state: AgentState) -> str:
    return "fail" if _has_state_validation_failure(state) else "plan"


def should_continue_after_plan(state: AgentState) -> str:
    return "fail" if _has_state_validation_failure(state) else "code"


def should_continue_after_code(state: AgentState) -> str:
    return "fail" if _has_state_validation_failure(state) else "test"


class LocalCompiledGraph:
    """Minimal local graph executor used when LangGraph is unavailable."""

    def __init__(self):
        self.nodes = {
            "scope": scope_node,
            "analysis": analysis_node,
            "plan": plan_node,
            "code": code_node,
            "test": test_node,
            "review": review_node,
            "create_pr": create_pr_node,
        }

    def _merge_state(self, state: AgentState, delta: dict[str, Any]) -> AgentState:
        merged = dict(state)
        merged.update(delta)
        return merged

    def _next_node(self, current: str, state: AgentState) -> str:
        if current == "scope":
            route = should_continue_after_scope(state)
            return END if route == "fail" else route
        if current == "analysis":
            route = should_continue_after_analysis(state)
            return END if route == "fail" else route
        if current == "plan":
            route = should_continue_after_plan(state)
            return END if route == "fail" else route
        if current == "code":
            route = should_continue_after_code(state)
            return END if route == "fail" else route
        if current == "test":
            return "review"
        if current == "review":
            route = should_continue(state)
            return END if route == "fail" else route
        if current == "create_pr":
            return END
        return END

    def invoke(self, state: AgentState) -> AgentState:
        current = "scope"
        current_state = dict(state)
        while current != END:
            delta = self.nodes[current](current_state)
            current_state = self._merge_state(current_state, delta)
            current = self._next_node(current, current_state)
        return current_state

    def stream(self, state: AgentState):
        current = "scope"
        current_state = dict(state)
        while current != END:
            delta = self.nodes[current](current_state)
            current_state = self._merge_state(current_state, delta)
            yield {current: dict(current_state)}
            current = self._next_node(current, current_state)


def build_graph():
    """Builds and compiles the workflow graph."""
    if StateGraph is None:
        return LocalCompiledGraph()

    workflow = StateGraph(AgentState)
    workflow.add_node("scope", scope_node)
    workflow.add_node("analysis", analysis_node)
    workflow.add_node("plan", plan_node)
    workflow.add_node("code", code_node)
    workflow.add_node("test", test_node)
    workflow.add_node("review", review_node)
    workflow.add_node("create_pr", create_pr_node)
    workflow.set_entry_point("scope")
    workflow.add_conditional_edges("scope", should_continue_after_scope, {"analysis": "analysis", "fail": END})
    workflow.add_conditional_edges("analysis", should_continue_after_analysis, {"plan": "plan", "fail": END})
    workflow.add_conditional_edges("plan", should_continue_after_plan, {"code": "code", "fail": END})
    workflow.add_conditional_edges("code", should_continue_after_code, {"test": "test", "fail": END})
    workflow.add_edge("test", "review")
    workflow.add_conditional_edges(
        "review",
        should_continue,
        {"create_pr": "create_pr", "code": "code", "fail": END},
    )
    workflow.add_edge("create_pr", END)
    return workflow.compile()
