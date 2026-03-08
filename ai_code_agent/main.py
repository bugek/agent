import argparse
import json
import sys
from ai_code_agent.config import AgentConfig
from ai_code_agent.llm.client import LLMClient
from ai_code_agent.metrics import (
    build_execution_metrics_trend,
    generate_run_id,
    list_execution_metrics_artifacts,
    load_execution_metrics_artifact,
    utc_now_iso,
)
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

    diagnose_parser = subparsers.add_parser("diagnose", help="Read persisted execution metrics for the latest or requested run")
    diagnose_parser.add_argument("--repo", type=str, required=False, help="Local path to the repository workspace")
    diagnose_parser.add_argument("--run-id", type=str, required=False, help="Specific run id to inspect")
    diagnose_parser.add_argument("--recent", type=int, default=5, help="Number of recent runs to include when summarizing without --run-id")
    diagnose_parser.add_argument("--status", choices=["approved", "failed", "aborted", "changes_required"], help="Filter recent runs by workflow status")
    diagnose_parser.add_argument("--failure-category", type=str, required=False, help="Filter recent runs by primary failure category")
    diagnose_parser.add_argument("--json", action="store_true", help="Print the metrics artifact as JSON")

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


def run_diagnostics(
    config: AgentConfig,
    repo: str | None,
    run_id: str | None,
    recent: int,
    status: str | None,
    failure_category: str | None,
    as_json: bool,
) -> int:
    workspace_dir = repo or config.workspace_dir
    if run_id:
        metrics, metrics_path = load_execution_metrics_artifact(workspace_dir, run_id)
        if metrics is None or metrics_path is None:
            print(f"No execution metrics artifact found for {run_id} in {workspace_dir}")
            return 1

        if as_json:
            print(json.dumps(metrics, indent=2, ensure_ascii=True))
            return 0

        _print_single_run_diagnostics(metrics, metrics_path)
        return 0

    metrics_entries = _filter_metrics_entries(
        list_execution_metrics_artifacts(workspace_dir, limit=max(recent * 5, recent)),
        status=status,
        failure_category=failure_category,
    )[: max(1, recent)]
    if not metrics_entries:
        filter_parts: list[str] = []
        if status:
            filter_parts.append(f"status={status}")
        if failure_category:
            filter_parts.append(f"failure_category={failure_category}")
        filter_suffix = f" matching {' '.join(filter_parts)}" if filter_parts else ""
        print(f"No execution metrics artifact found for latest run in {workspace_dir}{filter_suffix}")
        return 1

    latest_metrics, latest_path = metrics_entries[0]
    trend = build_execution_metrics_trend(metrics_entries)
    if as_json:
        print(
            json.dumps(
                {
                    "latest": latest_metrics,
                    "latest_path": latest_path,
                    "filters": {"status": status, "failure_category": failure_category},
                    "recent_runs": [
                        {"metrics": metrics, "path": path} for metrics, path in metrics_entries
                    ],
                    "trend": trend,
                },
                indent=2,
                ensure_ascii=True,
            )
        )
        return 0

    _print_single_run_diagnostics(latest_metrics, latest_path)
    if status or failure_category:
        print(
            "Applied filters: "
            + ", ".join(
                part
                for part in [
                    f"status={status}" if status else None,
                    f"failure_category={failure_category}" if failure_category else None,
                ]
                if part
            )
        )
    print(f"Recent runs analyzed: {trend['run_count']}")
    print(f"Comparable runs: {trend['comparable_run_count']}")
    print(f"Approved runs: {trend['approved_count']}")
    print(f"Failed runs: {trend['failed_count']}")
    print(f"Aborted runs: {trend['aborted_count']}")
    print(f"Success rate: {trend['success_rate']:.2f}")
    print(f"Average duration ms: {trend['average_duration_ms']}")
    print(f"Average testing duration ms: {trend['average_testing_duration_ms']}")
    if trend["primary_failure_categories"]:
        print(
            "Primary failure categories: "
            + ", ".join(f"{name}={count}" for name, count in trend["primary_failure_categories"].items())
        )
    if trend.get("failure_category_breakdown"):
        summary_parts: list[str] = []
        for category, breakdown in trend["failure_category_breakdown"].items():
            command_summary = ", ".join(
                f"{item['label']}={item['count']}" for item in breakdown.get("failing_commands", [])
            ) or "none"
            terminal_summary = ", ".join(
                f"{item['node']}={item['count']}" for item in breakdown.get("terminal_nodes", [])
            ) or "none"
            summary_parts.append(
                f"{category}(runs={breakdown.get('run_count')}; commands={command_summary}; nodes={terminal_summary})"
            )
        print("Failure breakdown: " + "; ".join(summary_parts))
    if trend.get("top_terminal_nodes"):
        print(
            "Top terminal nodes: "
            + ", ".join(f"{item['node']}={item['count']}" for item in trend["top_terminal_nodes"])
        )
    if trend.get("top_failing_commands"):
        print(
            "Top failing commands: "
            + ", ".join(f"{item['label']}={item['count']}" for item in trend["top_failing_commands"])
        )
    if trend.get("slowest_commands"):
        print(
            "Top slowest commands: "
            + ", ".join(
                f"{command['label']} avg={command['average_duration_ms']} max={command['max_duration_ms']} count={command['count']}"
                for command in trend["slowest_commands"]
            )
        )
    latest_vs_window = trend.get("latest_vs_previous_window_average") or {}
    if latest_vs_window.get("previous_run_count"):
        print(f"Previous window runs compared: {latest_vs_window['previous_run_count']}")
        previous_success_rate = latest_vs_window.get("previous_success_rate")
        if isinstance(previous_success_rate, float):
            print(f"Previous window success rate: {previous_success_rate:.2f}")
        for label, key in [
            ("Window average duration delta ms", "duration_ms_delta"),
            ("Window average testing duration delta ms", "testing_duration_ms_delta"),
            ("Window average attempt count delta", "attempt_count_delta"),
            ("Window average residual risk delta", "residual_risk_count_delta"),
        ]:
            value = latest_vs_window.get(key)
            if isinstance(value, int):
                direction = latest_vs_window.get(key.replace("_delta", "_direction"))
                suffix = f" ({direction})" if isinstance(direction, str) else ""
                print(f"{label}: {value}{suffix}")
        if latest_vs_window.get("status_changed") is not None:
            print(f"Latest status changed vs previous window: {latest_vs_window['status_changed']}")
    latest_vs_immediate = trend.get("latest_vs_immediately_previous_run") or {}
    if latest_vs_immediate.get("previous_run_id"):
        print(f"Immediately previous run: {latest_vs_immediate['previous_run_id']}")
        for label, key in [
            ("Immediate duration delta ms", "duration_ms_delta"),
            ("Immediate testing duration delta ms", "testing_duration_ms_delta"),
            ("Immediate attempt count delta", "attempt_count_delta"),
            ("Immediate residual risk delta", "residual_risk_count_delta"),
        ]:
            value = latest_vs_immediate.get(key)
            if isinstance(value, int):
                direction = latest_vs_immediate.get(key.replace("_delta", "_direction"))
                suffix = f" ({direction})" if isinstance(direction, str) else ""
                print(f"{label}: {value}{suffix}")
        if latest_vs_immediate.get("status_changed") is not None:
            print(f"Latest status changed vs immediate previous run: {latest_vs_immediate['status_changed']}")
        if latest_vs_immediate.get("primary_failure_category_changed") is not None:
            print(
                "Latest primary failure category changed vs immediate previous run: "
                f"{latest_vs_immediate['primary_failure_category_changed']}"
            )
    print("Recent run list:")
    for metrics, path in metrics_entries:
        workflow = metrics.get("workflow") or {}
        failures = metrics.get("failures") or {}
        print(
            f"- {metrics.get('run_id')}: status={workflow.get('status')}, duration_ms={workflow.get('duration_ms')}, primary_failure={failures.get('primary_category')}, path={path}"
        )
    return 0


def _print_single_run_diagnostics(metrics: dict, metrics_path: str) -> None:
    workflow = metrics.get("workflow") or {}
    failures = metrics.get("failures") or {}
    testing = metrics.get("testing") or {}
    review = metrics.get("review") or {}
    print(f"Run ID: {metrics.get('run_id') or '<unknown>'}")
    print(f"Metrics artifact: {metrics_path}")
    print(f"Workflow status: {workflow.get('status')}")
    print(f"Attempts: {workflow.get('attempt_count')}")
    print(f"Duration ms: {workflow.get('duration_ms')}")
    print(f"Terminal node: {workflow.get('terminal_node')}")
    if failures.get("primary_category"):
        print(f"Primary failure category: {failures.get('primary_category')}")
    failed_commands = testing.get("failed_commands") or []
    if failed_commands:
        print(f"Failed commands: {', '.join(failed_commands)}")
    slowest_command = testing.get("slowest_command") or {}
    if slowest_command.get("label"):
        print(
            f"Slowest command: {slowest_command.get('label')} ({slowest_command.get('duration_ms')} ms)"
        )
    total_duration_ms = testing.get("total_duration_ms")
    if isinstance(total_duration_ms, int):
        print(f"Testing duration ms: {total_duration_ms}")
    print(f"Review status: {review.get('status')}")
    print(f"Residual risks: {review.get('residual_risk_count')}")


def _filter_metrics_entries(
    metrics_entries: list[tuple[dict, str]],
    *,
    status: str | None,
    failure_category: str | None,
) -> list[tuple[dict, str]]:
    normalized_failure_category = failure_category.casefold() if isinstance(failure_category, str) else None
    filtered_entries: list[tuple[dict, str]] = []
    for metrics, path in metrics_entries:
        workflow = metrics.get("workflow") if isinstance(metrics.get("workflow"), dict) else {}
        failures = metrics.get("failures") if isinstance(metrics.get("failures"), dict) else {}
        workflow_status = workflow.get("status")
        primary_category = failures.get("primary_category")
        if status and workflow_status != status:
            continue
        if normalized_failure_category and (
            not isinstance(primary_category, str) or primary_category.casefold() != normalized_failure_category
        ):
            continue
        filtered_entries.append((metrics, path))
    return filtered_entries


def cli(argv: list[str] | None = None):
    """Main CLI entrypoint for the AI Code Agent."""
    args = parse_args(argv)

    config = AgentConfig()

    if args.command == "health":
        return run_health_check(config, getattr(args, "role", None), getattr(args, "json", False))
    if args.command == "diagnose":
        return run_diagnostics(
            config,
            getattr(args, "repo", None),
            getattr(args, "run_id", None),
            getattr(args, "recent", 5),
            getattr(args, "status", None),
            getattr(args, "failure_category", None),
            getattr(args, "json", False),
        )

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
        "run_id": generate_run_id(),
        "workflow_started_at": utc_now_iso(),
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
        execution_metrics = final_state.get("execution_metrics") or {}
        if execution_metrics:
            workflow = execution_metrics.get("workflow") or {}
            failures = execution_metrics.get("failures") or {}
            print(
                f"Workflow metrics: status={workflow.get('status')}, attempts={workflow.get('attempt_count')}, duration_ms={workflow.get('duration_ms')}"
            )
            if final_state.get("execution_metrics_path"):
                print(f"Metrics artifact: {final_state.get('execution_metrics_path')}")
            if failures.get("primary_category"):
                print(f"Primary failure category: {failures.get('primary_category')}")
        if final_state.get("execution_events"):
            print(f"Execution events: {len(final_state['execution_events'])}")
        review_summary = final_state.get("review_summary") or {}
        if review_summary:
            print("Review summary:")
            print(f"- Status: {review_summary.get('status', '<unknown>')}")
            changed_areas = review_summary.get("changed_areas") or []
            if changed_areas:
                print(f"- Changed areas: {', '.join(changed_areas)}")
            validation = review_summary.get("validation") or {}
            if validation.get("passed"):
                print(f"- Validation passed: {', '.join(validation['passed'])}")
            if validation.get("failed"):
                print(f"- Validation failed: {', '.join(validation['failed'])}")
            visual_review = review_summary.get("visual_review") or {}
            if visual_review:
                print(
                    f"- Visual review: screenshot_status={visual_review.get('screenshot_status')}, artifact_count={visual_review.get('artifact_count')}"
                )
                if visual_review.get("missing_states"):
                    print(f"- Missing states: {', '.join(visual_review['missing_states'])}")
                if visual_review.get("missing_responsive_categories"):
                    print(
                        f"- Missing responsive coverage: {', '.join(visual_review['missing_responsive_categories'])}"
                    )
            residual_risks = review_summary.get("residual_risks") or []
            if residual_risks:
                print("- Residual risks:")
                for risk in residual_risks:
                    print(f"  - {risk}")
        if final_state.get("review_comments"):
            print("Review comments:")
            for comment in final_state["review_comments"]:
                print(f"- {comment}")

    return 0 if final_state.get("test_passed") and final_state.get("review_approved") else 1

if __name__ == "__main__":
    raise SystemExit(cli())
