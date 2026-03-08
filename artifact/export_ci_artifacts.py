from __future__ import annotations

import argparse
import json
from pathlib import Path

from ai_code_agent.metrics import (
    build_diagnostics_summary,
    build_execution_metrics_trend,
    list_execution_metrics_artifacts,
    persist_diagnostics_summary,
    utc_now_iso,
)


CI_ARTIFACTS_ROOT = Path(".ai-code-agent") / "ci"
CI_SUMMARY_FILE = "summary.json"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare CI validation artifacts for upload.")
    parser.add_argument("--workspace", default=".", help="Workspace root used to resolve artifact paths.")
    parser.add_argument("--validation-log", default=None, help="Optional path to the validation log file.")
    parser.add_argument("--recent", type=int, default=10, help="Number of recent metrics artifacts to summarize.")
    return parser.parse_args(argv)


def build_ci_artifact_summary(
    workspace_dir: str | Path,
    *,
    validation_log: str | Path | None,
    recent: int,
) -> tuple[dict[str, object], str | None]:
    workspace_path = Path(workspace_dir).resolve()
    recent_count = max(1, recent)
    metrics_entries = list_execution_metrics_artifacts(str(workspace_path), limit=recent_count)
    trend = build_execution_metrics_trend(metrics_entries)
    diagnostics_summary_path = None

    if metrics_entries:
        summary = build_diagnostics_summary(
            metrics_entries,
            trend,
            recent=recent_count,
            filters={"status": None, "failure_category": None},
        )
        diagnostics_summary_path = persist_diagnostics_summary(
            str(workspace_path),
            summary,
            recent=recent_count,
            status=None,
            failure_category=None,
        )

    latest_metrics_path = metrics_entries[0][1] if metrics_entries else None
    latest_run_id = metrics_entries[0][0].get("run_id") if metrics_entries else None

    report = {
        "schema_version": "ci-artifacts-summary/v1",
        "generated_at": utc_now_iso(),
        "recent": recent_count,
        "validation_log_path": _relative_workspace_path(workspace_path, validation_log),
        "execution_metrics_count": len(metrics_entries),
        "latest_execution_metrics_path": latest_metrics_path,
        "latest_run_id": latest_run_id,
        "diagnostics_summary_path": diagnostics_summary_path,
        "trend": trend,
        "notes": _build_notes(workspace_path, metrics_entries, validation_log),
    }
    return report, diagnostics_summary_path


def persist_ci_artifact_summary(workspace_dir: str | Path, summary: dict[str, object]) -> str:
    workspace_path = Path(workspace_dir).resolve()
    artifact_dir = workspace_path / CI_ARTIFACTS_ROOT
    artifact_dir.mkdir(parents=True, exist_ok=True)
    output_path = artifact_dir / CI_SUMMARY_FILE
    output_path.write_text(json.dumps(summary, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    return _relative_workspace_path(workspace_path, output_path) or str(output_path)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    summary, _ = build_ci_artifact_summary(
        args.workspace,
        validation_log=args.validation_log,
        recent=args.recent,
    )
    summary_path = persist_ci_artifact_summary(args.workspace, summary)
    print(f"CI artifact summary: {summary_path}")
    diagnostics_summary_path = summary.get("diagnostics_summary_path")
    if isinstance(diagnostics_summary_path, str) and diagnostics_summary_path:
        print(f"Diagnostics summary: {diagnostics_summary_path}")
    return 0


def _relative_workspace_path(workspace_dir: Path, target: str | Path | None) -> str | None:
    if target is None:
        return None

    target_path = Path(target)
    if not target_path.is_absolute():
        target_path = workspace_dir / target_path

    if not target_path.exists():
        return None

    try:
        return target_path.resolve().relative_to(workspace_dir).as_posix()
    except ValueError:
        return target_path.resolve().as_posix()


def _build_notes(
    workspace_dir: Path,
    metrics_entries: list[tuple[dict[str, object], str]],
    validation_log: str | Path | None,
) -> list[str]:
    notes: list[str] = []
    if validation_log is None:
        notes.append("validation log was not provided")
    elif _relative_workspace_path(workspace_dir, validation_log) is None:
        notes.append("validation log path does not exist")

    if not metrics_entries:
        notes.append("no execution metrics artifacts were found in the workspace")
    return notes


if __name__ == "__main__":
    raise SystemExit(main())