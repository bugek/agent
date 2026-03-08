from __future__ import annotations

import argparse
import json
import os
import socket
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = Path(__file__).parent / "fixtures" / "nextjs-visual-review"
ARTIFACT_ROOT = Path(".ai-code-agent") / "visual-review"
MANIFEST_PATH = ARTIFACT_ROOT / "manifest.json"
SCREENSHOT_DIR = ARTIFACT_ROOT / "screenshots"
REQUIRED_VIEWPORT_CATEGORIES = {"mobile", "desktop"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the committed Next.js visual-review fixture and verify that screenshot artifacts are produced."
    )
    parser.add_argument("--keep-workspace", action="store_true", help="Keep the temporary workspace for inspection.")
    parser.add_argument("--json", action="store_true", help="Print the final smoke summary as JSON.")
    return parser.parse_args()


def run_fixture(keep_workspace: bool) -> tuple[dict[str, object], str | None]:
    temp_dir = tempfile.mkdtemp(prefix="nextjs-visual-review-smoke-")
    workspace_dir = Path(temp_dir) / "workspace"
    shutil.copytree(FIXTURE_DIR, workspace_dir, ignore=shutil.ignore_patterns("node_modules", ".next", ".ai-code-agent"))
    port = _find_free_port()
    visual_review_env = {
        "PLAYWRIGHT_BASE_URL": f"http://127.0.0.1:{port}",
        "PLAYWRIGHT_WEB_SERVER_COMMAND": f"npm run dev -- --port {port}",
    }

    commands = [
        ([_node_tool("npm"), "install"], None),
        (_playwright_install_command(), None),
        ([_node_tool("npm"), "run", "visual-review"], visual_review_env),
    ]

    for command, env in commands:
        _run_command(command, workspace_dir, env)

    result = _collect_result(workspace_dir)

    if keep_workspace:
        return result, str(workspace_dir)

    shutil.rmtree(temp_dir, ignore_errors=True)
    return result, None


def _run_command(command: list[str], workspace_dir: Path, extra_env: dict[str, str] | None = None) -> None:
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    completed = subprocess.run(command, cwd=workspace_dir, env=env, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"Command failed with exit={completed.returncode}: {' '.join(command)}")


def _playwright_install_command() -> list[str]:
    command = [_node_tool("npx"), "playwright", "install"]
    if sys.platform.startswith("linux"):
        command.append("--with-deps")
    command.append("chromium")
    return command


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _node_tool(name: str) -> str:
    return f"{name}.cmd" if os.name == "nt" else name


def _collect_result(workspace_dir: Path) -> dict[str, object]:
    manifest_file = workspace_dir / MANIFEST_PATH
    screenshot_dir = workspace_dir / SCREENSHOT_DIR
    manifest = _load_manifest(manifest_file)
    artifacts = _artifact_paths_from_manifest(workspace_dir, manifest)
    screenshot_files = sorted(path.relative_to(workspace_dir).as_posix() for path in screenshot_dir.glob("**/*") if path.is_file())
    viewport_categories = _viewport_categories_from_manifest(manifest)
    missing_categories = sorted(REQUIRED_VIEWPORT_CATEGORIES.difference(viewport_categories))

    return {
        "manifest_path": manifest_file.relative_to(workspace_dir).as_posix() if manifest_file.exists() else None,
        "manifest_detected": manifest is not None,
        "artifact_count": len(artifacts),
        "artifacts": artifacts,
        "screenshot_files": screenshot_files,
        "viewport_categories": sorted(viewport_categories),
        "missing_viewport_categories": missing_categories,
        "passed": bool(manifest and artifacts and screenshot_files and not missing_categories),
    }


def _load_manifest(manifest_file: Path) -> dict[str, object] | None:
    if not manifest_file.exists():
        return None
    try:
        payload = json.loads(manifest_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _artifact_paths_from_manifest(workspace_dir: Path, manifest: dict[str, object] | None) -> list[str]:
    if not isinstance(manifest, dict):
        return []
    raw_artifacts = manifest.get("artifacts")
    if not isinstance(raw_artifacts, list):
        return []

    resolved: list[str] = []
    for entry in raw_artifacts:
        if not isinstance(entry, dict):
            continue
        raw_path = entry.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            continue
        artifact_path = workspace_dir / ARTIFACT_ROOT / raw_path
        if artifact_path.exists() and artifact_path.is_file():
            resolved.append(artifact_path.relative_to(workspace_dir).as_posix())
    return resolved


def _viewport_categories_from_manifest(manifest: dict[str, object] | None) -> set[str]:
    if not isinstance(manifest, dict):
        return set()
    raw_artifacts = manifest.get("artifacts")
    if not isinstance(raw_artifacts, list):
        return set()

    categories: set[str] = set()
    for entry in raw_artifacts:
        if not isinstance(entry, dict):
            continue
        viewport = entry.get("viewport")
        if not isinstance(viewport, dict):
            continue
        width = viewport.get("width")
        if not isinstance(width, int):
            continue
        if width < 768:
            categories.add("mobile")
        elif width >= 1024:
            categories.add("desktop")
        else:
            categories.add("tablet")
    return categories


def main() -> int:
    args = parse_args()

    try:
        result, workspace_dir = run_fixture(args.keep_workspace)
    except Exception as exc:
        if args.json:
            print(json.dumps({"passed": False, "error": str(exc)}, indent=2, ensure_ascii=True))
        else:
            print(f"Next.js visual review smoke failed: {exc}")
        return 1

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=True))
    else:
        print(f"Manifest detected: {result['manifest_detected']}")
        print(f"Artifact count: {result['artifact_count']}")
        print(f"Artifacts: {', '.join(result['artifacts'])}")
        if workspace_dir:
            print(f"Workspace kept at: {workspace_dir}")

    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())