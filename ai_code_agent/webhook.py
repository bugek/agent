import os
import json
import mimetypes
from pathlib import Path
from urllib.parse import urlencode

try:
    import uvicorn
    from fastapi import FastAPI, HTTPException, Query, Request
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import FileResponse, RedirectResponse
except ImportError:  # pragma: no cover - optional dependency
    uvicorn = None
    FastAPI = None
    HTTPException = None
    Query = None
    Request = None
    CORSMiddleware = None
    FileResponse = None
    RedirectResponse = None

from ai_code_agent.config import AgentConfig
from ai_code_agent.metrics import build_execution_metrics_trend, list_execution_metrics_artifacts, utc_now_iso


VISUAL_REVIEW_ROOT = Path('.ai-code-agent') / 'visual-review'
VISUAL_REVIEW_MANIFEST = 'manifest.json'
VISUAL_REVIEW_IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp'}


def _monitor_frontend_url(repo: str | None = None, recent: int | None = None) -> str:
    frontend_base = os.environ.get("MONITOR_FRONTEND_URL", "http://127.0.0.1:4173").rstrip("/")
    query: dict[str, str] = {}
    if repo:
        query["repo"] = repo
    if recent is not None:
        query["recent"] = str(recent)
    if not query:
        return frontend_base
    return f"{frontend_base}/?{urlencode(query)}"


def _join_items(items: list[str]) -> str:
    filtered = [item for item in items if item]
    return ", ".join(filtered) if filtered else "none"


def _command_summaries(commands: object) -> list[str]:
    if not isinstance(commands, list):
        return []
    summaries: list[str] = []
    for command in commands:
        if not isinstance(command, dict):
            continue
        label = command.get("label") or "unknown"
        exit_code = command.get("exit_code")
        duration_ms = command.get("duration_ms") or 0
        mode = command.get("mode") or "unknown"
        summaries.append(f"{label} (exit={exit_code}, duration_ms={duration_ms}, mode={mode})")
    return summaries


def _relative_workspace_path(workspace_dir: str, target_path: Path) -> str:
    try:
        return target_path.relative_to(Path(workspace_dir)).as_posix()
    except ValueError:
        return target_path.as_posix()


def _resolve_workspace_file(workspace_dir: str, relative_path: str) -> Path | None:
    workspace_root = Path(workspace_dir).resolve()
    candidate = (workspace_root / relative_path).resolve()
    try:
        candidate.relative_to(workspace_root)
    except ValueError:
        return None
    return candidate


def _artifact_entry_from_file(workspace_dir: str, file_path: Path) -> dict[str, object] | None:
    if not file_path.exists() or not file_path.is_file() or file_path.suffix.lower() not in VISUAL_REVIEW_IMAGE_EXTENSIONS:
        return None
    return {
        'path': _relative_workspace_path(workspace_dir, file_path),
        'kind': 'screenshot',
        'extension': file_path.suffix.lower(),
    }


def _load_visual_review_manifest(root_dir: Path) -> dict[str, object] | None:
    manifest_path = root_dir / VISUAL_REVIEW_MANIFEST
    if not manifest_path.exists():
        return None
    try:
        data = json.loads(manifest_path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _visual_review_images(workspace_dir: str) -> list[dict[str, object]]:
    root_dir = Path(workspace_dir) / VISUAL_REVIEW_ROOT
    manifest = _load_visual_review_manifest(root_dir)
    artifacts: list[dict[str, object]] = []

    if isinstance(manifest, dict) and isinstance(manifest.get('artifacts'), list):
        for entry in manifest['artifacts']:
            if not isinstance(entry, dict):
                continue
            raw_path = entry.get('path')
            if not isinstance(raw_path, str) or not raw_path.strip():
                continue
            candidate = Path(raw_path)
            if not candidate.is_absolute():
                candidate = root_dir / candidate
            artifact = _artifact_entry_from_file(workspace_dir, candidate)
            if artifact is None:
                continue
            for key in ['route', 'title', 'status', 'device', 'locale', 'viewport']:
                value = entry.get(key)
                if value is not None:
                    artifact[key] = value
            artifacts.append(artifact)

    if artifacts:
        return artifacts

    if not root_dir.exists():
        return []

    discovered: list[dict[str, object]] = []
    for file_path in sorted(root_dir.rglob('*')):
        artifact = _artifact_entry_from_file(workspace_dir, file_path)
        if artifact is not None:
            discovered.append(artifact)
    return discovered


def _monitor_image_entries(workspace_dir: str) -> list[dict[str, object]]:
    images: list[dict[str, object]] = []
    for artifact in _visual_review_images(workspace_dir):
        path = artifact.get('path')
        if not isinstance(path, str):
            continue
        route = artifact.get('route') if isinstance(artifact.get('route'), str) else None
        title = artifact.get('title') if isinstance(artifact.get('title'), str) else None
        viewport = artifact.get('viewport') if isinstance(artifact.get('viewport'), dict) else None
        viewport_text = None
        if viewport and isinstance(viewport.get('width'), int) and isinstance(viewport.get('height'), int):
            viewport_text = f"{viewport['width']}x{viewport['height']}"
        caption_parts = [part for part in [route, viewport_text] if part]
        images.append(
            {
                'path': path,
                'title': title or Path(path).name,
                'caption': ' | '.join(caption_parts) if caption_parts else path,
                'url': f"/api/monitor/artifact?{urlencode({'repo': workspace_dir, 'path': path})}",
            }
        )
    return images


def _monitor_phase_details(metrics: dict[str, object], workspace_dir: str | None = None) -> dict[str, dict[str, object]]:
    issue = metrics.get("issue") if isinstance(metrics.get("issue"), dict) else {}
    workspace = metrics.get("workspace") if isinstance(metrics.get("workspace"), dict) else {}
    workflow = metrics.get("workflow") if isinstance(metrics.get("workflow"), dict) else {}
    planning = metrics.get("planning") if isinstance(metrics.get("planning"), dict) else {}
    coding = metrics.get("coding") if isinstance(metrics.get("coding"), dict) else {}
    testing = metrics.get("testing") if isinstance(metrics.get("testing"), dict) else {}
    review = metrics.get("review") if isinstance(metrics.get("review"), dict) else {}
    create_pr = metrics.get("create_pr") if isinstance(metrics.get("create_pr"), dict) else {}
    failures = metrics.get("failures") if isinstance(metrics.get("failures"), dict) else {}

    review_remediation = review.get("remediation") if isinstance(review.get("remediation"), dict) else {}

    issue_description = issue.get("description") if isinstance(issue.get("description"), str) else None
    frameworks = workspace.get("frameworks") if isinstance(workspace.get("frameworks"), list) else []
    failed_commands = testing.get("failed_commands") if isinstance(testing.get("failed_commands"), list) else []
    requested_retry_labels = testing.get("requested_retry_labels") if isinstance(testing.get("requested_retry_labels"), list) else []
    monitor_images = _monitor_image_entries(workspace_dir) if workspace_dir else []

    return {
        "plan": {
            "title": "Planner agent",
            "narrative": "The planner turns the issue into a concrete execution plan, retrieval scope, and downstream edit intent.",
            "inputs": [
                f"Issue: {issue_description or 'none'}",
                f"Workspace frameworks: {_join_items([str(item) for item in frameworks if isinstance(item, str)])}",
            ],
            "outputs": [
                f"Plan summary: {planning.get('plan_summary') or 'none'}",
                f"Retrieval strategy: {planning.get('retrieval_strategy') or 'none'}",
                f"Candidate files: {planning.get('candidate_file_count') or 0}",
                f"Files to edit: {planning.get('files_to_edit_count') or 0}",
                f"Edit intent count: {planning.get('edit_intent_count') or 0}",
            ],
            "highlights": [
                f"Graph seed files: {planning.get('graph_seed_file_count') or 0}",
                f"Blocked files: {planning.get('blocked_file_count') or 0}",
            ],
            "images": [],
        },
        "code": {
            "title": "Coder agent",
            "narrative": "The coder converts the plan into file operations, patches, and remediation edits when a retry loop is active.",
            "inputs": [
                f"Requested operations: {coding.get('requested_operations') or 0}",
                f"Generation source: {coding.get('generated_by') or 'none'}",
            ],
            "outputs": [
                f"Applied operations: {coding.get('applied_operations') or 0}",
                f"Patch count: {coding.get('patch_count') or 0}",
                f"Changed files: {coding.get('changed_file_count') or 0}",
            ],
            "highlights": [
                f"Blocked operations: {coding.get('blocked_operation_count') or 0}",
                f"Failed operations: {coding.get('failed_operation_count') or 0}",
                f"Remediation applied: {'yes' if coding.get('remediation_applied') else 'no'}",
            ],
            "images": [],
        },
        "test": {
            "title": "Tester agent",
            "narrative": "The tester validates the workspace and records whether the current run used a full pass or a targeted retry strategy.",
            "inputs": [
                f"Validation strategy: {testing.get('validation_strategy') or 'full'}",
                f"Selected commands: {testing.get('selected_command_count') or 0}",
                f"Sandbox mode: {testing.get('sandbox_mode') or testing.get('sandbox_requested_mode') or 'none'}",
            ],
            "outputs": [
                f"Testing status: {testing.get('status') or 'none'}",
                f"Failed commands: {_join_items([str(item) for item in failed_commands if isinstance(item, str)])}",
                f"Lint issues: {testing.get('lint_issue_count') or 0}",
                f"Screenshot status: {((testing.get('visual_review') if isinstance(testing.get('visual_review'), dict) else {}) or {}).get('screenshot_status') or 'none'}",
                f"Screenshot artifacts: {((testing.get('visual_review') if isinstance(testing.get('visual_review'), dict) else {}) or {}).get('artifact_count') or 0}",
                *[f"Command: {item}" for item in _command_summaries(testing.get('commands'))],
            ],
            "highlights": [
                f"Requested retry labels: {_join_items([str(item) for item in requested_retry_labels if isinstance(item, str)])}",
                f"Skipped commands: {testing.get('skipped_command_count') or 0}",
                f"Failure taxonomy: {failures.get('primary_category') or 'none'}/{failures.get('subcategory') or 'none'}",
            ],
            "images": monitor_images,
        },
        "review": {
            "title": "Reviewer agent",
            "narrative": "The reviewer judges whether the run is acceptable, which validations still fail, and whether another remediation pass is required.",
            "inputs": [
                f"Workflow status: {workflow.get('status') or 'none'}",
                f"Validation failures: {review.get('validation_failed_count') or 0}",
            ],
            "outputs": [
                f"Review status: {review.get('status') or 'none'}",
                f"Approved: {'yes' if review.get('approved') else 'no'}",
                f"Residual risks: {review.get('residual_risk_count') or 0}",
                f"Remediation guidance: {_join_items([str(item) for item in review_remediation.get('guidance', []) if isinstance(item, str)])}",
            ],
            "highlights": [
                f"Changed areas: {review.get('changed_area_count') or 0}",
                f"Remediation required: {'yes' if review.get('remediation_required') else 'no'}",
                f"Focus areas: {_join_items([str(item) for item in review_remediation.get('focus_areas', []) if isinstance(item, str)])}",
            ],
            "images": monitor_images,
        },
        "create_pr": {
            "title": "Publish step",
            "narrative": "The publish step creates, reuses, or skips the pull request after the review gate has been satisfied.",
            "inputs": [
                f"Terminal node: {workflow.get('terminal_node') or 'none'}",
                f"Review approved: {'yes' if review.get('approved') else 'no'}",
            ],
            "outputs": [
                f"PR outcome: {create_pr.get('outcome') or 'none'}",
                f"Reason: {create_pr.get('reason') or 'none'}",
                f"Provider: {create_pr.get('provider') or 'none'}",
                f"Message: {create_pr.get('message') or 'none'}",
            ],
            "highlights": [
                f"Branch: {create_pr.get('branch_name') or 'none'}",
                f"PR URL: {create_pr.get('pr_url') or 'none'}",
            ],
            "images": [],
        },
    }


def _monitor_payload(repo: str | None, recent: int) -> dict[str, object]:
    config = AgentConfig()
    workspace_dir = repo or config.workspace_dir
    metrics_entries = list_execution_metrics_artifacts(workspace_dir, limit=max(1, recent))
    latest_metrics = metrics_entries[0][0] if metrics_entries else {}
    latest_path = metrics_entries[0][1] if metrics_entries else None
    trend = build_execution_metrics_trend(metrics_entries)
    rows: list[dict[str, object]] = []
    for metrics, path in metrics_entries:
        workflow = metrics.get("workflow") if isinstance(metrics.get("workflow"), dict) else {}
        failures = metrics.get("failures") if isinstance(metrics.get("failures"), dict) else {}
        testing = metrics.get("testing") if isinstance(metrics.get("testing"), dict) else {}
        rows.append(
            {
                "run_id": metrics.get("run_id") or "",
                "status": workflow.get("status") or "",
                "primary_failure": failures.get("primary_category") or "",
                "failure_subcategory": failures.get("subcategory") or "",
                "validation_strategy": testing.get("validation_strategy") or "full",
                "duration_ms": workflow.get("duration_ms") or 0,
                "path": path,
            }
        )
    return {
        "workspace_dir": workspace_dir,
        "latest": latest_metrics,
        "latest_path": latest_path,
        "phase_details": _monitor_phase_details(latest_metrics, workspace_dir) if latest_metrics else {},
        "trend": trend,
        "rows": rows,
        "generated_at": utc_now_iso(),
    }


if FastAPI is not None:
    app = FastAPI(title="AI Code Agent Webhook Server")
    if CORSMiddleware is not None:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[
                "http://127.0.0.1:4173",
                "http://localhost:4173",
            ],
            allow_credentials=False,
            allow_methods=["GET"],
            allow_headers=["*"],
        )

    @app.get("/monitor")
    async def monitor_page(repo: str | None = Query(default=None), recent: int = Query(default=5, ge=1, le=20)):
        """Redirect monitor traffic to the dedicated frontend service."""
        if RedirectResponse is None:
            raise RuntimeError("fastapi responses must be installed to redirect monitor traffic")
        return RedirectResponse(url=_monitor_frontend_url(repo=repo, recent=recent), status_code=307)

    @app.get("/api/monitor")
    async def monitor_api(repo: str | None = Query(default=None), recent: int = Query(default=5, ge=1, le=20)):
        """Return recent run status and timeline data for the requested workspace."""
        return _monitor_payload(repo, recent)

    @app.get('/api/monitor/artifact')
    async def monitor_artifact(repo: str = Query(...), path: str = Query(...)):
        """Serve a visual-review artifact from the requested workspace."""
        if FileResponse is None or HTTPException is None:
            raise RuntimeError('fastapi responses must be installed to return monitor artifacts')
        resolved = _resolve_workspace_file(repo, path)
        if resolved is None or not resolved.exists() or not resolved.is_file():
            raise HTTPException(status_code=404, detail='artifact not found')
        if resolved.suffix.lower() not in VISUAL_REVIEW_IMAGE_EXTENSIONS:
            raise HTTPException(status_code=400, detail='unsupported artifact type')
        media_type = mimetypes.guess_type(str(resolved))[0] or 'application/octet-stream'
        return FileResponse(path=resolved, media_type=media_type, content_disposition_type='inline')

    @app.post("/github/webhook")
    async def github_webhook(request: Request):
        """Handle incoming GitHub events."""
        payload = await request.json()
        return {"status": "received", "source": "github", "event_keys": sorted(payload.keys())}

    @app.post("/ado/webhook")
    async def ado_webhook(request: Request):
        """Handle incoming Azure DevOps service hook events."""
        payload = await request.json()
        return {"status": "received", "source": "ado", "event_keys": sorted(payload.keys())}
else:
    app = None


def start_server(host: str = "0.0.0.0", port: int = 8000):
    if uvicorn is None or app is None:
        raise RuntimeError("fastapi and uvicorn must be installed to start the webhook server")
    uvicorn.run("ai_code_agent.webhook:app", host=host, port=port, reload=True)


if __name__ == "__main__":
    start_server()
