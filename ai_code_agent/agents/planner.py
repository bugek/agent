import json
import re
from collections import defaultdict
from typing import Any
from pathlib import Path

from ai_code_agent.agents.base import BaseAgent
from ai_code_agent.orchestrator import AgentState
from ai_code_agent.llm.prompts import ANALYSIS_SYSTEM_PROMPT, PLAN_SYSTEM_PROMPT, PLANNER_SYSTEM_PROMPT, SCOPE_SYSTEM_PROMPT
from ai_code_agent.skills import discover_local_skills, partition_skills_by_permission, select_skills
from ai_code_agent.tools.file_editor import FileEditor
from ai_code_agent.tools.code_search import CodeSearch
from ai_code_agent.tools.edit_policy import filter_edit_paths, summarize_edit_policy
from ai_code_agent.tools.version_resolution import resolve_workspace_version_context
from ai_code_agent.tools.workspace_profile import detect_workspace_profile

class PlannerAgent(BaseAgent):
    """
    Agent responsible for analyzing the issue, 
    searching the codebase, and formulating a plan.
    """
    
    def run(self, state: AgentState) -> dict:
        """
        Analyzes the issue description, searches code context,
        and outputs a high-level plan and target files to edit.
        """
        scope_result = ScopeAgent(self.config, self.llm).run(state)
        scoped_state = dict(state)
        scoped_state.update(scope_result)
        analysis_result = AnalysisAgent(self.config, self.llm).run(scoped_state)
        analyzed_state = dict(scoped_state)
        analyzed_state.update(analysis_result)
        return PlanAgent(self.config, self.llm).run(analyzed_state)

    def _available_skills(self, workspace_dir: str):
        if not self.config.skills_enabled:
            return []
        return discover_local_skills(workspace_dir, self.config.skill_registry_paths)

    def _planning_skill_summary(self, skill: object) -> dict[str, object]:
        if not isinstance(skill, dict):
            return {}
        return {
            "name": skill.get("name"),
            "version": skill.get("version"),
            "title": skill.get("title"),
            "description": skill.get("description"),
            "path": skill.get("path"),
            "permission": skill.get("permission"),
            "sandbox": skill.get("sandbox"),
            "score": skill.get("score"),
            "reasons": skill.get("reasons") if isinstance(skill.get("reasons"), list) else [],
            "blocked_reason": skill.get("blocked_reason") if isinstance(skill.get("blocked_reason"), str) else None,
        }

    def _planning_skill_invocation_summary(self, skill: object, *, outcome: str, phase: str = "plan") -> dict[str, object]:
        if not isinstance(skill, dict):
            return {}
        return {
            "name": skill.get("name"),
            "version": skill.get("version"),
            "title": skill.get("title"),
            "phase": phase,
            "outcome": outcome,
            "permission": skill.get("permission"),
            "sandbox": skill.get("sandbox"),
            "blocked_reason": skill.get("blocked_reason") if isinstance(skill.get("blocked_reason"), str) else None,
        }

    def _rank_candidate_files(
        self,
        search: CodeSearch,
        workspace_dir: str,
        workspace_profile: dict,
        keywords: list[str],
        retrieval_mode: str,
    ) -> list[tuple[str, int]]:
        scored_files = self._score_candidate_files(search, keywords)
        if retrieval_mode != "baseline":
            scored_files = self._scale_scores(scored_files, 0.6)
            scored_files = self._merge_scored_files(scored_files, search.hybrid_search(keywords, workspace_profile))
        scored_files = self._merge_scored_files(scored_files, self._score_nextjs_candidates(workspace_dir, workspace_profile, keywords))
        scored_files = self._merge_scored_files(scored_files, self._score_nestjs_candidates(workspace_profile, keywords))
        if retrieval_mode == "hybrid":
            seed_files = self._graph_seed_files(search, scored_files, keywords)
            scored_files = self._merge_scored_files(scored_files, search.graph_related_files(seed_files, keywords))
            scored_files = self._rerank_hybrid_scores(search, scored_files, keywords)
        return scored_files

    def _normalized_retrieval_mode(self) -> str:
        retrieval_mode = (self.config.retrieval_mode or "hybrid").strip().lower()
        if retrieval_mode in {"baseline", "hybrid"}:
            return retrieval_mode
        return "hybrid"

    def _extract_keywords(self, issue: str) -> list[str]:
        words = re.findall(r"[A-Za-z_][A-Za-z0-9_\-]+", issue.lower())
        stop_words = {
            "the",
            "and",
            "for",
            "with",
            "that",
            "this",
            "from",
            "make",
            "into",
            "agent",
            "code",
            "update",
            "fix",
            "revamp",
            "current",
        }
        return [self._normalize_keyword(word) for word in words if len(word) > 2 and self._normalize_keyword(word) not in stop_words]

    def _fallback_plan(self, issue: str, candidate_files: list[str]) -> str:
        steps = [
            f"Review the issue: {issue}",
            "Inspect the most relevant files and determine the smallest safe implementation change.",
            "Apply the code changes and run smoke tests.",
        ]
        if candidate_files:
            steps.insert(1, f"Start with: {', '.join(candidate_files[:5])}")
        return "\n".join(f"- {step}" for step in steps)

    def _planning_remediation_context(self, state: AgentState) -> dict[str, object] | None:
        if int(state.get("retry_count", 0) or 0) <= 0:
            return None

        review_summary = state.get("review_summary") if isinstance(state.get("review_summary"), dict) else {}
        remediation = review_summary.get("remediation") if isinstance(review_summary.get("remediation"), dict) else {}
        if not remediation.get("required"):
            return None

        context = {
            "review_status": review_summary.get("status"),
            "failed_validation_labels": [
                label for label in remediation.get("failed_validation_labels", []) if isinstance(label, str) and label
            ],
            "focus_areas": [
                file_path for file_path in remediation.get("focus_areas", []) if isinstance(file_path, str) and file_path
            ],
            "guidance": [
                item for item in remediation.get("guidance", []) if isinstance(item, str) and item
            ],
            "failed_operations": [
                item for item in remediation.get("failed_operations", []) if isinstance(item, str) and item
            ],
            "task_remediation": [
                item for item in remediation.get("task_remediation", [])
                if isinstance(item, dict) and isinstance(item.get("task_id"), str) and item.get("task_id")
            ],
        }
        workspace_profile = detect_workspace_profile(state["workspace_dir"])
        context["focus_areas"] = self._expand_nextjs_route_bundle_files(context["focus_areas"], workspace_profile)
        if not any(context[key] for key in ["failed_validation_labels", "focus_areas", "guidance", "failed_operations", "task_remediation"]):
            return None
        return context

    def _prioritize_remediation_files(
        self,
        candidate_files: list[str],
        remediation_context: dict[str, object] | None,
    ) -> list[str]:
        if not remediation_context:
            return candidate_files[:10]

        prioritized: list[str] = []
        seen: set[str] = set()
        for file_path in remediation_context.get("focus_areas", []):
            if isinstance(file_path, str):
                normalized = file_path.replace("\\", "/")
                if normalized not in seen:
                    prioritized.append(normalized)
                    seen.add(normalized)
        for file_path in candidate_files:
            normalized = file_path.replace("\\", "/")
            if normalized not in seen:
                prioritized.append(normalized)
                seen.add(normalized)
        return prioritized[:10]

    def _normalize_edit_intent(
        self,
        raw_edit_intent: object,
        files_to_edit: list[str],
        remediation_context: dict[str, object] | None,
    ) -> list[dict[str, object]]:
        intents: list[dict[str, object]] = []
        if isinstance(raw_edit_intent, list):
            for item in raw_edit_intent:
                if not isinstance(item, dict):
                    continue
                file_path = item.get("file_path")
                if not isinstance(file_path, str) or not file_path:
                    continue
                normalized: dict[str, object] = {"file_path": file_path.replace("\\", "/")}
                for key in ["intent", "reason"]:
                    value = item.get(key)
                    if isinstance(value, str) and value:
                        normalized[key] = value
                if isinstance(item.get("validation_targets"), list):
                    validation_targets = [
                        label for label in item.get("validation_targets", []) if isinstance(label, str) and label
                    ]
                    if validation_targets:
                        normalized["validation_targets"] = validation_targets
                intents.append(normalized)

        if intents:
            return self._expand_nextjs_edit_intent(intents)[:10]

        fallback_targets = [file_path.replace("\\", "/") for file_path in files_to_edit if isinstance(file_path, str) and file_path]
        validation_targets = remediation_context.get("failed_validation_labels", []) if remediation_context else []
        guidance = remediation_context.get("guidance", []) if remediation_context else []
        for file_path in fallback_targets[:5]:
            fallback_intent: dict[str, object] = {
                "file_path": file_path,
                "intent": "Address follow-up issues from the previous review cycle." if remediation_context else "Implement the requested change safely.",
            }
            if validation_targets:
                fallback_intent["validation_targets"] = [
                    label for label in validation_targets if isinstance(label, str) and label
                ][:5]
            if guidance:
                fallback_intent["reason"] = guidance[0]
            intents.append(fallback_intent)
        return self._expand_nextjs_edit_intent(intents)

    def _normalize_scope(
        self,
        raw_scope: object,
        files_to_edit: list[str],
    ) -> dict[str, list[str]]:
        def scope_path_key(value: str) -> str:
            return value.replace("\\", "/").strip().rstrip("/")

        def append_unique(items: list[str], seen: set[str], value: str) -> None:
            normalized = value.replace("\\", "/").strip()
            key = scope_path_key(normalized)
            if not key or key in seen:
                return
            seen.add(key)
            items.append(normalized)

        scope: dict[str, list[str]] = {"in_scope": [], "out_of_scope": []}
        in_scope_seen: set[str] = set()
        out_of_scope_seen: set[str] = set()
        if isinstance(raw_scope, dict):
            for key in ["in_scope", "out_of_scope"]:
                items = raw_scope.get(key)
                if isinstance(items, list):
                    for item in items:
                        if not isinstance(item, str) or not item.strip():
                            continue
                        if key == "in_scope":
                            append_unique(scope[key], in_scope_seen, item)
                        else:
                            append_unique(scope[key], out_of_scope_seen, item)
        if not scope["in_scope"] and files_to_edit:
            for file_path in files_to_edit:
                if isinstance(file_path, str) and file_path:
                    append_unique(scope["in_scope"], in_scope_seen, file_path)
        elif files_to_edit:
            for file_path in files_to_edit:
                if isinstance(file_path, str) and file_path:
                    append_unique(scope["in_scope"], in_scope_seen, file_path)

        scope["out_of_scope"] = [
            item for item in scope["out_of_scope"]
            if scope_path_key(item) not in in_scope_seen
        ]
        return scope

    def _normalize_tasks(
        self,
        raw_tasks: object,
        files_to_edit: list[str],
        failed_task_ids: list[str] | None = None,
    ) -> list[dict[str, object]]:
        tasks: list[dict[str, object]] = []
        if not isinstance(raw_tasks, list):
            return self._fallback_tasks(files_to_edit)

        for item in raw_tasks:
            if not isinstance(item, dict):
                continue
            task_id = item.get("id")
            if not isinstance(task_id, str) or not task_id.strip():
                continue
            task: dict[str, object] = {"id": task_id.strip()}
            for key in ["title", "goal"]:
                value = item.get(key)
                if isinstance(value, str) and value.strip():
                    task[key] = value.strip()
            if not task.get("title"):
                continue
            target_files = item.get("target_files")
            if isinstance(target_files, list):
                task["target_files"] = [
                    f.replace("\\", "/")
                    for f in target_files
                    if isinstance(f, str) and f.strip()
                ]
            else:
                task["target_files"] = []
            acceptance_checks = item.get("acceptance_checks")
            if isinstance(acceptance_checks, list):
                task["acceptance_checks"] = [
                    c for c in acceptance_checks if isinstance(c, str) and c.strip()
                ]
            else:
                task["acceptance_checks"] = []
            if failed_task_ids and task_id.strip() not in failed_task_ids:
                task["status"] = "completed"
            else:
                task["status"] = "pending"
            tasks.append(task)

        return tasks if tasks else self._fallback_tasks(files_to_edit)

    def _fallback_tasks(self, files_to_edit: list[str]) -> list[dict[str, object]]:
        if not files_to_edit:
            return []
        return [{
            "id": "T1",
            "title": "Implement the requested change",
            "goal": "Apply the necessary code changes to the target files.",
            "target_files": [f.replace("\\", "/") for f in files_to_edit[:5] if isinstance(f, str)],
            "acceptance_checks": ["typecheck", "build"],
            "status": "pending",
        }]

    def _prioritize_version_resolution_files(
        self,
        files_to_edit: list[str],
        workspace_profile: dict[str, object],
        version_resolution: dict[str, object] | None,
    ) -> list[str]:
        if not version_resolution:
            return files_to_edit

        prioritized: list[str] = []
        seen: set[str] = set()

        def add(file_path: str) -> None:
            normalized = file_path.replace("\\", "/")
            if normalized not in seen and (Path(self.config.workspace_dir) / normalized).exists():
                prioritized.append(normalized)
                seen.add(normalized)

        add("package.json")
        if version_resolution.get("requires_version_display"):
            next_profile = workspace_profile.get("nextjs") if isinstance(workspace_profile.get("nextjs"), dict) else {}
            router_type = next_profile.get("router_type")
            if router_type == "app":
                app_dir = str(next_profile.get("app_dir") or "app")
                add(f"{app_dir}/layout.tsx")
            elif router_type == "pages":
                pages_dir = str(next_profile.get("pages_dir") or "pages")
                add(f"{pages_dir}/_app.tsx")

        for file_path in files_to_edit:
            if isinstance(file_path, str):
                add(file_path)
        return prioritized[:10]

    def _expand_nextjs_edit_intent(self, intents: list[dict[str, object]]) -> list[dict[str, object]]:
        workspace_profile = detect_workspace_profile(self.config.workspace_dir)
        expanded_files = self._expand_nextjs_route_bundle_files(
            [item.get("file_path", "") for item in intents if isinstance(item.get("file_path"), str)],
            workspace_profile,
        )
        by_file: dict[str, dict[str, object]] = {}
        for item in intents:
            file_path = item.get("file_path")
            if isinstance(file_path, str) and file_path:
                by_file[file_path.replace("\\", "/")] = dict(item)

        template = intents[0] if intents else {}
        for file_path in expanded_files:
            if file_path not in by_file:
                synthesized: dict[str, object] = {"file_path": file_path}
                for key in ["intent", "reason", "validation_targets"]:
                    value = template.get(key)
                    if value:
                        synthesized[key] = value
                by_file[file_path] = synthesized

        ordered: list[dict[str, object]] = []
        for file_path in expanded_files:
            item = by_file.get(file_path)
            if item:
                ordered.append(item)
        return ordered

    def _expand_nextjs_route_bundle_files(self, file_paths: list[str], workspace_profile: dict) -> list[str]:
        nextjs_profile = workspace_profile.get("nextjs") if isinstance(workspace_profile, dict) else None
        app_dir = "app"
        if isinstance(nextjs_profile, dict) and nextjs_profile.get("router_type") == "app":
            app_dir = (nextjs_profile.get("app_dir") or "app").replace("\\", "/")
        elif not any(
            isinstance(file_path, str)
            and file_path.replace("\\", "/").startswith("app/")
            and Path(file_path.replace("\\", "/")).name in {"page.tsx", "loading.tsx", "error.tsx"}
            for file_path in file_paths
        ):
            return [file_path.replace("\\", "/") for file_path in file_paths if isinstance(file_path, str)]

        expanded: list[str] = []
        seen: set[str] = set()
        for file_path in file_paths:
            if not isinstance(file_path, str) or not file_path:
                continue
            normalized = file_path.replace("\\", "/")
            candidates = [normalized]
            route_dir = self._next_route_dir_for_file(normalized, app_dir)
            if route_dir is not None:
                candidates.extend([f"{route_dir}/page.tsx", f"{route_dir}/loading.tsx", f"{route_dir}/error.tsx"])
            for candidate in candidates:
                if candidate not in seen:
                    expanded.append(candidate)
                    seen.add(candidate)
        return expanded

    def _expand_nextjs_scaffold_target_files(
        self,
        state: AgentState,
        file_paths: list[str],
        workspace_profile: dict,
    ) -> list[str]:
        nextjs_profile = workspace_profile.get("nextjs") if isinstance(workspace_profile, dict) else None
        if not isinstance(nextjs_profile, dict):
            return [file_path.replace("\\", "/") for file_path in file_paths if isinstance(file_path, str) and file_path]

        from ai_code_agent.agents.coder import CoderAgent

        predicted_state = dict(state)
        predicted_state["files_to_edit"] = [
            file_path.replace("\\", "/") for file_path in file_paths if isinstance(file_path, str) and file_path
        ]
        coder = CoderAgent(self.config, self.llm)
        operations = coder._build_nextjs_operations(
            predicted_state,
            workspace_profile,
            FileEditor(state["workspace_dir"]),
        )
        anchored_route_dirs = self._anchored_nextjs_route_dirs(predicted_state["files_to_edit"], workspace_profile)

        expanded: list[str] = []
        seen: set[str] = set()
        for file_path in predicted_state["files_to_edit"]:
            if file_path not in seen:
                expanded.append(file_path)
                seen.add(file_path)
        for operation in operations:
            file_path = operation.get("file_path")
            if isinstance(file_path, str):
                normalized = file_path.replace("\\", "/")
                if not self._is_allowed_nextjs_scaffold_target(normalized, workspace_profile, anchored_route_dirs):
                    continue
                if normalized not in seen:
                    expanded.append(normalized)
                    seen.add(normalized)
        return expanded

    def _anchored_nextjs_route_dirs(self, file_paths: list[str], workspace_profile: dict) -> set[str]:
        nextjs_profile = workspace_profile.get("nextjs") if isinstance(workspace_profile, dict) else None
        if not isinstance(nextjs_profile, dict):
            return set()
        app_dir = (nextjs_profile.get("app_dir") or "app").replace("\\", "/")
        anchored: set[str] = set()
        for file_path in file_paths:
            if not isinstance(file_path, str) or not file_path:
                continue
            normalized = file_path.replace("\\", "/").rstrip("/")
            if normalized in {app_dir, f"{app_dir}/page.tsx", f"{app_dir}/layout.tsx", f"{app_dir}/loading.tsx", f"{app_dir}/error.tsx"}:
                anchored.add(app_dir)
                continue
            if normalized.startswith(f"{app_dir}/"):
                top_segment = normalized[len(app_dir) + 1:].split("/", 1)[0]
                if top_segment:
                    anchored.add(f"{app_dir}/{top_segment}")
        return anchored

    def _is_allowed_nextjs_scaffold_target(self, file_path: str, workspace_profile: dict, anchored_route_dirs: set[str]) -> bool:
        if not anchored_route_dirs:
            return True
        nextjs_profile = workspace_profile.get("nextjs") if isinstance(workspace_profile, dict) else None
        if not isinstance(nextjs_profile, dict):
            return True
        app_dir = (nextjs_profile.get("app_dir") or "app").replace("\\", "/")
        normalized = file_path.replace("\\", "/")
        if not normalized.startswith(f"{app_dir}/"):
            return True
        if normalized in {f"{app_dir}/page.tsx", f"{app_dir}/layout.tsx", f"{app_dir}/loading.tsx", f"{app_dir}/error.tsx"}:
            return app_dir in anchored_route_dirs
        top_segment = normalized[len(app_dir) + 1:].split("/", 1)[0]
        if not top_segment:
            return True
        return f"{app_dir}/{top_segment}" in anchored_route_dirs

    def _next_route_dir_for_file(self, file_path: str, app_dir: str) -> str | None:
        normalized = file_path.replace("\\", "/")
        file_name = Path(normalized).name
        if normalized in {f"{app_dir}/page.tsx", f"{app_dir}/loading.tsx", f"{app_dir}/error.tsx"}:
            return app_dir
        if not normalized.startswith(f"{app_dir}/"):
            return None
        if file_name not in {"page.tsx", "loading.tsx", "error.tsx"}:
            return None
        return normalized.rsplit("/", 1)[0]

    def _extract_design_brief(self, issue: str, workspace_profile: dict) -> dict[str, object] | None:
        lower_issue = issue.lower()
        is_frontend_request = bool(
            workspace_profile.get("nextjs")
            or re.search(r"\b(next|frontend|page|layout|component|hero|section|dashboard|screen|view|ui|visual)\b", lower_issue)
        )
        if not is_frontend_request:
            return None

        if re.search(r"\b(dashboard|analytics|metric|report|signal)\b", lower_issue):
            style_family = "dashboard"
        elif re.search(r"\b(profile|account|auth|login|setting|minimal|calm|quiet|clean)\b", lower_issue):
            style_family = "calm"
        else:
            style_family = "editorial"

        visual_tone = None
        for tone in ["signal-rich", "calm", "minimal", "bold", "editorial", "immersive", "quiet"]:
            if tone in lower_issue:
                visual_tone = tone
                break

        if re.search(r"\b(cool|teal|slate|blue|mint)\b", lower_issue):
            palette_hint = "cool"
        elif re.search(r"\b(warm|amber|sand|gold|terracotta)\b", lower_issue):
            palette_hint = "warm"
        elif re.search(r"\b(neutral|mono|monochrome|stone)\b", lower_issue):
            palette_hint = "neutral"
        else:
            palette_hint = "cool" if style_family == "calm" else "warm"

        return {
            "style_family": style_family,
            "visual_tone": visual_tone,
            "palette_hint": palette_hint,
            "state_coverage": ["loading", "empty", "error", "success"],
            "source": "issue_keywords",
        }

    def _score_candidate_files(self, search: CodeSearch, keywords: list[str]) -> list[tuple[str, int]]:
        scores: dict[str, int] = defaultdict(int)
        for keyword in keywords[:8]:
            for match in search.search_text(keyword)[:8]:
                file_path = match.split(":", 1)[0].replace("\\", "/")
                if self._skip_file(file_path):
                    continue
                scores[file_path] += 2
            for match in search.search_symbol(keyword)[:4]:
                file_path = match.split(":", 1)[0].replace("\\", "/")
                if self._skip_file(file_path):
                    continue
                scores[file_path] += 3

        ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        return ranked

    def _skip_file(self, file_path: str) -> bool:
        return (
            file_path.startswith("artifact/")
            or file_path.startswith(".git/")
            or file_path.startswith(".next/")
        )

    def _prioritize_profile_files(self, candidate_files: list[str], workspace_profile: dict) -> list[str]:
        prioritized: list[str] = []
        seen: set[str] = set()
        workspace_root = Path(self.config.workspace_dir)

        for file_path in workspace_profile.get("priority_files", []):
            normalized = file_path.replace("\\", "/")
            if normalized not in seen and (workspace_root / normalized).exists():
                prioritized.append(normalized)
                seen.add(normalized)

        for file_path in candidate_files:
            if file_path not in seen:
                prioritized.append(file_path)
                seen.add(file_path)

        return prioritized[:10]

    def _expand_related_files(self, search: CodeSearch, candidate_files: list[str], keywords: list[str], retrieval_mode: str) -> list[str]:
        if not candidate_files:
            return []

        related_files = [file_path for file_path, _ in search.related_files(candidate_files[:5])[:5]]
        graph_files: list[str] = []
        if retrieval_mode == "hybrid":
            graph_files = [
                file_path
                for file_path, _ in search.graph_related_files(
                    self._graph_seed_files(search, [(file_path, 0) for file_path in candidate_files[:5]], keywords),
                    keywords,
                )[:5]
            ]
        expanded: list[str] = []
        seen: set[str] = set()
        for file_path in [*candidate_files, *related_files, *graph_files]:
            if file_path not in seen:
                expanded.append(file_path)
                seen.add(file_path)
        return expanded[:10]

    def _graph_seed_files(self, search: CodeSearch, scored_files: list[tuple[str, int]], keywords: list[str]) -> list[str]:
        indexed_map = {indexed_file.path: indexed_file for indexed_file in search.build_index()}
        keyword_set = set(keywords)
        preferred_kinds = {
            "code",
            "nest-module",
            "nest-controller",
            "nest-service",
            "nest-dto",
            "next-route",
            "next-layout",
            "next-component",
            "api-route",
        }

        highly_relevant: list[str] = []
        prioritized: list[str] = []
        fallback: list[str] = []
        for file_path, _ in scored_files[:10]:
            indexed_file = indexed_map.get(file_path)
            if indexed_file is None:
                continue
            direct_relevance = bool(keyword_set.intersection(indexed_file.path_tokens)) or bool(keyword_set.intersection(indexed_file.symbols))
            if indexed_file.kind in preferred_kinds and direct_relevance:
                highly_relevant.append(file_path)
            elif indexed_file.kind in preferred_kinds:
                prioritized.append(file_path)
            else:
                fallback.append(file_path)

        selected = highly_relevant[:5]
        if len(selected) < 5:
            selected.extend(file_path for file_path in prioritized if file_path not in selected)
        if len(selected) < 3:
            selected.extend(file_path for file_path in fallback if file_path not in selected)
        return selected[:5]

    def _normalize_plan(self, plan: object) -> str:
        if isinstance(plan, str):
            return plan
        if isinstance(plan, list):
            return "\n".join(f"- {item}" for item in plan if isinstance(item, str))
        return ""

    def _score_nextjs_candidates(self, workspace_dir: str, workspace_profile: dict, keywords: list[str]) -> list[tuple[str, int]]:
        nextjs_profile = workspace_profile.get("nextjs")
        if not nextjs_profile:
            return []

        root = Path(workspace_dir)
        scores: dict[str, int] = defaultdict(int)
        normalized_keywords = [keyword.lower() for keyword in keywords]

        for file_path in nextjs_profile.get("route_files", []):
            score = self._score_path_keywords(file_path, normalized_keywords)
            if file_path.endswith(("page.tsx", "page.ts", "page.jsx", "page.js", "index.tsx", "index.ts", "index.jsx", "index.js")):
                score += 3
            if score:
                scores[file_path] += score

        for file_path in nextjs_profile.get("layout_files", []):
            score = self._score_path_keywords(file_path, normalized_keywords)
            scores[file_path] += score + 2

        for file_path in nextjs_profile.get("special_files", []):
            score = self._score_path_keywords(file_path, normalized_keywords)
            if score:
                scores[file_path] += score + 1

        for file_path in nextjs_profile.get("api_routes", []):
            score = self._score_path_keywords(file_path, normalized_keywords)
            if score:
                scores[file_path] += score + 2

        for directory in nextjs_profile.get("component_directories", []):
            base = root / directory
            for file_path in base.rglob("*"):
                if not file_path.is_file() or file_path.suffix not in {".tsx", ".ts", ".jsx", ".js"}:
                    continue
                relative_path = file_path.relative_to(root).as_posix()
                score = self._score_path_keywords(relative_path, normalized_keywords)
                if score:
                    scores[relative_path] += score + 1

        return sorted(scores.items(), key=lambda item: (-item[1], item[0]))

    def _merge_scored_files(
        self,
        base_scores: list[tuple[str, int]],
        extra_scores: list[tuple[str, int]],
    ) -> list[tuple[str, int]]:
        merged: dict[str, int] = defaultdict(int)
        for file_path, score in [*base_scores, *extra_scores]:
            merged[file_path] += score
        return sorted(merged.items(), key=lambda item: (-item[1], item[0]))

    def _score_path_keywords(self, file_path: str, keywords: list[str]) -> int:
        normalized = file_path.lower()
        score = 0
        for keyword in keywords:
            if keyword in normalized:
                score += 4
        return score

    def _scale_scores(self, scores: list[tuple[str, int]], factor: float) -> list[tuple[str, int]]:
        scaled: list[tuple[str, int]] = []
        for file_path, score in scores:
            scaled_score = max(1, int(round(score * factor)))
            scaled.append((file_path, scaled_score))
        return scaled

    def _rerank_hybrid_scores(self, search: CodeSearch, scored_files: list[tuple[str, int]], keywords: list[str]) -> list[tuple[str, int]]:
        indexed_map = {indexed_file.path: indexed_file for indexed_file in search.build_index()}
        keyword_set = set(keywords)
        adjusted_scores: list[tuple[str, int]] = []

        for file_path, score in scored_files:
            indexed_file = indexed_map.get(file_path)
            if indexed_file is None:
                adjusted_scores.append((file_path, score))
                continue

            direct_path_overlap = len(keyword_set.intersection(indexed_file.path_tokens))
            direct_symbol_overlap = len(keyword_set.intersection(indexed_file.symbols))
            adjusted_score = score + (direct_path_overlap * 4) + (direct_symbol_overlap * 5)

            if direct_path_overlap == 0 and direct_symbol_overlap == 0:
                adjusted_score -= 18
            if indexed_file.kind == "entrypoint" and direct_path_overlap == 0:
                adjusted_score -= 12
            if indexed_file.kind == "config":
                adjusted_score -= 10

            adjusted_scores.append((file_path, adjusted_score))

        return sorted(adjusted_scores, key=lambda item: (-item[1], item[0]))

    def _normalize_keyword(self, word: str) -> str:
        normalized = word.strip().lower()
        if normalized.endswith("ies") and len(normalized) > 4:
            return normalized[:-3] + "y"
        if normalized.endswith("s") and len(normalized) > 3 and not normalized.endswith("ss"):
            return normalized[:-1]
        return normalized

    def _score_nestjs_candidates(self, workspace_profile: dict, keywords: list[str]) -> list[tuple[str, int]]:
        nestjs_profile = workspace_profile.get("nestjs")
        if not nestjs_profile:
            return []

        scores: dict[str, int] = defaultdict(int)
        normalized_keywords = [keyword.lower() for keyword in keywords]
        weighted_groups = [
            (nestjs_profile.get("module_files", []), 3),
            (nestjs_profile.get("controller_files", []), 4),
            (nestjs_profile.get("service_files", []), 4),
            (nestjs_profile.get("dto_files", []), 3),
            (nestjs_profile.get("entity_files", []), 2),
            (nestjs_profile.get("guard_files", []), 2),
            (nestjs_profile.get("pipe_files", []), 2),
            (nestjs_profile.get("interceptor_files", []), 2),
            (nestjs_profile.get("middleware_files", []), 2),
        ]

        for file_group, base_score in weighted_groups:
            for file_path in file_group:
                score = self._score_path_keywords(file_path, normalized_keywords)
                if score or base_score >= 3:
                    scores[file_path] += score + base_score

        main_file = nestjs_profile.get("main_file")
        if main_file:
            scores[main_file] += 2

        app_module_file = nestjs_profile.get("app_module_file")
        if app_module_file:
            scores[app_module_file] += 4

        return sorted(scores.items(), key=lambda item: (-item[1], item[0]))


class ScopeAgent(PlannerAgent):
    """Deterministic scope normalization for a single run."""

    def run(self, state: AgentState) -> dict:
        issue = state["issue_description"]
        remediation_context = self._planning_remediation_context(state)
        workspace_profile = detect_workspace_profile(state["workspace_dir"])
        previous_scope = {}
        planning_context = state.get("planning_context") if isinstance(state.get("planning_context"), dict) else {}
        if isinstance(planning_context.get("scope"), dict):
            previous_scope = planning_context.get("scope") or {}

        fallback_in_scope: list[str] = []
        fallback_out_of_scope: list[str] = []
        for file_path in self._scope_seed_files(issue, workspace_profile):
            if isinstance(file_path, str) and file_path:
                normalized = file_path.replace("\\", "/")
                if normalized not in fallback_in_scope:
                    fallback_in_scope.append(normalized)
        for file_path in remediation_context.get("focus_areas", []) if remediation_context else []:
            if isinstance(file_path, str) and file_path:
                normalized = file_path.replace("\\", "/")
                if normalized not in fallback_in_scope:
                    fallback_in_scope.append(normalized)
        for file_path in previous_scope.get("in_scope", []) if isinstance(previous_scope, dict) else []:
            if isinstance(file_path, str) and file_path:
                normalized = file_path.replace("\\", "/")
                if normalized not in fallback_in_scope:
                    fallback_in_scope.append(normalized)
        for file_path in previous_scope.get("out_of_scope", []) if isinstance(previous_scope, dict) else []:
            if isinstance(file_path, str) and file_path:
                normalized = file_path.replace("\\", "/")
                if normalized not in fallback_out_of_scope:
                    fallback_out_of_scope.append(normalized)

        prompt_payload = {
            "issue": issue,
            "workspace_profile": workspace_profile,
            "retry_count": state.get("retry_count", 0),
            "remediation": remediation_context,
            "failed_task_ids": state.get("failed_task_ids") or [],
            "task_statuses": state.get("task_statuses") or {},
            "existing_scope": previous_scope,
        }
        response = self.llm.generate_json(SCOPE_SYSTEM_PROMPT, json.dumps(prompt_payload, indent=2))
        raw_scope = response.get("scope") if isinstance(response, dict) else None
        fallback_files = fallback_in_scope[:10]
        scope = self._normalize_scope(raw_scope, fallback_files)
        if not scope.get("out_of_scope") and fallback_out_of_scope:
            scope["out_of_scope"] = fallback_out_of_scope[:10]
        assumptions = response.get("assumptions") if isinstance(response, dict) else []
        ambiguities = response.get("ambiguities") if isinstance(response, dict) else []
        goal = response.get("goal") if isinstance(response, dict) else None

        return {
            "scope_context": {
                "status": "ready",
                "goal": goal.strip() if isinstance(goal, str) and goal.strip() else issue.strip(),
                "in_scope": scope.get("in_scope", [])[:10],
                "out_of_scope": scope.get("out_of_scope", [])[:10],
                "assumptions": [item for item in assumptions if isinstance(item, str) and item][:10] if isinstance(assumptions, list) else [],
                "ambiguities": [item for item in ambiguities if isinstance(item, str) and item][:10] if isinstance(ambiguities, list) else [],
                "blocked_reasons": [],
            },
            "file_edit_policy": summarize_edit_policy(self.config.edit_allow_globs, self.config.edit_deny_globs),
        }

    def _scope_seed_files(self, issue: str, workspace_profile: dict) -> list[str]:
        nextjs_profile = workspace_profile.get("nextjs") if isinstance(workspace_profile, dict) else None
        if not isinstance(nextjs_profile, dict):
            return []

        route_slug = self._extract_scope_route_slug(issue)
        if not route_slug:
            return []

        app_dir = (nextjs_profile.get("app_dir") or "app").replace("\\", "/")
        candidate_paths = [
            f"{app_dir}/{route_slug}/page.tsx",
            f"{app_dir}/{route_slug}/layout.tsx",
            f"{app_dir}/{route_slug}/loading.tsx",
            f"{app_dir}/{route_slug}/error.tsx",
        ]

        route_files = [
            file_path for file_path in nextjs_profile.get("route_files", [])
            if isinstance(file_path, str) and file_path
        ]
        layout_files = [
            file_path for file_path in nextjs_profile.get("layout_files", [])
            if isinstance(file_path, str) and file_path
        ]
        special_files = [
            file_path for file_path in nextjs_profile.get("special_files", [])
            if isinstance(file_path, str) and file_path
        ]

        seeded: list[str] = []
        for file_path in [*candidate_paths, *route_files, *layout_files, *special_files]:
            normalized = file_path.replace("\\", "/")
            if f"/{route_slug}/" in normalized or normalized.endswith(f"/{route_slug}.tsx"):
                if normalized not in seeded:
                    seeded.append(normalized)

        for component_dir in nextjs_profile.get("component_directories", []):
            if isinstance(component_dir, str) and component_dir:
                seeded.append(component_dir.replace("\\", "/"))
                break
        return seeded[:10]

    def _extract_scope_route_slug(self, issue: str) -> str | None:
        if not isinstance(issue, str) or not issue.strip():
            return None

        title_match = re.search(r"^\s*Title\s*:\s*(.+)$", issue, re.IGNORECASE | re.MULTILINE)
        candidate_texts = []
        if title_match and isinstance(title_match.group(1), str):
            candidate_texts.append(title_match.group(1).strip())
        candidate_texts.append(issue)

        for text in candidate_texts:
            lowered = re.sub(r"https?://\S+", " ", text.lower())
            explicit_match = re.search(r"(?:(?:path|route|url|page)\s+)?/([a-z0-9\-/]+)", lowered)
            if explicit_match:
                return explicit_match.group(1).strip("/")
            phrase_match = re.search(r"\b([a-z0-9-]+)\s+(?:page|screen|view|route|layout|api)\b", lowered)
            if phrase_match:
                return phrase_match.group(1)
            for term in ["github", "dashboard", "settings", "profile", "billing", "analytics"]:
                if term in lowered:
                    return term
        return None


class AnalysisAgent(PlannerAgent):
    """Deterministic repository analysis and retrieval."""

    def run(self, state: AgentState) -> dict:
        issue = state["issue_description"]
        search = CodeSearch(state["workspace_dir"])
        keywords = self._extract_keywords(issue)
        workspace_profile = detect_workspace_profile(state["workspace_dir"])
        design_brief = self._extract_design_brief(issue, workspace_profile)
        version_resolution = resolve_workspace_version_context(state["workspace_dir"], issue, workspace_profile)
        remediation_context = self._planning_remediation_context(state)
        available_skills = self._available_skills(state["workspace_dir"])
        selected_skills = select_skills(
            available_skills,
            issue,
            workspace_profile,
            limit=max(0, self.config.skill_selection_limit),
        )
        selected_skills, blocked_skills = partition_skills_by_permission(
            selected_skills,
            self.config.skill_allowed_permissions,
        )
        skill_invocations = [
            self._planning_skill_invocation_summary(skill, outcome="applied", phase="plan")
            for skill in selected_skills
        ] + [
            self._planning_skill_invocation_summary(skill, outcome="blocked", phase="plan")
            for skill in blocked_skills
        ]
        retrieval_mode = self._normalized_retrieval_mode()
        scored_files = self._rank_candidate_files(search, state["workspace_dir"], workspace_profile, keywords, retrieval_mode)
        graph_seed_files = self._graph_seed_files(search, scored_files, keywords) if retrieval_mode == "hybrid" else []

        candidate_files = [file_path for file_path, _ in scored_files[:10]]
        candidate_files = self._expand_related_files(search, candidate_files, keywords, retrieval_mode)
        candidate_files = self._prioritize_profile_files(candidate_files, workspace_profile)
        candidate_files = self._prioritize_remediation_files(candidate_files, remediation_context)
        candidate_files, blocked_candidate_files = filter_edit_paths(
            candidate_files,
            self.config.edit_allow_globs,
            self.config.edit_deny_globs,
        )

        if not candidate_files:
            candidate_files = [
                file_path for file_path in search.list_files("ai_code_agent") if file_path.endswith(".py")
            ][:10]
            candidate_files, blocked_fallback_files = filter_edit_paths(
                candidate_files,
                self.config.edit_allow_globs,
                self.config.edit_deny_globs,
            )
            blocked_candidate_files.extend(blocked_fallback_files)

        prompt_payload = {
            "issue": issue,
            "scope_context": state.get("scope_context") if isinstance(state.get("scope_context"), dict) else {},
            "workspace_profile": workspace_profile,
            "design_brief": design_brief,
            "version_resolution": version_resolution,
            "retry_count": state.get("retry_count", 0),
            "remediation": remediation_context,
            "candidate_files": candidate_files[:10],
            "selected_skills": [self._planning_skill_summary(skill) for skill in selected_skills],
        }
        response = self.llm.generate_json(ANALYSIS_SYSTEM_PROMPT, json.dumps(prompt_payload, indent=2))
        response_candidate_files = response.get("candidate_files") if isinstance(response, dict) else None
        if isinstance(response_candidate_files, list):
            normalized_candidate_files = [
                file_path.replace("\\", "/")
                for file_path in response_candidate_files
                if isinstance(file_path, str) and file_path and not self._skip_file(file_path.replace("\\", "/"))
            ]
            if normalized_candidate_files:
                candidate_files = normalized_candidate_files[:10]
        response_risks = response.get("risks") if isinstance(response, dict) else None
        response_evidence = response.get("evidence") if isinstance(response, dict) else None

        return {
            "analysis_context": {
                "status": "ready",
                "keywords": keywords[:10],
                "workspace_profile": workspace_profile,
                "design_brief": design_brief,
                "version_resolution": version_resolution,
                "available_skill_count": len(available_skills),
                "selected_skills": selected_skills,
                "blocked_skills": blocked_skills,
                "skill_invocations": [item for item in skill_invocations if item],
                "retrieval_strategy": retrieval_mode,
                "graph_seed_files": graph_seed_files,
                "candidate_files": candidate_files[:10],
                "blocked_candidate_files": blocked_candidate_files[:10],
                "candidate_scores": [
                    {"file_path": file_path, "score": score} for file_path, score in scored_files[:10]
                ],
                "candidate_explanations": [
                    search.explain_candidate(file_path, keywords, graph_seed_files)
                    for file_path, _ in scored_files[:10]
                ],
                "risks": [item for item in response_risks if isinstance(item, str) and item][:10] if isinstance(response_risks, list) else [],
                "evidence": [item for item in response_evidence if isinstance(item, str) and item][:10] if isinstance(response_evidence, list) else [],
                "remediation": remediation_context,
            },
            "workspace_profile": workspace_profile,
        }


class PlanAgent(PlannerAgent):
    """LLM planning from scope and analysis evidence."""

    def run(self, state: AgentState) -> dict:
        issue = state["issue_description"]
        analysis_context = state.get("analysis_context") if isinstance(state.get("analysis_context"), dict) else {}
        scope_context = state.get("scope_context") if isinstance(state.get("scope_context"), dict) else {}
        workspace_profile = analysis_context.get("workspace_profile") if isinstance(analysis_context.get("workspace_profile"), dict) else detect_workspace_profile(state["workspace_dir"])
        design_brief = analysis_context.get("design_brief")
        version_resolution = analysis_context.get("version_resolution") if isinstance(analysis_context.get("version_resolution"), dict) else None
        remediation_context = analysis_context.get("remediation") if isinstance(analysis_context.get("remediation"), dict) else self._planning_remediation_context(state)
        candidate_files = [
            file_path for file_path in analysis_context.get("candidate_files", [])
            if isinstance(file_path, str) and file_path
        ]
        selected_skills = analysis_context.get("selected_skills") if isinstance(analysis_context.get("selected_skills"), list) else []

        prompt_payload = {
            "issue": issue,
            "scope_context": scope_context,
            "workspace_profile": workspace_profile,
            "design_brief": design_brief,
            "version_resolution": version_resolution,
            "retry_count": state.get("retry_count", 0),
            "remediation": remediation_context,
            "failed_task_ids": state.get("failed_task_ids") or [],
            "task_statuses": state.get("task_statuses") or {},
            "selected_skills": selected_skills,
            "candidate_files": candidate_files[:10],
        }
        response = self.llm.generate_json(PLAN_SYSTEM_PROMPT, json.dumps(prompt_payload, indent=2))
        plan = self._normalize_plan(response.get("plan")) or self._fallback_plan(issue, candidate_files)
        files_to_edit = response.get("files_to_edit") or candidate_files[:10]
        route_anchor_candidates = self._route_anchor_candidates(analysis_context)
        route_lock = self._locked_nextjs_route_rewrite(issue, files_to_edit, route_anchor_candidates or candidate_files, workspace_profile)
        if route_lock is not None:
            from_slug, to_slug = route_lock
            files_to_edit = self._rewrite_nextjs_route_targets(files_to_edit, from_slug, to_slug, workspace_profile)
            response = dict(response)
            response["scope"] = self._rewrite_nextjs_scope_targets(response.get("scope"), from_slug, to_slug, workspace_profile)
            response["tasks"] = self._rewrite_nextjs_task_targets(response.get("tasks"), from_slug, to_slug, workspace_profile)
        graph_route_slug = self._existing_next_route_slug(
            route_anchor_candidates or candidate_files,
            workspace_profile.get("nextjs") if isinstance(workspace_profile, dict) else {},
            self._issue_disallowed_route_slugs(issue),
        ) if self._is_nextjs_graph_request(issue, workspace_profile) else None
        if graph_route_slug:
            files_to_edit = self._canonicalize_nextjs_graph_targets(files_to_edit, graph_route_slug)
            response = dict(response)
            response["scope"] = self._canonicalize_nextjs_graph_scope(response.get("scope"), graph_route_slug)
            response["tasks"] = self._canonicalize_nextjs_graph_tasks(response.get("tasks"), graph_route_slug)
        files_to_edit = self._expand_nextjs_route_bundle_files(files_to_edit, workspace_profile)
        files_to_edit = self._expand_nextjs_scaffold_target_files(state, files_to_edit, workspace_profile)
        files_to_edit = self._prioritize_version_resolution_files(files_to_edit, workspace_profile, version_resolution)
        edit_intent = self._normalize_edit_intent(response.get("edit_intent"), files_to_edit, remediation_context)
        scope_seed_files = list(files_to_edit)
        if remediation_context:
            scope_seed_files.extend(
                file_path for file_path in remediation_context.get("focus_areas", [])
                if isinstance(file_path, str) and file_path
            )
        scope = self._normalize_scope(response.get("scope"), scope_seed_files)
        tasks = self._normalize_tasks(response.get("tasks"), files_to_edit, state.get("failed_task_ids"))
        if remediation_context:
            files_to_edit = self._prioritize_remediation_files(files_to_edit, remediation_context)
        files_to_edit, blocked_files_to_edit = filter_edit_paths(
            files_to_edit,
            self.config.edit_allow_globs,
            self.config.edit_deny_globs,
        )
        file_edit_policy = summarize_edit_policy(self.config.edit_allow_globs, self.config.edit_deny_globs)

        return {
            "plan": plan,
            "files_to_edit": files_to_edit,
            "file_edit_policy": file_edit_policy,
            "workspace_profile": workspace_profile,
            "planning_context": {
                "keywords": analysis_context.get("keywords", []),
                "workspace_profile": workspace_profile,
                "design_brief": design_brief,
                "version_resolution": version_resolution,
                "file_edit_policy": file_edit_policy,
                "available_skill_count": analysis_context.get("available_skill_count", 0),
                "selected_skills": [self._planning_skill_summary(skill) for skill in selected_skills],
                "blocked_skills": [self._planning_skill_summary(skill) for skill in analysis_context.get("blocked_skills", []) if isinstance(skill, dict)],
                "skill_invocations": [item for item in analysis_context.get("skill_invocations", []) if isinstance(item, dict)],
                "blocked_candidate_files": analysis_context.get("blocked_candidate_files", [])[:10],
                "blocked_files_to_edit": blocked_files_to_edit[:10],
                "retrieval_strategy": analysis_context.get("retrieval_strategy"),
                "candidate_explanations_schema_version": 2,
                "graph_seed_files": analysis_context.get("graph_seed_files", []),
                "remediation": remediation_context,
                "edit_intent": edit_intent,
                "scope": scope,
                "tasks": tasks,
                "candidate_scores": analysis_context.get("candidate_scores", []),
                "candidate_explanations": analysis_context.get("candidate_explanations", []),
            },
        }

    def _route_anchor_candidates(self, analysis_context: dict[str, Any]) -> list[str]:
        candidate_scores = analysis_context.get("candidate_scores") if isinstance(analysis_context.get("candidate_scores"), list) else []
        ranked_files: list[str] = []
        for item in candidate_scores:
            if not isinstance(item, dict):
                continue
            file_path = item.get("file_path")
            if isinstance(file_path, str) and file_path and not self._skip_file(file_path.replace("\\", "/")):
                ranked_files.append(file_path.replace("\\", "/"))
        return ranked_files

    def _is_nextjs_graph_request(self, issue: str, workspace_profile: dict) -> bool:
        nextjs_profile = workspace_profile.get("nextjs") if isinstance(workspace_profile, dict) else None
        if not isinstance(nextjs_profile, dict):
            return False
        lowered = issue.lower() if isinstance(issue, str) else ""
        return any(term in lowered for term in ["graph", "react flow", "reactflow", "workspace"])

    def _canonicalize_nextjs_graph_targets(self, file_paths: list[str], route_slug: str) -> list[str]:
        alias_map = self._nextjs_graph_alias_map(route_slug)
        canonical: list[str] = []
        seen: set[str] = set()
        for file_path in file_paths:
            if not isinstance(file_path, str) or not file_path:
                continue
            normalized = file_path.replace("\\", "/")
            mapped = alias_map.get(normalized, normalized)
            if mapped not in seen:
                canonical.append(mapped)
                seen.add(mapped)
        return canonical

    def _canonicalize_nextjs_graph_scope(self, raw_scope: object, route_slug: str) -> object:
        if not isinstance(raw_scope, dict):
            return raw_scope
        return {
            **raw_scope,
            "in_scope": self._canonicalize_nextjs_graph_targets(raw_scope.get("in_scope", []), route_slug) if isinstance(raw_scope.get("in_scope"), list) else raw_scope.get("in_scope"),
        }

    def _canonicalize_nextjs_graph_tasks(self, raw_tasks: object, route_slug: str) -> object:
        if not isinstance(raw_tasks, list):
            return raw_tasks
        rewritten: list[dict[str, Any]] = []
        for item in raw_tasks:
            if not isinstance(item, dict):
                continue
            updated = dict(item)
            if isinstance(item.get("target_files"), list):
                updated["target_files"] = self._canonicalize_nextjs_graph_targets(item.get("target_files", []), route_slug)
            rewritten.append(updated)
        return rewritten

    def _nextjs_graph_alias_map(self, route_slug: str) -> dict[str, str]:
        route_component = f"components/react-flow/{route_slug}-react-flow-workspace.tsx" if route_slug else "components/react-flow/home-react-flow-workspace.tsx"
        return {
            "lib/graph/types.ts": "components/graph/types.ts",
            "lib/graph/type.ts": "components/graph/types.ts",
            "lib/graph/graph-types.ts": "components/graph/types.ts",
            "lib/graph/data.ts": "components/graph/graph-data.ts",
            "lib/graph/graph-data.ts": "components/graph/graph-data.ts",
            "lib/graph/utils.ts": route_component,
            "components/graph-workspace.tsx": "components/graph/GraphWorkspace.tsx",
            "components/graph-legend.tsx": "components/graph/GraphLegend.tsx",
            "components/graph-detail-panel.tsx": "components/graph/GraphSummary.tsx",
            "components/graph-node-card.tsx": "components/graph/GraphEmptyState.tsx",
            "components/graph/graph-view.tsx": "components/graph/GraphWorkspace.tsx",
            "components/graph/react-flow-workspace.tsx": route_component,
            "components/ui/graph/graph-card.tsx": "components/graph/GraphSummary.tsx",
            "components/graph/graph-card.tsx": "components/graph/GraphSummary.tsx",
        }

    def _locked_nextjs_route_rewrite(
        self,
        issue: str,
        files_to_edit: list[str],
        candidate_files: list[str],
        workspace_profile: dict,
    ) -> tuple[str, str] | None:
        nextjs_profile = workspace_profile.get("nextjs") if isinstance(workspace_profile, dict) else None
        if not isinstance(nextjs_profile, dict):
            return None
        if self._issue_has_explicit_next_route(issue):
            return None
        lower_issue = issue.lower() if isinstance(issue, str) else ""
        if not any(term in lower_issue for term in ["graph", "react flow", "reactflow", "workspace"]):
            return None
        disallowed_slugs = self._issue_disallowed_route_slugs(issue)
        preferred_slug = self._existing_next_route_slug(candidate_files, nextjs_profile, disallowed_slugs)
        if not preferred_slug:
            return None
        planned_slugs = [
            slug for slug in self._next_route_slugs_from_paths(files_to_edit, nextjs_profile)
            if slug and slug != preferred_slug and slug not in disallowed_slugs
        ]
        if not planned_slugs:
            return None
        return planned_slugs[0], preferred_slug

    def _issue_has_explicit_next_route(self, issue: str) -> bool:
        if not isinstance(issue, str) or not issue.strip():
            return False
        sanitized = re.sub(r"https?://\S+", " ", issue.lower())
        sanitized = re.sub(r"\b(issue provider|source url|github issue|azure devops work item):[^\n]*", " ", sanitized)
        return bool(re.search(r"(?:\bpath\b|\broute\b|\burl\b|\bpage\b)\s+/(?:[a-z0-9-]+(?:/[a-z0-9-]+)*)", sanitized)) or "app/" in sanitized

    def _existing_next_route_slug(self, candidate_files: list[str], nextjs_profile: dict[str, Any], disallowed_slugs: set[str] | None = None) -> str | None:
        app_dir = (nextjs_profile.get("app_dir") or "app").replace("\\", "/")
        disallowed = disallowed_slugs or set()
        for file_path in candidate_files:
            if not isinstance(file_path, str):
                continue
            normalized = file_path.replace("\\", "/")
            if normalized.startswith(f"{app_dir}/") and normalized.endswith("/page.tsx"):
                slug = normalized[len(app_dir) + 1 : -len("/page.tsx")]
                if slug and "/" not in slug and slug not in disallowed:
                    return slug
        return None

    def _issue_disallowed_route_slugs(self, issue: str) -> set[str]:
        if not isinstance(issue, str) or not issue.strip():
            return set()
        disallowed: set[str] = set()
        github_match = re.search(r"github issue:\s*[^/]+/(?P<repo>[a-z0-9-]+)#\d+", issue, re.I)
        if github_match:
            repo_slug = github_match.group("repo").strip().lower()
            if repo_slug:
                disallowed.add(repo_slug)
        return disallowed

    def _next_route_slugs_from_paths(self, file_paths: list[str], nextjs_profile: dict[str, Any]) -> list[str]:
        app_dir = (nextjs_profile.get("app_dir") or "app").replace("\\", "/")
        slugs: list[str] = []
        seen: set[str] = set()
        for file_path in file_paths:
            if not isinstance(file_path, str) or not file_path:
                continue
            normalized = file_path.replace("\\", "/").rstrip("/")
            if not normalized.startswith(f"{app_dir}/"):
                continue
            remainder = normalized[len(app_dir) + 1:]
            top_segment = remainder.split("/", 1)[0]
            if top_segment and top_segment not in {"page.tsx", "layout.tsx", "loading.tsx", "error.tsx"} and top_segment not in seen:
                slugs.append(top_segment)
                seen.add(top_segment)
        return slugs

    def _rewrite_nextjs_route_targets(self, file_paths: list[str], from_slug: str, to_slug: str, workspace_profile: dict) -> list[str]:
        nextjs_profile = workspace_profile.get("nextjs") if isinstance(workspace_profile, dict) else None
        if not isinstance(nextjs_profile, dict):
            return [file_path.replace("\\", "/") for file_path in file_paths if isinstance(file_path, str)]
        app_dir = (nextjs_profile.get("app_dir") or "app").replace("\\", "/")
        from_prefix = f"{app_dir}/{from_slug}"
        to_prefix = f"{app_dir}/{to_slug}"
        rewritten: list[str] = []
        for file_path in file_paths:
            if not isinstance(file_path, str) or not file_path:
                continue
            normalized = file_path.replace("\\", "/")
            rewritten.append(to_prefix + normalized[len(from_prefix):] if normalized.startswith(from_prefix) else normalized)
        return rewritten

    def _rewrite_nextjs_scope_targets(self, raw_scope: object, from_slug: str, to_slug: str, workspace_profile: dict) -> object:
        if not isinstance(raw_scope, dict):
            return raw_scope
        return {
            **raw_scope,
            "in_scope": self._rewrite_nextjs_route_targets(raw_scope.get("in_scope", []), from_slug, to_slug, workspace_profile) if isinstance(raw_scope.get("in_scope"), list) else raw_scope.get("in_scope"),
        }

    def _rewrite_nextjs_task_targets(self, raw_tasks: object, from_slug: str, to_slug: str, workspace_profile: dict) -> object:
        if not isinstance(raw_tasks, list):
            return raw_tasks
        rewritten: list[dict[str, Any]] = []
        for item in raw_tasks:
            if not isinstance(item, dict):
                continue
            updated = dict(item)
            if isinstance(item.get("target_files"), list):
                updated["target_files"] = self._rewrite_nextjs_route_targets(item.get("target_files", []), from_slug, to_slug, workspace_profile)
            rewritten.append(updated)
        return rewritten
