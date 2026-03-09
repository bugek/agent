from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ai_code_agent.tools.sandbox import SandboxRunner


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "compose-smoke"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the committed Docker Compose smoke fixture through SandboxRunner and verify service-backed execution."
    )
    parser.add_argument("--keep-workspace", action="store_true", help="Keep the temporary workspace for inspection.")
    parser.add_argument("--json", action="store_true", help="Print the smoke summary as JSON.")
    return parser.parse_args()


def run_fixture(keep_workspace: bool) -> tuple[dict[str, object], str | None]:
    temp_dir = tempfile.mkdtemp(prefix="compose-smoke-")
    workspace_dir = Path(temp_dir) / "workspace"
    shutil.copytree(FIXTURE_DIR, workspace_dir)

    if shutil.which("docker") is None:
        result = {"passed": True, "skipped": True, "reason": "docker_unavailable"}
        if keep_workspace:
            return result, str(workspace_dir)
        shutil.rmtree(temp_dir, ignore_errors=True)
        return result, None

    compose_version = subprocess.run(
        ["docker", "compose", "version"],
        cwd=workspace_dir,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if compose_version.returncode != 0:
        result = {"passed": True, "skipped": True, "reason": "docker_compose_unavailable"}
        if keep_workspace:
            return result, str(workspace_dir)
        shutil.rmtree(temp_dir, ignore_errors=True)
        return result, None

    runner = SandboxRunner(
        "unused-compose-image",
        workspace_dir=str(workspace_dir),
        mode="compose_required",
        compose_file="docker-compose.yml",
        compose_service="app",
        compose_project_name="ai-code-agent-compose-smoke",
        compose_ready_services=["app", "sidecar"],
        compose_readiness_timeout_seconds=30,
    )

    startup = runner.start_container()
    execution: dict[str, object] | None = None
    compose_logs_path: str | None = None

    try:
        if startup.get("resolved_mode") != "compose" or not startup.get("started"):
            result = {
                "passed": False,
                "skipped": False,
                "startup": startup,
                "reason": startup.get("fallback_reason") or "compose_start_failed",
            }
        else:
            execution = runner.execute("printf 'compose-smoke-ok'", timeout=30)
            compose_logs_path = runner.capture_compose_logs()
            logs_exist = bool(compose_logs_path and (workspace_dir / compose_logs_path).exists())
            result = {
                "passed": execution.get("exit_code") == 0 and "compose-smoke-ok" in str(execution.get("stdout", "")) and logs_exist,
                "skipped": False,
                "startup": startup,
                "execution": execution,
                "compose_logs_path": compose_logs_path,
                "logs_exist": logs_exist,
            }
    finally:
        cleanup = runner.cleanup()
        if 'result' in locals():
            result["cleanup"] = cleanup

    if keep_workspace:
        return result, str(workspace_dir)

    shutil.rmtree(temp_dir, ignore_errors=True)
    return result, None


def main() -> int:
    args = parse_args()
    result, workspace_dir = run_fixture(args.keep_workspace)

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=True))
    else:
        if result.get("skipped"):
            print(f"Compose smoke skipped: {result.get('reason')}")
        else:
            print(f"Compose smoke passed: {result.get('passed')}")
            print(f"Resolved mode: {result.get('startup', {}).get('resolved_mode') if isinstance(result.get('startup'), dict) else 'unknown'}")
            print(f"Compose logs: {result.get('compose_logs_path') or 'none'}")
        if workspace_dir:
            print(f"Workspace kept at: {workspace_dir}")

    return 0 if result.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())