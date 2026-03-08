from __future__ import annotations

import fnmatch
from pathlib import PurePosixPath
from typing import Any


DEFAULT_EDIT_DENY_GLOBS = [".git/**"]


def normalize_policy_globs(patterns: list[str] | tuple[str, ...] | None) -> list[str]:
    if not patterns:
        return []
    normalized: list[str] = []
    for pattern in patterns:
        cleaned = str(pattern or "").strip().replace("\\", "/").lstrip("/")
        if cleaned:
            normalized.append(cleaned)
    return normalized


def normalize_relative_path(file_path: str) -> str:
    return PurePosixPath(str(file_path).replace("\\", "/")).as_posix().lstrip("/")


def summarize_edit_policy(allow_globs: list[str] | tuple[str, ...], deny_globs: list[str] | tuple[str, ...]) -> dict[str, Any]:
    normalized_allow = normalize_policy_globs(list(allow_globs))
    normalized_deny = normalize_policy_globs(list(deny_globs))
    return {
        "allow_globs": normalized_allow,
        "deny_globs": normalized_deny,
        "has_allowlist": bool(normalized_allow),
        "has_denylist": bool(normalized_deny),
    }


def evaluate_edit_path(
    file_path: str,
    allow_globs: list[str] | tuple[str, ...],
    deny_globs: list[str] | tuple[str, ...],
) -> tuple[bool, str | None]:
    normalized_path = normalize_relative_path(file_path)
    normalized_allow = normalize_policy_globs(list(allow_globs))
    normalized_deny = normalize_policy_globs(list(deny_globs))

    for pattern in normalized_deny:
        if fnmatch.fnmatchcase(normalized_path, pattern):
            return False, f"matched deny rule: {pattern}"

    if normalized_allow:
        for pattern in normalized_allow:
            if fnmatch.fnmatchcase(normalized_path, pattern):
                return True, None
        return False, "outside allowed edit paths"

    return True, None


def filter_edit_paths(
    file_paths: list[str],
    allow_globs: list[str] | tuple[str, ...],
    deny_globs: list[str] | tuple[str, ...],
) -> tuple[list[str], list[dict[str, str]]]:
    allowed: list[str] = []
    blocked: list[dict[str, str]] = []
    seen: set[str] = set()
    for file_path in file_paths:
        normalized_path = normalize_relative_path(file_path)
        if normalized_path in seen:
            continue
        seen.add(normalized_path)
        is_allowed, reason = evaluate_edit_path(normalized_path, allow_globs, deny_globs)
        if is_allowed:
            allowed.append(normalized_path)
        else:
            blocked.append({"file_path": normalized_path, "reason": reason or "blocked by file edit policy"})
    return allowed, blocked