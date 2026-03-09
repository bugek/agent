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


def _checkpoint_progress(state: AgentState) -> AgentState:
    checkpointed = dict(state)
    checkpointed["execution_metrics"] = build_execution_metrics(checkpointed)
    checkpointed["execution_metrics_path"] = persist_execution_metrics(
        checkpointed.get("workspace_dir"),
        checkpointed.get("run_id"),
        checkpointed["execution_metrics"],
    )
    return checkpointed


def plan_node(state: AgentState) -> dict[str, Any]:
    """Invokes the Planner agent."""
    from ai_code_agent.agents.planner import PlannerAgent

    started = _start_node(state, "plan")
    current_state = _checkpoint_progress(_event_state(state, started))
    config = AgentConfig()
    llm = LLMClient.from_config(config, role="planner")
    agent = PlannerAgent(config, llm)
    result = agent.run(current_state)
    result = _with_run_identity(current_state, result)
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
    else:
        result["retry_count"] = current_state.get("retry_count", 0) + 1
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
    create_pr_result: dict[str, Any] = {
        "outcome": "skipped",
        "reason": "auto_commit_disabled" if not config.auto_commit else "no_patches",
        "provider": (current_state.get("issue_context") or {}).get("provider") if isinstance(current_state.get("issue_context"), dict) else None,
        "branch_name": None,
        "base_branch": None,
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
        create_pr_result.update({"branch_name": branch_name, "provider": issue_context.get("provider")})
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
                    create_pr_result = create_remote_pr(current_state, config, branch_name=branch_name)
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
                "error": create_pr_result.get("error"),
            },
        ),
        "error_message": current_state.get("error_message"),
    }
    result = _with_run_identity(current_state, result)
    return _finalize_result(current_state, result)


def should_continue(state: AgentState) -> str:
    """Routing logic after a review or test."""
    if state["review_approved"] and state["test_passed"]:
        return "create_pr"

    testing_summary = state.get("testing_summary") if isinstance(state.get("testing_summary"), dict) else {}
    if bool(testing_summary.get("stop_retry_after_failure", False)) and not state.get("test_passed", False):
        return "fail"

    if state["retry_count"] >= AgentConfig().max_retries:
        return "fail"

    return "code"


class LocalCompiledGraph:
    """Minimal local graph executor used when LangGraph is unavailable."""

    def __init__(self):
        self.nodes = {
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
        if current == "plan":
            return "code"
        if current == "code":
            return "test"
        if current == "test":
            return "review"
        if current == "review":
            route = should_continue(state)
            return END if route == "fail" else route
        if current == "create_pr":
            return END
        return END

    def invoke(self, state: AgentState) -> AgentState:
        current = "plan"
        current_state = dict(state)
        while current != END:
            delta = self.nodes[current](current_state)
            current_state = self._merge_state(current_state, delta)
            current = self._next_node(current, current_state)
        return current_state

    def stream(self, state: AgentState):
        current = "plan"
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
    workflow.add_node("plan", plan_node)
    workflow.add_node("code", code_node)
    workflow.add_node("test", test_node)
    workflow.add_node("review", review_node)
    workflow.add_node("create_pr", create_pr_node)
    workflow.set_entry_point("plan")
    workflow.add_edge("plan", "code")
    workflow.add_edge("code", "test")
    workflow.add_edge("test", "review")
    workflow.add_conditional_edges(
        "review",
        should_continue,
        {"create_pr": "create_pr", "code": "code", "fail": END},
    )
    workflow.add_edge("create_pr", END)
    return workflow.compile()
