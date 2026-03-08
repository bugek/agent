from __future__ import annotations

import re
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any
from uuid import uuid4


ISO_SUFFIX = "Z"
PHASE_ORDER = ["plan", "code", "test", "review", "create_pr"]
EXECUTION_RUNS_ROOT = Path(".ai-code-agent") / "runs"
EXECUTION_METRICS_FILE = "metrics.json"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", ISO_SUFFIX)


def generate_run_id(timestamp: str | None = None) -> str:
    base_timestamp = _parse_timestamp(timestamp) or datetime.now(timezone.utc)
    return f"{base_timestamp.strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"


def build_execution_metrics(state: dict[str, Any]) -> dict[str, Any]:
    execution_events = [event for event in state.get("execution_events", []) if isinstance(event, dict)]
    started_at = state.get("workflow_started_at") or _first_timestamp(execution_events) or utc_now_iso()
    completed_at = _last_timestamp(execution_events) or started_at
    run_id = state.get("run_id") or generate_run_id(started_at)
    workspace_profile = state.get("workspace_profile") if isinstance(state.get("workspace_profile"), dict) else {}
    planning_context = state.get("planning_context") if isinstance(state.get("planning_context"), dict) else {}
    codegen_summary = state.get("codegen_summary") if isinstance(state.get("codegen_summary"), dict) else {}
    visual_review = state.get("visual_review") if isinstance(state.get("visual_review"), dict) else None
    testing_summary = state.get("testing_summary") if isinstance(state.get("testing_summary"), dict) else {}
    review_summary = state.get("review_summary") if isinstance(state.get("review_summary"), dict) else {}
    test_signals = _extract_validation_signals(state.get("test_results", ""))
    distinct_changed_files = sorted(
        {
            patch.get("file")
            for patch in state.get("patches", [])
            if isinstance(patch, dict) and isinstance(patch.get("file"), str)
        }
    )
    analysis_only = bool(re.search(r"\b(analyze|inspect|summari[sz]e|review|readiness)\b", state.get("issue_description", ""), re.I))
    failure_categories = _failure_categories(state, test_signals, review_summary, codegen_summary)

    return {
        "schema_version": "execution-metrics/v1",
        "run_id": run_id,
        "issue": {
            "mode": "analysis_only" if analysis_only else "change_request",
            "analysis_only": analysis_only,
            "source": "cli",
            "description": state.get("issue_description"),
        },
        "workflow": {
            "status": _workflow_status(state, review_summary),
            "started_at": started_at,
            "completed_at": completed_at,
            "duration_ms": _duration_ms(started_at, completed_at),
            "retry_count": _as_int(state.get("retry_count")),
            "attempt_count": max(1, _as_int(state.get("retry_count")) + 1),
            "terminal_node": execution_events[-1].get("node") if execution_events else None,
            "created_pr": bool(state.get("created_pr_url")),
        },
        "workspace": {
            "path": state.get("workspace_dir"),
            "has_python": bool(workspace_profile.get("has_python")),
            "has_package_json": bool(workspace_profile.get("has_package_json")),
            "frameworks": list(workspace_profile.get("frameworks", [])) if isinstance(workspace_profile.get("frameworks"), list) else [],
            "package_manager": workspace_profile.get("package_manager") or "none",
        },
        "planning": {
            "retrieval_strategy": planning_context.get("retrieval_strategy"),
            "candidate_file_count": _count_mapping_entries(planning_context.get("candidate_scores")),
            "graph_seed_file_count": _count_items(planning_context.get("graph_seed_files")),
            "blocked_file_count": _count_items(planning_context.get("blocked_files_to_edit")),
            "files_to_edit_count": _count_items(state.get("files_to_edit")),
        },
        "coding": {
            "generated_by": codegen_summary.get("generated_by"),
            "requested_operations": _as_int(codegen_summary.get("requested_operations")),
            "applied_operations": _as_int(codegen_summary.get("applied_operations")),
            "failed_operation_count": _count_items(codegen_summary.get("failed_operations")),
            "blocked_operation_count": _count_items(codegen_summary.get("blocked_operations")),
            "patch_count": _count_items(state.get("patches")),
            "changed_file_count": len(distinct_changed_files),
        },
        "testing": {
            "status": _testing_status(state, test_signals),
            "command_count": _testing_command_count(testing_summary, test_signals),
            "failed_command_count": _testing_failed_count(testing_summary, test_signals),
            "failed_commands": _testing_failed_commands(testing_summary, test_signals),
            "lint_issue_count": _testing_lint_issue_count(testing_summary, state.get("test_results", "")),
            "total_duration_ms": _as_int(testing_summary.get("total_duration_ms")),
            "slowest_command": _testing_slowest_command(testing_summary),
            "commands": _testing_command_summaries(testing_summary),
            "visual_review": _visual_review_metrics(visual_review),
        },
        "review": {
            "status": review_summary.get("status"),
            "approved": bool(state.get("review_approved", False)),
            "comment_count": _count_items(state.get("review_comments")),
            "residual_risk_count": _count_items(review_summary.get("residual_risks")),
            "changed_area_count": _count_items(review_summary.get("changed_areas")),
            "validation_failed_count": _count_items((review_summary.get("validation") or {}).get("failed")),
        },
        "failures": {
            "has_failure": not (bool(state.get("review_approved", False)) and bool(state.get("test_passed", False))),
            "primary_category": failure_categories[0] if failure_categories else None,
            "categories": failure_categories,
            "error_message": _primary_error_message(state, test_signals),
            "blocking_comment_count": _blocking_comment_count(state.get("review_comments", [])),
        },
        "phases": _phase_metrics(execution_events, started_at, state),
    }


def persist_execution_metrics(workspace_dir: str | None, run_id: str | None, metrics: dict[str, Any]) -> str | None:
    if not workspace_dir or not run_id or not isinstance(metrics, dict) or not metrics:
        return None

    run_dir = Path(workspace_dir) / EXECUTION_RUNS_ROOT / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    metrics_path = run_dir / EXECUTION_METRICS_FILE
    temporary_path = metrics_path.with_suffix(metrics_path.suffix + ".tmp")
    payload = json.dumps(metrics, indent=2, ensure_ascii=True) + "\n"
    temporary_path.write_text(payload, encoding="utf-8")
    temporary_path.replace(metrics_path)
    return _relative_workspace_path(workspace_dir, metrics_path)


def load_execution_metrics_artifact(
    workspace_dir: str | None,
    run_id: str | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    if not workspace_dir:
        return None, None

    runs_root = Path(workspace_dir) / EXECUTION_RUNS_ROOT
    if not runs_root.exists():
        return None, None

    candidates: list[Path]
    if run_id:
        candidates = [runs_root / run_id / EXECUTION_METRICS_FILE]
    else:
        candidates = sorted(
            runs_root.glob(f"*/{EXECUTION_METRICS_FILE}"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )

    for metrics_path in candidates:
        metrics = _load_metrics_file(metrics_path)
        if metrics is not None:
            return metrics, _relative_workspace_path(workspace_dir, metrics_path)
    return None, None


def _phase_metrics(execution_events: list[dict[str, Any]], started_at: str, state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    phase_metrics: dict[str, dict[str, Any]] = {
        phase: {"status": "not_run", "attempts": 0, "started_at": None, "completed_at": None, "duration_ms": 0}
        for phase in PHASE_ORDER
    }
    active_attempts: dict[tuple[str, int], datetime] = {}

    for event in execution_events:
        node = event.get("node")
        if node not in phase_metrics:
            continue
        current_timestamp = _parse_timestamp(event.get("timestamp"))
        event_type = event.get("event_type")
        attempt = event.get("attempt") if isinstance(event.get("attempt"), int) else 1
        metrics = phase_metrics[node]
        if event_type == "node_started":
            if metrics["started_at"] is None:
                metrics["started_at"] = event.get("timestamp") or started_at
            if current_timestamp is not None:
                active_attempts[(node, attempt)] = current_timestamp
            continue

        metrics["attempts"] = max(metrics["attempts"], attempt)
        if metrics["started_at"] is None:
            metrics["started_at"] = event.get("timestamp") or started_at
        metrics["completed_at"] = event.get("timestamp")
        start_timestamp = active_attempts.pop((node, attempt), None)
        if start_timestamp is not None and current_timestamp is not None:
            metrics["duration_ms"] += max(0, int((current_timestamp - start_timestamp).total_seconds() * 1000))
        else:
            metrics["duration_ms"] += _as_int(event.get("duration_ms"))
        metrics["status"] = event.get("status") or "completed"

    if phase_metrics["test"]["attempts"] > 0 and not bool(state.get("test_passed", False)):
        phase_metrics["test"]["status"] = "failed"
    elif phase_metrics["test"]["attempts"] > 0:
        phase_metrics["test"]["status"] = "passed"
    if phase_metrics["create_pr"]["attempts"] > 0:
        phase_metrics["create_pr"]["status"] = "completed"
    if phase_metrics["review"]["attempts"] > 0:
        phase_metrics["review"]["status"] = "approved" if bool(state.get("review_approved", False)) else "changes_required"
    if phase_metrics["code"]["attempts"] > 0:
        phase_metrics["code"]["status"] = "completed"
    if phase_metrics["plan"]["attempts"] > 0:
        phase_metrics["plan"]["status"] = "completed"

    return phase_metrics


def _workflow_status(state: dict[str, Any], review_summary: dict[str, Any]) -> str:
    if bool(state.get("review_approved", False)) and bool(state.get("test_passed", False)):
        return "approved"
    if review_summary.get("status") == "changes_required":
        return "changes_required"
    if state.get("error_message"):
        return "failed"
    if state.get("test_passed") is False and state.get("test_results"):
        return "failed"
    return "aborted"


def _testing_status(state: dict[str, Any], test_signals: list[dict[str, Any]]) -> str:
    if not test_signals and not state.get("test_results"):
        return "not_run"
    return "passed" if bool(state.get("test_passed", False)) else "failed"


def _visual_review_metrics(visual_review: dict[str, Any] | None) -> dict[str, Any] | None:
    if not visual_review or not visual_review.get("enabled"):
        return None

    state_coverage = visual_review.get("state_coverage") if isinstance(visual_review.get("state_coverage"), dict) else {}
    responsive_review = visual_review.get("responsive_review") if isinstance(visual_review.get("responsive_review"), dict) else {}
    required_flags = ["loading_state", "empty_state", "error_state", "success_state", "loading_file", "error_file"]
    missing_state_count = sum(1 for flag in required_flags if not state_coverage.get(flag))

    return {
        "enabled": True,
        "screenshot_status": visual_review.get("screenshot_status"),
        "artifact_count": _as_int(visual_review.get("artifact_count")),
        "missing_state_count": missing_state_count,
        "missing_responsive_category_count": _count_items(responsive_review.get("missing_categories")),
    }


def _testing_command_count(testing_summary: dict[str, Any], test_signals: list[dict[str, Any]]) -> int:
    count = testing_summary.get("command_count")
    if isinstance(count, int):
        return max(0, count)
    return len(test_signals)


def _testing_failed_count(testing_summary: dict[str, Any], test_signals: list[dict[str, Any]]) -> int:
    count = testing_summary.get("failed_command_count")
    if isinstance(count, int):
        return max(0, count)
    return len([signal for signal in test_signals if signal["exit_code"] != 0])


def _testing_failed_commands(testing_summary: dict[str, Any], test_signals: list[dict[str, Any]]) -> list[str]:
    failed_commands = testing_summary.get("failed_commands")
    if isinstance(failed_commands, list):
        return [label for label in failed_commands if isinstance(label, str)]
    return [signal["label"] for signal in test_signals if signal["exit_code"] != 0]


def _testing_lint_issue_count(testing_summary: dict[str, Any], test_results: str) -> int:
    count = testing_summary.get("lint_issue_count")
    if isinstance(count, int):
        return max(0, count)
    return _lint_issue_count(test_results)


def _testing_slowest_command(testing_summary: dict[str, Any]) -> dict[str, Any] | None:
    slowest_command = testing_summary.get("slowest_command")
    if not isinstance(slowest_command, dict):
        return None
    return {
        "label": slowest_command.get("label"),
        "duration_ms": _as_int(slowest_command.get("duration_ms")),
        "exit_code": slowest_command.get("exit_code"),
        "timed_out": bool(slowest_command.get("timed_out", False)),
    }


def _testing_command_summaries(testing_summary: dict[str, Any]) -> list[dict[str, Any]]:
    commands = testing_summary.get("commands")
    if not isinstance(commands, list):
        return []
    summaries: list[dict[str, Any]] = []
    for command in commands:
        if not isinstance(command, dict):
            continue
        summaries.append(
            {
                "label": command.get("label"),
                "exit_code": command.get("exit_code"),
                "duration_ms": _as_int(command.get("duration_ms")),
                "mode": command.get("mode"),
                "timed_out": bool(command.get("timed_out", False)),
            }
        )
    return summaries


def _extract_validation_signals(test_results: str) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    for match in re.finditer(r"^([A-Za-z0-9:_-]+)\(exit=(\d+)\):", test_results or "", re.M):
        signals.append({"label": match.group(1), "exit_code": int(match.group(2))})
    return signals


def _lint_issue_count(test_results: str) -> int:
    if "lint:\n" not in (test_results or ""):
        return 0
    lint_section = (test_results or "").split("lint:\n", 1)[1]
    return len([line for line in lint_section.splitlines() if line.strip()])


def _primary_error_message(state: dict[str, Any], test_signals: list[dict[str, Any]]) -> str | None:
    error_message = state.get("error_message")
    if isinstance(error_message, str) and error_message.strip():
        return error_message
    if not bool(state.get("test_passed", False)):
        return "Smoke tests failed."
    comments = state.get("review_comments")
    if isinstance(comments, list):
        for comment in comments:
            if isinstance(comment, str) and comment.strip() and comment.strip() != "Review passed.":
                return comment.strip()
    if test_signals:
        return next((f"Validation failed: {signal['label']}" for signal in test_signals if signal["exit_code"] != 0), None)
    return None


def _failure_categories(
    state: dict[str, Any],
    test_signals: list[dict[str, Any]],
    review_summary: dict[str, Any],
    codegen_summary: dict[str, Any],
) -> list[str]:
    categories: list[str] = []
    error_message = state.get("error_message") if isinstance(state.get("error_message"), str) else ""
    test_results = state.get("test_results") if isinstance(state.get("test_results"), str) else ""

    if _looks_like_configuration_failure(error_message, test_results):
        categories.append("configuration")
    if _looks_like_sandbox_failure(error_message, test_results):
        categories.append("sandbox")
    if _count_items(codegen_summary.get("blocked_operations")) or _count_items((state.get("planning_context") or {}).get("blocked_files_to_edit")):
        categories.append("policy")
    if not bool(state.get("test_passed", False)) and test_signals:
        categories.append("validation")
    if _count_items(codegen_summary.get("failed_operations")) or (
        not bool(state.get("patches")) and not _is_analysis_only(state.get("issue_description", ""))
    ):
        categories.append("generation")
    if review_summary.get("status") == "changes_required" or not bool(state.get("review_approved", False)):
        categories.append("review")

    deduped: list[str] = []
    for category in ["configuration", "sandbox", "policy", "validation", "generation", "review"]:
        if category in categories and category not in deduped:
            deduped.append(category)
    return deduped or (["unknown"] if state.get("error_message") or test_results else [])


def _looks_like_configuration_failure(error_message: str, test_results: str) -> bool:
    haystack = f"{error_message}\n{test_results}".lower()
    return any(token in haystack for token in ["not configured", "missing api key", "provider is not configured", "unsupported validation mode"])


def _looks_like_sandbox_failure(error_message: str, test_results: str) -> bool:
    haystack = f"{error_message}\n{test_results}".lower()
    return any(token in haystack for token in ["sandbox", "docker", "container", "timed out"])


def _blocking_comment_count(review_comments: Any) -> int:
    if not isinstance(review_comments, list):
        return 0
    blocker_tokens = ["failed", "missing", "blocked", "changes required", "did not", "no code changes"]
    return len(
        [
            comment
            for comment in review_comments
            if isinstance(comment, str)
            and comment.strip()
            and comment.strip() != "Review passed."
            and any(token in comment.lower() for token in blocker_tokens)
        ]
    )


def _is_analysis_only(issue_description: str) -> bool:
    return bool(re.search(r"\b(analyze|inspect|summari[sz]e|review|readiness)\b", issue_description, re.I))


def _count_items(value: Any) -> int:
    return len(value) if isinstance(value, (list, tuple, set)) else 0


def _count_mapping_entries(value: Any) -> int:
    return len(value) if isinstance(value, dict) else 0


def _as_int(value: Any) -> int:
    return value if isinstance(value, int) and value >= 0 else 0


def _first_timestamp(execution_events: list[dict[str, Any]]) -> str | None:
    return next((timestamp for timestamp in [event.get("timestamp") for event in execution_events] if isinstance(timestamp, str)), None)


def _last_timestamp(execution_events: list[dict[str, Any]]) -> str | None:
    timestamps = [event.get("timestamp") for event in execution_events if isinstance(event.get("timestamp"), str)]
    return timestamps[-1] if timestamps else None


def _duration_ms(started_at: str | None, completed_at: str | None) -> int:
    start = _parse_timestamp(started_at)
    end = _parse_timestamp(completed_at)
    if start is None or end is None:
        return 0
    return max(0, int((end - start).total_seconds() * 1000))


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    normalized = value[:-1] + "+00:00" if value.endswith(ISO_SUFFIX) else value
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def _to_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", ISO_SUFFIX)


def _relative_workspace_path(workspace_dir: str, target: Path) -> str:
    try:
        return target.relative_to(Path(workspace_dir)).as_posix()
    except ValueError:
        return target.as_posix()


def _load_metrics_file(metrics_path: Path) -> dict[str, Any] | None:
    if not metrics_path.exists() or not metrics_path.is_file():
        return None
    try:
        data = json.loads(metrics_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None