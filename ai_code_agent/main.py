import argparse
import json
import sys
from ai_code_agent.config import AgentConfig
from ai_code_agent.llm.client import LLMClient
from ai_code_agent.orchestrator import build_graph, AgentState

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    arguments = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(description="AI Code Agent - Autonomous Issue Solver")
    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser("run", help="Run the issue workflow")
    run_parser.add_argument("--issue", type=str, required=True, help="URL or ID of the issue to solve")
    run_parser.add_argument("--repo", type=str, required=False, help="Local path to the repository workspace")
    run_parser.add_argument("--json", action="store_true", help="Print the final state as JSON")

    health_parser = subparsers.add_parser("health", help="Check provider connectivity and resolved model")
    health_parser.add_argument("--role", choices=["planner", "coder", "tester", "reviewer"], help="Resolve model for a specific agent role")
    health_parser.add_argument("--json", action="store_true", help="Print the health report as JSON")

    if not arguments or arguments[0].startswith("-"):
        arguments = ["run", *arguments]
    return parser.parse_args(arguments)


def run_health_check(config: AgentConfig, role: str | None, as_json: bool) -> int:
    llm = LLMClient.from_config(config, role=role)
    report = llm.health_check()
    report["role"] = role or "default"

    if as_json:
        print(json.dumps(report, indent=2, ensure_ascii=True))
    else:
        print(f"Provider: {report['provider']}")
        print(f"Role: {report['role']}")
        print(f"Model: {report.get('model') or '<provider default>'}")
        print(f"Enabled: {report['enabled']}")
        print(f"Live call attempted: {report['live_call']}")
        print(f"OK: {report['ok']}")
        print(f"Message: {report['message']}")

    return 0 if report["ok"] else 1


def cli(argv: list[str] | None = None):
    """Main CLI entrypoint for the AI Code Agent."""
    args = parse_args(argv)

    config = AgentConfig()

    if args.command == "health":
        return run_health_check(config, getattr(args, "role", None), getattr(args, "json", False))

    print(f"Starting AI Agent for issue: {args.issue}")

    # Initialize the LLM client
    llm = LLMClient.from_config(config)
    if not llm.enabled:
        print("LLM provider is not configured. Running in fallback mode.")

    graph = build_graph()

    # Define initial state
    initial_state: AgentState = {
        "issue_description": args.issue,
        "workspace_dir": args.repo or config.workspace_dir,
        "plan": None,
        "files_to_edit": [],
        "patches": [],
        "test_results": None,
        "test_passed": False,
        "review_comments": [],
        "review_approved": False,
        "retry_count": 0,
        "error_message": None,
        "created_pr_url": None,
        "execution_log": [],
        "execution_events": [],
    }

    # Run the graph
    print("Executing state machine...")
    final_state = graph.invoke(initial_state)

    if args.json:
        print(json.dumps(final_state, indent=2, ensure_ascii=True))
    else:
        print("Plan:")
        print(final_state.get("plan") or "<none>")
        print()
        print(f"Files to edit: {', '.join(final_state.get('files_to_edit', [])) or '<none>'}")
        print(f"Patches generated: {len(final_state.get('patches', []))}")
        print(f"Tests passed: {final_state.get('test_passed', False)}")
        print(f"Review approved: {final_state.get('review_approved', False)}")
        if final_state.get("execution_events"):
            print(f"Execution events: {len(final_state['execution_events'])}")
        if final_state.get("review_comments"):
            print("Review comments:")
            for comment in final_state["review_comments"]:
                print(f"- {comment}")

    return 0 if final_state.get("test_passed") and final_state.get("review_approved") else 1

if __name__ == "__main__":
    raise SystemExit(cli())
