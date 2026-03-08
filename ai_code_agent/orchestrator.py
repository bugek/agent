from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional, TypedDict

try:
    from langgraph.graph import END, StateGraph
except ImportError:  # pragma: no cover - optional dependency
    END = "__end__"
    StateGraph = None

from ai_code_agent.config import AgentConfig
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


def plan_node(state: AgentState) -> dict[str, Any]:
    """Invokes the Planner agent."""
    from ai_code_agent.agents.planner import PlannerAgent

    started = _start_node(state, "plan")
    current_state = _event_state(state, started)
    config = AgentConfig()
    llm = LLMClient.from_config(config, role="planner")
    agent = PlannerAgent(config, llm)
    result = agent.run(current_state)
    result = _with_run_identity(current_state, result)
    planning_context = result.get("planning_context", {})
    result["execution_log"] = _merge_logs(current_state, "Planner agent completed.")
    result["execution_events"] = _append_event(
        _event_state(current_state, result),
        "plan",
        "completed",
        {
            "files_to_edit": len(result.get("files_to_edit", [])),
            "retrieval_strategy": planning_context.get("retrieval_strategy"),
            "blocked_files_to_edit": len(planning_context.get("blocked_files_to_edit", [])),
            "graph_seed_files": len(planning_context.get("graph_seed_files", [])),
        },
    )
    return _finalize_result(state, result)

def code_node(state: AgentState) -> dict[str, Any]:
    """Invokes the Coder agent."""
    from ai_code_agent.agents.coder import CoderAgent

    started = _start_node(state, "code")
    current_state = _event_state(state, started)
    config = AgentConfig()
    llm = LLMClient.from_config(config, role="coder")
    agent = CoderAgent(config, llm)
    result = agent.run(current_state)
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
        },
    )
    return _finalize_result(state, result)

def test_node(state: AgentState) -> dict[str, Any]:
    """Invokes the Tester agent."""
    from ai_code_agent.agents.tester import TesterAgent

    started = _start_node(state, "test")
    current_state = _event_state(state, started)
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
        {"test_passed": result.get("test_passed", False)},
    )
    return _finalize_result(state, result)

def review_node(state: AgentState) -> dict[str, Any]:
    """Invokes the Reviewer agent."""
    from ai_code_agent.agents.reviewer import ReviewerAgent

    started = _start_node(state, "review")
    current_state = _event_state(state, started)
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
        },
    )
    return _finalize_result(state, result)

def create_pr_node(state: AgentState) -> dict[str, Any]:
    """Creates a Pull Request with the changes."""
    started = _start_node(state, "create_pr")
    current_state = _event_state(state, started)
    config, _ = _build_runtime()
    message = "Workflow completed without creating a PR."
    created_pr_url = None

    if config.auto_commit and current_state.get("patches"):
        from ai_code_agent.tools.git_ops import GitOps

        git_ops = GitOps(current_state["workspace_dir"])
        branch_name = f"ai-code-agent/{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
        if git_ops.create_branch(branch_name) and git_ops.commit_changes("AI Code Agent automated update"):
            message = f"Committed changes on branch {branch_name}."
            if config.auto_push and git_ops.push_branch(branch_name):
                message = f"Committed and pushed changes on branch {branch_name}."
        else:
            message = "Workflow completed, but automatic git commit failed."

    result = {
        "created_pr_url": created_pr_url,
        "execution_log": _merge_logs(current_state, message),
        "execution_events": _append_event(_event_state(current_state, {"created_pr_url": created_pr_url}), "create_pr", "completed", {"created_pr_url": created_pr_url}),
        "error_message": current_state.get("error_message"),
    }
    result = _with_run_identity(current_state, result)
    return _finalize_result(current_state, result)


def should_continue(state: AgentState) -> str:
    """Routing logic after a review or test."""
    if state["review_approved"] and state["test_passed"]:
        return "create_pr"

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
