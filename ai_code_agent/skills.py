from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


_FRONTMATTER_PATTERN = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.DOTALL)
_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_\-/+.#]*")
_SEMVER_PATTERN = re.compile(r"^\d+\.\d+\.\d+(?:[-+][A-Za-z0-9._-]+)?$")
_VALID_PERMISSIONS = {"read-only", "codegen", "sandbox", "publish"}
_VALID_SANDBOX_VALUES = {"optional", "required", "none"}
_LIST_FRONTMATTER_FIELDS = {"tags", "triggers", "frameworks", "required"}


class SkillManifestError(ValueError):
    def __init__(self, skill_path: str, errors: list[str]) -> None:
        self.skill_path = skill_path
        self.errors = errors
        joined = "; ".join(errors)
        super().__init__(f"Invalid skill manifest at {skill_path}: {joined}")


@dataclass(frozen=True)
class SkillDefinition:
    name: str
    version: str
    title: str
    description: str
    path: str
    tags: list[str]
    triggers: list[str]
    frameworks: list[str]
    permission: str
    sandbox: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    instructions: str

    def planning_summary(self, *, score: int, reasons: list[str]) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "title": self.title,
            "description": self.description,
            "path": self.path,
            "tags": self.tags,
            "frameworks": self.frameworks,
            "permission": self.permission,
            "sandbox": self.sandbox,
            "score": score,
            "reasons": reasons,
        }

    def prompt_payload(self, *, score: int, reasons: list[str]) -> dict[str, Any]:
        payload = self.planning_summary(score=score, reasons=reasons)
        payload["input_schema"] = self.input_schema
        payload["output_schema"] = self.output_schema
        payload["instructions"] = self.instructions
        return payload


def discover_local_skills(workspace_dir: str, registry_paths: list[str]) -> list[SkillDefinition]:
    skills: list[SkillDefinition] = []
    seen_paths: set[str] = set()

    for registry_path in registry_paths:
        resolved_registry = _resolve_registry_path(workspace_dir, registry_path)
        if resolved_registry is None or not resolved_registry.exists() or not resolved_registry.is_dir():
            continue
        for skill_file in sorted(resolved_registry.glob("*/SKILL.md")):
            normalized_path = skill_file.resolve().as_posix()
            if normalized_path in seen_paths:
                continue
            seen_paths.add(normalized_path)
            skill = _load_skill_file(workspace_dir, skill_file)
            if skill is not None:
                skills.append(skill)

    return skills


def select_skills(
    skills: list[SkillDefinition],
    issue: str,
    workspace_profile: dict[str, Any],
    *,
    limit: int = 3,
) -> list[dict[str, Any]]:
    issue_text = issue.lower()
    issue_tokens = set(_tokenize(issue_text))
    workspace_frameworks = _workspace_frameworks(workspace_profile)
    matches: list[tuple[int, SkillDefinition, list[str]]] = []

    for skill in skills:
        score = 0
        reasons: list[str] = []
        matched_phrases: set[str] = set()
        matched_frameworks: set[str] = set()

        for phrase in skill.triggers:
            normalized_phrase = phrase.strip().lower()
            if not normalized_phrase:
                continue
            phrase_tokens = set(_tokenize(normalized_phrase))
            if " " in normalized_phrase:
                if normalized_phrase in issue_text:
                    matched_phrases.add(normalized_phrase)
            elif normalized_phrase in issue_tokens or phrase_tokens.intersection(issue_tokens):
                matched_phrases.add(normalized_phrase)

        for tag in skill.tags:
            normalized_tag = tag.strip().lower()
            if not normalized_tag:
                continue
            tag_tokens = set(_tokenize(normalized_tag))
            if normalized_tag in issue_tokens or tag_tokens.intersection(issue_tokens):
                matched_phrases.add(normalized_tag)

        for framework in skill.frameworks:
            normalized_framework = framework.strip().lower()
            if normalized_framework and normalized_framework in workspace_frameworks:
                matched_frameworks.add(normalized_framework)

        if matched_phrases:
            score += len(matched_phrases) * 3
            reasons.append(f"Issue matched: {', '.join(sorted(matched_phrases)[:4])}")
        if matched_frameworks:
            score += len(matched_frameworks) * 2
            reasons.append(f"Workspace matched: {', '.join(sorted(matched_frameworks)[:4])}")

        if score <= 0:
            continue
        matches.append((score, skill, reasons))

    matches.sort(key=lambda item: (-item[0], item[1].name))
    return [
        skill.prompt_payload(score=score, reasons=reasons)
        for score, skill, reasons in matches[: max(0, limit)]
    ]


def partition_skills_by_permission(
    skills: list[dict[str, Any]],
    allowed_permissions: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    allowed = {item.strip().lower() for item in allowed_permissions if isinstance(item, str) and item.strip()}
    permitted: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []

    for skill in skills:
        permission = skill.get("permission") if isinstance(skill, dict) else None
        normalized_permission = permission.strip().lower() if isinstance(permission, str) and permission.strip() else "read-only"
        if not allowed or normalized_permission in allowed:
            permitted.append(skill)
            continue
        blocked.append(
            {
                **skill,
                "blocked_reason": f"permission_not_allowed:{normalized_permission}",
            }
        )

    return permitted, blocked


def _resolve_registry_path(workspace_dir: str, registry_path: str) -> Path | None:
    if not registry_path:
        return None
    candidate = Path(registry_path)
    if not candidate.is_absolute():
        candidate = Path(workspace_dir) / candidate
    return candidate


def _load_skill_file(workspace_dir: str, skill_file: Path) -> SkillDefinition | None:
    try:
        raw_text = skill_file.read_text(encoding="utf-8")
    except OSError:
        return None

    metadata, body = _parse_frontmatter(raw_text)
    relative_path = skill_file.resolve().relative_to(Path(workspace_dir).resolve()).as_posix()
    _validate_skill_manifest(relative_path, metadata, body)
    name = _string_value(metadata.get("name")) or skill_file.parent.name
    version = _string_value(metadata.get("version")) or "0.0.0"
    title = _string_value(metadata.get("title")) or name.replace("-", " ").replace("_", " ").title()
    description = _string_value(metadata.get("description")) or _first_sentence(body) or title
    tags = _list_value(metadata.get("tags"))
    triggers = _list_value(metadata.get("triggers"))
    frameworks = _list_value(metadata.get("frameworks"))
    permission = _string_value(metadata.get("permission")) or "read-only"
    sandbox = _string_value(metadata.get("sandbox")) or "optional"
    input_schema = _dict_value(metadata.get("input_schema"))
    output_schema = _dict_value(metadata.get("output_schema"))
    instructions = body.strip()

    return SkillDefinition(
        name=name,
        version=version,
        title=title,
        description=description,
        path=relative_path,
        tags=tags,
        triggers=triggers,
        frameworks=frameworks,
        permission=permission,
        sandbox=sandbox,
        input_schema=input_schema,
        output_schema=output_schema,
        instructions=instructions,
    )


def _validate_skill_manifest(skill_path: str, metadata: dict[str, Any], body: str) -> None:
    errors: list[str] = []

    name = _string_value(metadata.get("name"))
    version = _string_value(metadata.get("version"))
    description = _string_value(metadata.get("description"))
    permission = _string_value(metadata.get("permission"))
    sandbox = _string_value(metadata.get("sandbox")) or "optional"
    input_schema = _dict_value(metadata.get("input_schema"))
    output_schema = _dict_value(metadata.get("output_schema"))

    if not name:
        errors.append("missing required field 'name'")
    if not version:
        errors.append("missing required field 'version'")
    elif not _SEMVER_PATTERN.match(version):
        errors.append("field 'version' must use semver, for example 0.1.0")
    if not description:
        errors.append("missing required field 'description'")
    if not permission:
        errors.append("missing required field 'permission'")
    elif permission not in _VALID_PERMISSIONS:
        errors.append(f"field 'permission' must be one of {sorted(_VALID_PERMISSIONS)}")
    if sandbox not in _VALID_SANDBOX_VALUES:
        errors.append(f"field 'sandbox' must be one of {sorted(_VALID_SANDBOX_VALUES)}")
    if not input_schema:
        errors.append("missing required field 'input_schema' as a JSON object")
    elif input_schema.get("type") != "object":
        errors.append("field 'input_schema.type' must be 'object'")
    if not output_schema:
        errors.append("missing required field 'output_schema' as a JSON object")
    elif output_schema.get("type") != "object":
        errors.append("field 'output_schema.type' must be 'object'")
    if not body.strip():
        errors.append("skill instructions body must not be empty")

    if errors:
        raise SkillManifestError(skill_path, errors)


def _parse_frontmatter(raw_text: str) -> tuple[dict[str, Any], str]:
    match = _FRONTMATTER_PATTERN.match(raw_text.strip())
    if not match:
        return {}, raw_text

    metadata_block, body = match.groups()
    metadata: dict[str, Any] = {}
    current_list_key: str | None = None

    for raw_line in metadata_block.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("- ") and current_list_key:
            metadata.setdefault(current_list_key, [])
            metadata[current_list_key].append(stripped[2:].strip())
            continue
        if ":" not in stripped:
            current_list_key = None
            continue

        key, value = stripped.split(":", 1)
        normalized_key = key.strip().lower().replace("-", "_")
        normalized_value = value.strip()
        if not normalized_value:
            metadata[normalized_key] = []
            current_list_key = normalized_key
            continue

        metadata[normalized_key] = _parse_frontmatter_value(normalized_key, normalized_value)
        current_list_key = None

    return metadata, body


def _parse_frontmatter_value(key: str, value: str) -> Any:
    stripped = value.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            return stripped
        return parsed if isinstance(parsed, dict) else stripped
    if stripped.startswith("[") and stripped.endswith("]"):
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            inner = stripped[1:-1].strip()
            if not inner:
                return []
            return [item.strip().strip('"\'') for item in inner.split(",") if item.strip()]
        return parsed if isinstance(parsed, list) else stripped
    if key in _LIST_FRONTMATTER_FIELDS and "," in stripped:
        return [item.strip().strip('"\'') for item in stripped.split(",") if item.strip()]
    return stripped.strip('"\'')


def _list_value(value: Any) -> list[str]:
    if isinstance(value, list):
        return [item.strip() for item in value if isinstance(item, str) and item.strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _string_value(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _first_sentence(value: str) -> str | None:
    normalized = re.sub(r"\s+", " ", value).strip()
    if not normalized:
        return None
    sentence, _, _ = normalized.partition(".")
    return sentence.strip() if sentence.strip() else normalized


def _tokenize(value: str) -> list[str]:
    return [token.lower() for token in _TOKEN_PATTERN.findall(value.lower())]


def _workspace_frameworks(workspace_profile: dict[str, Any]) -> set[str]:
    frameworks = {
        item.strip().lower()
        for item in workspace_profile.get("frameworks", [])
        if isinstance(item, str) and item.strip()
    } if isinstance(workspace_profile, dict) else set()
    if isinstance(workspace_profile, dict) and workspace_profile.get("nextjs"):
        frameworks.add("nextjs")
    if isinstance(workspace_profile, dict) and workspace_profile.get("nestjs"):
        frameworks.add("nestjs")
    return frameworks