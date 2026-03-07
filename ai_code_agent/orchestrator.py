from __future__ import annotations

from datetime import datetime
from typing import Any, Optional, TypedDict

try:
    from langgraph.graph import END, StateGraph
except ImportError:  # pragma: no cover - optional dependency
    END = "__end__"
    StateGraph = None

from ai_code_agent.config import AgentConfig
from ai_code_agent.llm.client import LLMClient

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

    # Populated by Reviewer
    review_comments: list[str]
    review_approved: bool

    # Internal Orchestrator
    retry_count: int
    error_message: Optional[str]
    created_pr_url: Optional[str]
    execution_log: list[str]
    execution_events: list[dict[str, Any]]
    planning_context: dict[str, Any]
    codegen_summary: dict[str, Any]
    workspace_profile: dict[str, Any]


def _build_runtime() -> tuple[AgentConfig, LLMClient]:
    config = AgentConfig()
    llm = LLMClient.from_config(config)
    return config, llm


def _merge_logs(state: AgentState, message: str) -> list[str]:
    current_logs = list(state.get("execution_log", []))
    current_logs.append(message)
    return current_logs


def _append_event(state: AgentState, node: str, status: str, details: Optional[dict[str, Any]] = None) -> list[dict[str, Any]]:
    events = list(state.get("execution_events", []))
    event: dict[str, Any] = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "node": node,
        "status": status,
    }
    if details:
        event["details"] = details
    events.append(event)
    return events


def plan_node(state: AgentState) -> dict[str, Any]:
    """Invokes the Planner agent."""
    from ai_code_agent.agents.planner import PlannerAgent

    config = AgentConfig()
    llm = LLMClient.from_config(config, role="planner")
    agent = PlannerAgent(config, llm)
    result = agent.run(state)
    result["execution_log"] = _merge_logs(state, "Planner agent completed.")
    result["execution_events"] = _append_event(
        state,
        "plan",
        "completed",
        {"files_to_edit": len(result.get("files_to_edit", []))},
    )
    return result

def code_node(state: AgentState) -> dict[str, Any]:
    """Invokes the Coder agent."""
    from ai_code_agent.agents.coder import CoderAgent

    config = AgentConfig()
    llm = LLMClient.from_config(config, role="coder")
    agent = CoderAgent(config, llm)
    result = agent.run(state)
    result["execution_log"] = _merge_logs(
        state,
        f"Coder agent completed with {len(result.get('patches', []))} patch(es).",
    )
    result["execution_events"] = _append_event(
        state,
        "code",
        "completed",
        {
            "patches": len(result.get("patches", [])),
            "requested_operations": result.get("codegen_summary", {}).get("requested_operations", 0),
        },
    )
    return result

def test_node(state: AgentState) -> dict[str, Any]:
    """Invokes the Tester agent."""
    from ai_code_agent.agents.tester import TesterAgent

    config = AgentConfig()
    llm = LLMClient.from_config(config, role="tester")
    agent = TesterAgent(config, llm)
    result = agent.run(state)
    result["execution_log"] = _merge_logs(state, "Tester agent completed.")
    result["execution_events"] = _append_event(
        state,
        "test",
        "completed",
        {"test_passed": result.get("test_passed", False)},
    )
    return result

def review_node(state: AgentState) -> dict[str, Any]:
    """Invokes the Reviewer agent."""
    from ai_code_agent.agents.reviewer import ReviewerAgent

    config = AgentConfig()
    llm = LLMClient.from_config(config, role="reviewer")
    agent = ReviewerAgent(config, llm)
    result = agent.run(state)
    approved = result.get("review_approved", False) and state.get("test_passed", False)
    if approved:
        result["retry_count"] = state.get("retry_count", 0)
    else:
        result["retry_count"] = state.get("retry_count", 0) + 1
    result["execution_log"] = _merge_logs(
        state,
        f"Reviewer agent completed with status: {'approved' if approved else 'changes required'}.",
    )
    result["execution_events"] = _append_event(
        state,
        "review",
        "completed",
        {
            "review_approved": result.get("review_approved", False),
            "retry_count": result.get("retry_count", state.get("retry_count", 0)),
        },
    )
    return result

def create_pr_node(state: AgentState) -> dict[str, Any]:
    """Creates a Pull Request with the changes."""
    config, _ = _build_runtime()
    message = "Workflow completed without creating a PR."
    created_pr_url = None

    if config.auto_commit and state.get("patches"):
        from ai_code_agent.tools.git_ops import GitOps

        git_ops = GitOps(state["workspace_dir"])
        branch_name = f"ai-code-agent/{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
        if git_ops.create_branch(branch_name) and git_ops.commit_changes("AI Code Agent automated update"):
            message = f"Committed changes on branch {branch_name}."
            if config.auto_push and git_ops.push_branch(branch_name):
                message = f"Committed and pushed changes on branch {branch_name}."
        else:
            message = "Workflow completed, but automatic git commit failed."

    return {
        "created_pr_url": created_pr_url,
        "execution_log": _merge_logs(state, message),
        "execution_events": _append_event(state, "create_pr", "completed", {"created_pr_url": created_pr_url}),
        "error_message": state.get("error_message"),
    }


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
