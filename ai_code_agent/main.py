import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from ai_code_agent.config import AgentConfig
from ai_code_agent.integrations.workflow_support import resolve_issue_input
from ai_code_agent.llm.client import LLMClient
from ai_code_agent.metrics import (
    build_diagnostics_summary,
    build_execution_metrics_trend,
    generate_run_id,
    list_execution_metrics_artifacts,
    load_fresh_diagnostics_summary_artifact,
    load_execution_metrics_artifact,
    normalize_execution_metrics_artifacts,
    persist_diagnostics_summary,
    utc_now_iso,
)
from ai_code_agent.orchestrator import build_graph, AgentState
from ai_code_agent.tools.sandbox import SandboxRunner

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
    diagnose_parser.add_argument("--format", choices=["text", "json", "ndjson", "rows"], default="text", help="Output format for diagnostics export")
    diagnose_parser.add_argument("--json", action="store_true", help="Print the metrics artifact as JSON")

    normalize_parser = subparsers.add_parser("normalize-metrics", help="Rewrite persisted execution metrics artifacts to the latest normalized semantics")
    normalize_parser.add_argument("--repo", type=str, required=False, help="Local path to the repository workspace")
    normalize_parser.add_argument("--run-id", type=str, required=False, help="Specific run id to normalize")
    normalize_parser.add_argument("--json", action="store_true", help="Print the normalization report as JSON")

    monitor_parser = subparsers.add_parser("monitor", help="Launch the monitor backend API and frontend service together")
    monitor_parser.add_argument("--repo", type=str, required=False, help="Workspace path to prefill in the monitor UI")
    monitor_parser.add_argument("--recent", type=int, default=5, help="Recent run count to prefill in the monitor UI")
    monitor_parser.add_argument("--backend-host", type=str, default="127.0.0.1", help="Host for the monitor backend API")
    monitor_parser.add_argument("--backend-port", type=int, default=8000, help="Port for the monitor backend API")
    monitor_parser.add_argument("--frontend-host", type=str, default="127.0.0.1", help="Host for the monitor frontend service")
    monitor_parser.add_argument("--frontend-port", type=int, default=4173, help="Port for the monitor frontend service")
    monitor_parser.add_argument("--detach", action="store_true", help="Start the monitor services and exit without waiting")

    if not arguments or arguments[0].startswith("-"):
        arguments = ["run", *arguments]
    return parser.parse_args(arguments)


def run_health_check(config: AgentConfig, role: str | None, as_json: bool) -> int:
    llm = LLMClient.from_config(config, role=role)
    report = llm.health_check()
    report["role"] = role or "default"
    sandbox = SandboxRunner(
        config.docker_image,
        workspace_dir=config.workspace_dir,
        mode=config.sandbox_mode,
        compose_file=config.sandbox_compose_file,
        compose_service=config.sandbox_compose_service,
        compose_project_name=config.sandbox_compose_project_name,
        compose_ready_services=config.sandbox_compose_ready_services,
        compose_readiness_timeout_seconds=config.sandbox_compose_readiness_timeout_seconds,
    ).probe()
    report["sandbox"] = sandbox

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
        print(f"Sandbox requested mode: {sandbox['requested_mode']}")
        print(f"Sandbox resolved mode: {sandbox['resolved_mode']}")
        print(f"Sandbox image: {sandbox['image']}")
        print(f"Sandbox degraded: {sandbox['degraded']}")
        if sandbox.get("fallback_reason"):
            print(f"Sandbox fallback reason: {sandbox['fallback_reason']}")
        if sandbox.get("recommendation"):
            print(f"Sandbox recommendation: {sandbox['recommendation']}")

    return 0 if report["ok"] else 1


def run_diagnostics(
    config: AgentConfig,
    repo: str | None,
    run_id: str | None,
    recent: int,
    status: str | None,
    failure_category: str | None,
    output_format: str,
) -> int:
    workspace_dir = repo or config.workspace_dir
    if run_id:
        metrics, metrics_path = load_execution_metrics_artifact(workspace_dir, run_id)
        if metrics is None or metrics_path is None:
            print(f"No execution metrics artifact found for {run_id} in {workspace_dir}")
            return 1

        if output_format != "text":
            _print_export_output(
                latest_metrics=metrics,
                latest_path=metrics_path,
                metrics_entries=[(metrics, metrics_path)],
                trend=build_execution_metrics_trend([(metrics, metrics_path)]),
                output_format=output_format,
                filters={"status": status, "failure_category": failure_category},
                single_run=True,
            )
            return 0

        _print_single_run_diagnostics(metrics, metrics_path)
        return 0

    if output_format in {"text", "json", "rows", "ndjson"}:
        cached_summary, cached_summary_path = load_fresh_diagnostics_summary_artifact(
            workspace_dir,
            recent=recent,
            status=status,
            failure_category=failure_category,
        )
        if cached_summary is not None and cached_summary_path is not None:
            if output_format == "text":
                latest_run_id = cached_summary.get("latest_run_id") if isinstance(cached_summary.get("latest_run_id"), str) else None
                latest_metrics, latest_path = load_execution_metrics_artifact(workspace_dir, latest_run_id)
                if latest_metrics is not None and latest_path is not None:
                    _print_summary_text_output(
                        latest_metrics=latest_metrics,
                        latest_path=latest_path,
                        summary=cached_summary,
                        summary_path=cached_summary_path,
                        status=status,
                        failure_category=failure_category,
                    )
                    return 0
            elif output_format == "json":
                metrics_entries = _load_metrics_entries_from_summary(workspace_dir, cached_summary)
                if metrics_entries:
                    latest_metrics, latest_path = metrics_entries[0]
                    _print_export_output(
                        latest_metrics=latest_metrics,
                        latest_path=latest_path,
                        metrics_entries=metrics_entries,
                        trend=cached_summary.get("trend") if isinstance(cached_summary.get("trend"), dict) else {},
                        output_format=output_format,
                        filters={"status": status, "failure_category": failure_category},
                        summary_path=cached_summary_path,
                        single_run=False,
                    )
                    return 0
            else:
                _print_summary_export_output(cached_summary, output_format)
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
    summary = build_diagnostics_summary(
        metrics_entries,
        trend,
        recent=recent,
        filters={"status": status, "failure_category": failure_category},
    )
    summary_path = persist_diagnostics_summary(
        workspace_dir,
        summary,
        recent=recent,
        status=status,
        failure_category=failure_category,
    )
    if output_format != "text":
        _print_export_output(
            latest_metrics=latest_metrics,
            latest_path=latest_path,
            metrics_entries=metrics_entries,
            trend=trend,
            output_format=output_format,
            filters={"status": status, "failure_category": failure_category},
            summary_path=summary_path,
            single_run=False,
        )
        return 0

    _print_summary_text_output(
        latest_metrics=latest_metrics,
        latest_path=latest_path,
        summary=summary,
        summary_path=summary_path,
        status=status,
        failure_category=failure_category,
    )
    return 0


def run_normalize_metrics(config: AgentConfig, repo: str | None, run_id: str | None, as_json: bool) -> int:
    workspace_dir = repo or config.workspace_dir
    report = normalize_execution_metrics_artifacts(workspace_dir, run_id)
    report.update({"workspace_dir": workspace_dir, "run_id": run_id})
    if as_json:
        print(json.dumps(report, indent=2, ensure_ascii=True))
    else:
        print(f"Workspace: {workspace_dir}")
        if run_id:
            print(f"Run ID: {run_id}")
        print(f"Artifacts checked: {report['checked']}")
        print(f"Artifacts updated: {report['updated']}")
        print(f"Diagnostics summaries removed: {report['diagnostics_removed']}")
    return 0


def _monitor_frontend_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "monitor_frontend"


def _npm_executable() -> str:
    return "npm.cmd" if os.name == "nt" else "npm"


def run_monitor_services(
    config: AgentConfig,
    repo: str | None,
    recent: int,
    backend_host: str,
    backend_port: int,
    frontend_host: str,
    frontend_port: int,
    detach: bool,
) -> int:
    frontend_dir = _monitor_frontend_dir()
    if not frontend_dir.exists():
        print(f"Monitor frontend service not found at {frontend_dir}")
        return 1

    workspace_dir = repo or config.workspace_dir
    backend_url = f"http://{backend_host}:{backend_port}"
    frontend_url = f"http://{frontend_host}:{frontend_port}"

    backend_env = os.environ.copy()
    backend_env["MONITOR_FRONTEND_URL"] = frontend_url

    frontend_env = os.environ.copy()
    frontend_env["VITE_MONITOR_API_BASE"] = backend_url

    backend_cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "ai_code_agent.webhook:app",
        "--host",
        backend_host,
        "--port",
        str(backend_port),
    ]
    frontend_cmd = [
        _npm_executable(),
        "run",
        "dev",
        "--",
        "--host",
        frontend_host,
        "--port",
        str(frontend_port),
    ]

    backend_process = subprocess.Popen(backend_cmd, env=backend_env)
    frontend_process = subprocess.Popen(frontend_cmd, cwd=str(frontend_dir), env=frontend_env)

    print(f"Monitor backend API: {backend_url}/api/monitor")
    print(f"Monitor frontend UI: {frontend_url}/?repo={workspace_dir}&recent={recent}")
    print(f"Monitor redirect route: {backend_url}/monitor?repo={workspace_dir}&recent={recent}")

    if detach:
        print(f"Backend PID: {backend_process.pid}")
        print(f"Frontend PID: {frontend_process.pid}")
        return 0

    try:
        while True:
            backend_returncode = backend_process.poll()
            frontend_returncode = frontend_process.poll()
            if backend_returncode is not None or frontend_returncode is not None:
                if backend_returncode not in (None, 0):
                    print(f"Monitor backend exited with code {backend_returncode}")
                if frontend_returncode not in (None, 0):
                    print(f"Monitor frontend exited with code {frontend_returncode}")
                return 0 if backend_returncode in (None, 0) and frontend_returncode in (None, 0) else 1
            time.sleep(1)
    except KeyboardInterrupt:
        print("Stopping monitor services...")
        for process in (backend_process, frontend_process):
            if process.poll() is None:
                process.terminate()
        for process in (backend_process, frontend_process):
            if process.poll() is None:
                process.wait(timeout=10)
        return 0


def _print_summary_text_output(
    *,
    latest_metrics: dict,
    latest_path: str,
    summary: dict,
    summary_path: str | None,
    status: str | None,
    failure_category: str | None,
) -> None:
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
    if summary_path:
        print(f"Diagnostics summary artifact: {summary_path}")
    trend = summary.get("trend") if isinstance(summary.get("trend"), dict) else {}
    print(f"Recent runs analyzed: {trend['run_count']}")
    print(f"Comparable runs: {trend['comparable_run_count']}")
    print(f"Approved runs: {trend['approved_count']}")
    print(f"Failed runs: {trend['failed_count']}")
    print(f"Aborted runs: {trend['aborted_count']}")
    print(f"Success rate: {trend['success_rate']:.2f}")
    print(f"Average duration ms: {trend['average_duration_ms']}")
    print(f"Average testing duration ms: {trend['average_testing_duration_ms']}")
    if trend.get("validation_strategies"):
        print(
            "Validation strategies: "
            + ", ".join(f"{name}={count}" for name, count in trend["validation_strategies"].items())
        )
    if trend.get("create_pr_outcomes"):
        print(
            "Create PR outcomes: "
            + ", ".join(f"{name}={count}" for name, count in trend["create_pr_outcomes"].items())
        )
    effectiveness = trend.get("effectiveness") if isinstance(trend.get("effectiveness"), dict) else {}
    if effectiveness:
        print(
            f"Retry recovery: {effectiveness.get('retry_recovered_runs', 0)}/{effectiveness.get('retry_runs', 0)} ({effectiveness.get('retry_recovery_rate', 0.0):.2f})"
        )
        print(
            f"Remediation recovery: {effectiveness.get('remediation_recovered_runs', 0)}/{effectiveness.get('remediation_runs', 0)} ({effectiveness.get('remediation_recovery_rate', 0.0):.2f})"
        )
        print(
            f"Edit intent recovery: {effectiveness.get('edit_intent_recovered_runs', 0)}/{effectiveness.get('edit_intent_runs', 0)} ({effectiveness.get('edit_intent_recovery_rate', 0.0):.2f})"
        )
        print(
            "Targeted retry savings: "
            f"runs={effectiveness.get('targeted_retry_runs', 0)}, "
            f"approved={effectiveness.get('targeted_retry_approved_runs', 0)}, "
            f"success_rate={effectiveness.get('targeted_retry_success_rate', 0.0):.2f}, "
            f"skipped_commands={effectiveness.get('targeted_retry_total_skipped_commands', 0)}, "
            f"avg_skipped={effectiveness.get('targeted_retry_average_skipped_commands', 0)}, "
            f"avg_reduction_rate={effectiveness.get('targeted_retry_average_reduction_rate', 0.0):.2f}"
        )
    strategy_comparison = trend.get("strategy_comparison") if isinstance(trend.get("strategy_comparison"), dict) else {}
    if strategy_comparison:
        full_summary = strategy_comparison.get("full") if isinstance(strategy_comparison.get("full"), dict) else {}
        targeted_summary = strategy_comparison.get("targeted_retry") if isinstance(strategy_comparison.get("targeted_retry"), dict) else {}
        delta_summary = strategy_comparison.get("targeted_retry_vs_full") if isinstance(strategy_comparison.get("targeted_retry_vs_full"), dict) else {}
        print(
            "Strategy comparison: "
            f"full(runs={full_summary.get('run_count', 0)}, success_rate={full_summary.get('success_rate', 0.0):.2f}, avg_testing_ms={full_summary.get('average_testing_duration_ms', 0)}) ; "
            f"targeted_retry(runs={targeted_summary.get('run_count', 0)}, success_rate={targeted_summary.get('success_rate', 0.0):.2f}, avg_testing_ms={targeted_summary.get('average_testing_duration_ms', 0)}) ; "
            f"delta(success_rate={delta_summary.get('success_rate_delta', 0.0):.2f}, testing_ms={delta_summary.get('testing_duration_ms_delta', 0)}, reduction_rate={delta_summary.get('average_command_reduction_rate_delta', 0.0):.2f})"
        )
    if trend["primary_failure_categories"]:
        print(
            "Primary failure categories: "
            + ", ".join(f"{name}={count}" for name, count in trend["primary_failure_categories"].items())
        )
    if trend.get("primary_failure_subcategories"):
        print(
            "Failure subcategories: "
            + ", ".join(f"{name}={count}" for name, count in trend["primary_failure_subcategories"].items())
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
    if trend.get("failure_subcategory_breakdown"):
        summary_parts = []
        for subcategory, breakdown in trend["failure_subcategory_breakdown"].items():
            category_summary = ", ".join(
                f"{item['category']}={item['count']}" for item in breakdown.get("primary_categories", [])
            ) or "none"
            summary_parts.append(
                f"{subcategory}(runs={breakdown.get('run_count')}; categories={category_summary})"
            )
        print("Failure subcategory breakdown: " + "; ".join(summary_parts))
    if trend.get("retry_policy_stop_reasons"):
        print(
            "Retry stop reasons: "
            + ", ".join(f"{name}={count}" for name, count in trend["retry_policy_stop_reasons"].items())
        )
    if trend.get("sandbox_fallback_reasons"):
        print(
            "Sandbox fallback reasons: "
            + ", ".join(f"{name}={count}" for name, count in trend["sandbox_fallback_reasons"].items())
        )
    dashboard = trend.get("dashboard") if isinstance(trend.get("dashboard"), dict) else {}
    if dashboard:
        print(
            "Dashboard summary: "
            f"latest_failure={dashboard.get('latest_failure_category') or 'none'}"
            f"/{dashboard.get('latest_failure_subcategory') or 'none'}, "
            f"dominant_failure={dashboard.get('dominant_failure_category') or 'none'}"
            f"/{dashboard.get('dominant_failure_subcategory') or 'none'}, "
            f"retry_stop_rate={dashboard.get('retry_stop_rate', 0.0):.2f}, "
            f"sandbox_fallback_rate={dashboard.get('sandbox_fallback_rate', 0.0):.2f}"
        )
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
    for row in summary.get("rows", []):
        if not isinstance(row, dict):
            continue
        print(
            f"- {row.get('run_id')}: status={row.get('status')}, duration_ms={row.get('duration_ms')}, primary_failure={row.get('primary_failure')}, path={row.get('path')}"
        )


def _print_summary_export_output(summary: dict, output_format: str) -> None:
    rows = summary.get("rows") if isinstance(summary.get("rows"), list) else []
    if output_format == "ndjson":
        for row in rows:
            print(json.dumps(row, ensure_ascii=True))
        return
    if output_format == "rows":
        print("run_id\tstatus\tprimary_failure\tfailure_subcategory\tvalidation_strategy\tcreate_pr_outcome\tcreate_pr_reason\tretry_recovered\tskipped_command_count\tcommand_reduction_rate\tduration_ms\ttesting_duration_ms\tterminal_node\tpath")
        for row in rows:
            if not isinstance(row, dict):
                continue
            print(
                "\t".join(
                    str(row.get(key, ""))
                    for key in [
                        "run_id",
                        "status",
                        "primary_failure",
                        "failure_subcategory",
                        "validation_strategy",
                        "create_pr_outcome",
                        "create_pr_reason",
                        "retry_recovered",
                        "skipped_command_count",
                        "command_reduction_rate",
                        "duration_ms",
                        "testing_duration_ms",
                        "terminal_node",
                        "path",
                    ]
                )
            )
        return
    raise ValueError(f"Unsupported summary export format: {output_format}")


def _load_metrics_entries_from_summary(workspace_dir: str | None, summary: dict) -> list[tuple[dict, str]]:
    rows = summary.get("rows") if isinstance(summary.get("rows"), list) else []
    metrics_entries: list[tuple[dict, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            return []
        run_id = row.get("run_id")
        if not isinstance(run_id, str) or not run_id:
            return []
        metrics, path = load_execution_metrics_artifact(workspace_dir, run_id)
        if metrics is None or path is None:
            return []
        metrics_entries.append((metrics, path))
    return metrics_entries


def _print_single_run_diagnostics(metrics: dict, metrics_path: str) -> None:
    workflow = metrics.get("workflow") or {}
    failures = _display_failure_info(metrics)
    testing = metrics.get("testing") or {}
    review = metrics.get("review") or {}
    create_pr = metrics.get("create_pr") or {}
    effectiveness = metrics.get("effectiveness") or {}
    print(f"Run ID: {metrics.get('run_id') or '<unknown>'}")
    print(f"Metrics artifact: {metrics_path}")
    print(f"Workflow status: {workflow.get('status')}")
    print(f"Attempts: {workflow.get('attempt_count')}")
    print(f"Duration ms: {workflow.get('duration_ms')}")
    print(f"Terminal node: {workflow.get('terminal_node')}")
    if failures.get("primary_category"):
        print(f"Primary failure category: {failures.get('primary_category')}")
    if failures.get("subcategory"):
        print(f"Failure subcategory: {failures.get('subcategory')}")
    if isinstance(testing.get("validation_strategy"), str):
        print(f"Validation strategy: {testing.get('validation_strategy')}")
    if effectiveness.get("retry_attempted"):
        print(f"Retry recovered: {effectiveness.get('retry_recovered')}")
    if effectiveness.get("remediation_applied"):
        print(f"Remediation applied: {effectiveness.get('remediation_applied')}")
    failed_commands = testing.get("failed_commands") or []
    if failed_commands:
        print(f"Failed commands: {', '.join(failed_commands)}")
    requested_retry_labels = testing.get("requested_retry_labels") or []
    if requested_retry_labels:
        print(f"Requested retry labels: {', '.join(requested_retry_labels)}")
    skipped_command_count = testing.get("skipped_command_count")
    if isinstance(skipped_command_count, int) and skipped_command_count > 0:
        print(f"Skipped commands on this pass: {skipped_command_count}")
    command_reduction_rate = testing.get("command_reduction_rate")
    if isinstance(command_reduction_rate, (int, float)) and command_reduction_rate > 0:
        print(f"Command reduction rate: {command_reduction_rate:.2f}")
    slowest_command = testing.get("slowest_command") or {}
    if slowest_command.get("label"):
        print(
            f"Slowest command: {slowest_command.get('label')} ({slowest_command.get('duration_ms')} ms)"
        )
    total_duration_ms = testing.get("total_duration_ms")
    if isinstance(total_duration_ms, int):
        print(f"Testing duration ms: {total_duration_ms}")
    if create_pr.get("outcome"):
        print(f"Create PR outcome: {create_pr.get('outcome')}")
    if create_pr.get("reason"):
        print(f"Create PR reason: {create_pr.get('reason')}")
    if create_pr.get("pr_url"):
        print(f"PR URL: {create_pr.get('pr_url')}")
    elif create_pr.get("error"):
        print(f"Create PR error: {create_pr.get('error')}")
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


def _print_export_output(
    *,
    latest_metrics: dict,
    latest_path: str,
    metrics_entries: list[tuple[dict, str]],
    trend: dict,
    output_format: str,
    filters: dict[str, str | None],
    summary_path: str | None = None,
    single_run: bool,
) -> None:
    payload = {
        "latest": latest_metrics,
        "latest_path": latest_path,
        "summary_path": summary_path,
        "filters": filters,
        "recent_runs": [
            {"metrics": metrics, "path": path} for metrics, path in metrics_entries
        ],
        "trend": trend,
    }
    if output_format == "json":
        if single_run:
            print(json.dumps(latest_metrics, indent=2, ensure_ascii=True))
            return
        print(json.dumps(payload, indent=2, ensure_ascii=True))
        return
    if output_format == "ndjson":
        for metrics, path in metrics_entries:
            print(json.dumps(_diagnostics_row(metrics, path), ensure_ascii=True))
        return
    if output_format == "rows":
        print("run_id\tstatus\tprimary_failure\tfailure_subcategory\tvalidation_strategy\tcreate_pr_outcome\tcreate_pr_reason\tretry_recovered\tskipped_command_count\tcommand_reduction_rate\tduration_ms\ttesting_duration_ms\tterminal_node\tpath")
        for metrics, path in metrics_entries:
            row = _diagnostics_row(metrics, path)
            print(
                "\t".join(
                    str(row[key])
                    for key in [
                        "run_id",
                        "status",
                        "primary_failure",
                        "failure_subcategory",
                        "validation_strategy",
                        "create_pr_outcome",
                        "create_pr_reason",
                        "retry_recovered",
                        "skipped_command_count",
                        "command_reduction_rate",
                        "duration_ms",
                        "testing_duration_ms",
                        "terminal_node",
                        "path",
                    ]
                )
            )
        return
    raise ValueError(f"Unsupported diagnostics output format: {output_format}")


def _diagnostics_row(metrics: dict, path: str) -> dict[str, object]:
    workflow = metrics.get("workflow") if isinstance(metrics.get("workflow"), dict) else {}
    failures = _display_failure_info(metrics)
    testing = metrics.get("testing") if isinstance(metrics.get("testing"), dict) else {}
    create_pr = metrics.get("create_pr") if isinstance(metrics.get("create_pr"), dict) else {}
    effectiveness = metrics.get("effectiveness") if isinstance(metrics.get("effectiveness"), dict) else {}
    return {
        "run_id": metrics.get("run_id") or "",
        "status": workflow.get("status") or "",
        "primary_failure": failures.get("primary_category") or "",
        "failure_subcategory": failures.get("subcategory") or "",
        "validation_strategy": testing.get("validation_strategy") or "full",
        "create_pr_outcome": create_pr.get("outcome") or "",
        "create_pr_reason": create_pr.get("reason") or "",
        "retry_recovered": bool(effectiveness.get("retry_recovered", False)),
        "skipped_command_count": testing.get("skipped_command_count") or 0,
        "command_reduction_rate": testing.get("command_reduction_rate") or 0.0,
        "duration_ms": workflow.get("duration_ms") or 0,
        "testing_duration_ms": testing.get("total_duration_ms") or 0,
        "terminal_node": workflow.get("terminal_node") or "",
        "path": path,
    }


def _display_failure_info(metrics: dict) -> dict[str, object]:
    workflow = metrics.get("workflow") if isinstance(metrics.get("workflow"), dict) else {}
    failures = metrics.get("failures") if isinstance(metrics.get("failures"), dict) else {}
    if failures.get("has_failure") is False or workflow.get("status") == "approved":
        return {"primary_category": None, "subcategory": None}
    return failures


def cli(argv: list[str] | None = None):
    """Main CLI entrypoint for the AI Code Agent."""
    args = parse_args(argv)

    config = AgentConfig()

    if args.command == "health":
        return run_health_check(config, getattr(args, "role", None), getattr(args, "json", False))
    if args.command == "diagnose":
        output_format = "json" if getattr(args, "json", False) and getattr(args, "format", "text") == "text" else getattr(args, "format", "text")
        return run_diagnostics(
            config,
            getattr(args, "repo", None),
            getattr(args, "run_id", None),
            getattr(args, "recent", 5),
            getattr(args, "status", None),
            getattr(args, "failure_category", None),
            output_format,
        )
    if args.command == "normalize-metrics":
        return run_normalize_metrics(
            config,
            getattr(args, "repo", None),
            getattr(args, "run_id", None),
            getattr(args, "json", False),
        )
    if args.command == "monitor":
        return run_monitor_services(
            config,
            getattr(args, "repo", None),
            getattr(args, "recent", 5),
            getattr(args, "backend_host", "127.0.0.1"),
            getattr(args, "backend_port", 8000),
            getattr(args, "frontend_host", "127.0.0.1"),
            getattr(args, "frontend_port", 4173),
            getattr(args, "detach", False),
        )

    resolved_issue_description, issue_context = resolve_issue_input(args.issue, config)

    print(f"Starting AI Agent for issue: {args.issue}")

    # Initialize the LLM client
    llm = LLMClient.from_config(config)
    if not llm.enabled:
        print("LLM provider is not configured. Running in fallback mode.")

    graph = build_graph()

    # Define initial state
    initial_state: AgentState = {
        "issue_description": resolved_issue_description,
        "issue_context": issue_context,
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
