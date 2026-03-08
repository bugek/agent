from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any


NEXTJS_FIXTURE_PACKAGE = Path(__file__).resolve().parents[2] / "artifact" / "fixtures" / "nextjs-visual-review" / "package.json"
DEFAULT_SANDBOX_IMAGE = os.getenv("DOCKER_IMAGE_NAME", "ai-code-agent-sandbox:latest")


def is_dependency_upgrade_request(issue: str) -> bool:
    if not isinstance(issue, str) or not issue.strip():
        return False

    lowered = issue.lower()
    has_upgrade_intent = bool(
        re.search(r"\b(upgrade|bump|update|refresh|move|migrate|latest|newest|supported version|baseline)\b", lowered)
    )
    has_dependency_scope = bool(
        re.search(r"\b(next(?:\.js)?|react(?:-dom)?|package\.json|dependency|dependencies|version|versions|npm|dist-tags?)\b", lowered)
    )
    has_version_display_request = bool(
        re.search(r"\b(display|show|surface|read)\b.*\bversion\b", lowered)
        or re.search(r"\bversion\b.*\bpackage\.json\b", lowered)
    )
    return (has_upgrade_intent and has_dependency_scope) or has_version_display_request


def resolve_workspace_version_context(
    workspace_dir: str,
    issue_description: str,
    workspace_profile: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    profile = workspace_profile or {}
    if "nextjs" not in profile.get("frameworks", []):
        return None
    if not is_dependency_upgrade_request(issue_description):
        return None

    package_json_path = Path(workspace_dir) / "package.json"
    package_data = _read_json(package_json_path)
    dependencies = {
        **(package_data.get("dependencies") if isinstance(package_data.get("dependencies"), dict) else {}),
        **(package_data.get("devDependencies") if isinstance(package_data.get("devDependencies"), dict) else {}),
    }
    current_version = _normalize_version(dependencies.get("next"))
    baseline_version = _fixture_baseline_version("next")
    dist_tags = _npm_dist_tags("next")
    latest_version = _normalize_version(dist_tags.get("latest")) or _npm_package_version("next")
    runtime_node_version = _runtime_node_version()
    selected_version, selection_reason = _select_target_version(
        issue_description,
        current_version,
        baseline_version,
        latest_version,
        dist_tags,
        runtime_node_version,
    )

    return {
        "dependency_upgrade_request": True,
        "package_name": "next",
        "current_version": current_version,
        "baseline_version": baseline_version,
        "latest_version": latest_version,
        "selected_version": selected_version,
        "selection_reason": selection_reason,
        "runtime_node_version": runtime_node_version,
        "selected_version_node_requirement": _package_node_engine("next", selected_version),
        "requires_version_display": _requires_version_display(issue_description),
        "package_json_version": _normalize_version(package_data.get("version")),
        "dist_tags": dist_tags,
    }


def _requires_version_display(issue_description: str) -> bool:
    lowered = issue_description.lower()
    return bool(
        re.search(r"\b(display|show|surface|read)\b.*\bversion\b", lowered)
        or re.search(r"\bversion\b.*\bpackage\.json\b", lowered)
    )


def _select_target_version(
    issue_description: str,
    current_version: str | None,
    baseline_version: str | None,
    latest_version: str | None,
    dist_tags: dict[str, str],
    runtime_node_version: str | None,
) -> tuple[str | None, str]:
    lowered = issue_description.lower()
    requests_latest = bool(re.search(r"\b(latest|newest|most recent|current stable)\b", lowered))
    requests_baseline = bool(re.search(r"\b(project baseline|current project baseline|baseline|supported version|compatible newer supported version)\b", lowered))

    preferred_version = None
    preferred_reason = None
    if requests_latest and latest_version:
        preferred_version = latest_version
        preferred_reason = "prefer_latest_requested"
    elif requests_baseline and baseline_version:
        preferred_version = baseline_version
        preferred_reason = "prefer_project_baseline"
    elif baseline_version:
        preferred_version = baseline_version
        preferred_reason = "prefer_project_baseline"
    elif latest_version:
        preferred_version = latest_version
        preferred_reason = "prefer_latest_available"
    else:
        preferred_version = current_version
        preferred_reason = "keep_current_version"

    if _version_compatible_with_runtime(preferred_version, runtime_node_version):
        return preferred_version, preferred_reason

    fallback = _best_compatible_tag_version(dist_tags, runtime_node_version)
    if fallback and fallback != preferred_version:
        return fallback, f"fallback_runtime_compatible_from_{preferred_reason}"

    return preferred_version, preferred_reason


def _best_compatible_tag_version(dist_tags: dict[str, str], runtime_node_version: str | None) -> str | None:
    candidates = sorted(
        {
            _normalize_version(value)
            for value in dist_tags.values()
            if isinstance(value, str) and _normalize_version(value)
        },
        key=_version_sort_key,
        reverse=True,
    )
    for candidate in candidates:
        if _version_compatible_with_runtime(candidate, runtime_node_version):
            return candidate
    return None


def _version_sort_key(version: str | None) -> tuple[int, int, int, str]:
    if not version:
        return (0, 0, 0, "")
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)(.*)$", version)
    if not match:
        return (0, 0, 0, version)
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)), match.group(4) or "")


def _runtime_node_version() -> str | None:
    sandbox_version = _docker_node_version(DEFAULT_SANDBOX_IMAGE)
    if sandbox_version:
        return sandbox_version
    return _local_node_version()


def _docker_node_version(image_name: str) -> str | None:
    try:
        completed = subprocess.run(
            ["docker", "run", "--rm", image_name, "node", "-v"],
            capture_output=True,
            text=True,
            timeout=20,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return _normalize_version(completed.stdout.strip())


def _local_node_version() -> str | None:
    node_executable = _resolve_executable("node")
    if node_executable is None:
        return None
    try:
        completed = subprocess.run([node_executable, "-v"], capture_output=True, text=True, timeout=10, check=True)
    except (OSError, subprocess.SubprocessError):
        return None
    return _normalize_version(completed.stdout.strip())


def _package_node_engine(package_name: str, version: str | None) -> str | None:
    if not version:
        return None
    raw = _npm_view(f"{package_name}@{version}", "engines", json_output=True)
    if isinstance(raw, dict):
        node_engine = raw.get("node")
        return str(node_engine) if isinstance(node_engine, str) and node_engine else None
    return None


def _version_compatible_with_runtime(version: str | None, runtime_node_version: str | None) -> bool:
    if not version or not runtime_node_version:
        return True
    requirement = _package_node_engine("next", version)
    if not requirement:
        return True
    return _satisfies_node_requirement(runtime_node_version, requirement)


def _satisfies_node_requirement(version: str, requirement: str) -> bool:
    normalized_version = _parse_semver(version)
    if normalized_version is None:
        return True
    clauses = [clause.strip() for clause in requirement.split("||") if clause.strip()]
    if not clauses:
        return True
    return any(_satisfies_clause(normalized_version, clause) for clause in clauses)


def _satisfies_clause(version: tuple[int, int, int], clause: str) -> bool:
    clause = clause.strip()
    if clause.startswith(">="):
        minimum = _parse_semver(clause[2:].strip())
        return minimum is None or version >= minimum
    if clause.startswith("^"):
        minimum = _parse_semver(clause[1:].strip())
        if minimum is None:
            return True
        return version[0] == minimum[0] and version >= minimum
    exact = _parse_semver(clause)
    if exact is not None:
        return version == exact
    return True


def _parse_semver(value: str) -> tuple[int, int, int] | None:
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", value)
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def _fixture_baseline_version(package_name: str) -> str | None:
    package_data = _read_json(NEXTJS_FIXTURE_PACKAGE)
    dependencies = package_data.get("dependencies") if isinstance(package_data.get("dependencies"), dict) else {}
    dev_dependencies = package_data.get("devDependencies") if isinstance(package_data.get("devDependencies"), dict) else {}
    return _normalize_version(dependencies.get(package_name) or dev_dependencies.get(package_name))


def _npm_package_version(package_name: str) -> str | None:
    return _normalize_version(_npm_view(package_name, "version"))


def _npm_dist_tags(package_name: str) -> dict[str, str]:
    raw = _npm_view(package_name, "dist-tags", json_output=True)
    if isinstance(raw, dict):
        return {
            str(key): str(value)
            for key, value in raw.items()
            if isinstance(key, str) and isinstance(value, str)
        }
    return {}


def _npm_view(package_name: str, field: str, *, json_output: bool = False) -> Any:
    npm_executable = _resolve_executable("npm")
    if npm_executable is None:
        return {} if json_output else None
    command = [npm_executable, "view", package_name, field]
    if json_output:
        command.append("--json")
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=20, check=True)
    except (OSError, subprocess.SubprocessError):
        return {} if json_output else None
    output = completed.stdout.strip()
    if not output:
        return {} if json_output else None
    if json_output:
        try:
            parsed = json.loads(output)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return output


def _normalize_version(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    match = re.search(r"(\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?)", value)
    if match:
        return match.group(1)
    normalized = value.strip()
    return normalized or None


def _resolve_executable(name: str) -> str | None:
    direct = shutil.which(name)
    if direct:
        return direct
    if os.name == "nt":
        cmd_variant = shutil.which(f"{name}.cmd")
        if cmd_variant:
            return cmd_variant
        exe_variant = shutil.which(f"{name}.exe")
        if exe_variant:
            return exe_variant
    return None


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}