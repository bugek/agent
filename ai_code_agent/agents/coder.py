import difflib
import json
from pathlib import PurePosixPath
from pathlib import Path
import re
from typing import Any

from ai_code_agent.agents.base import BaseAgent, is_analysis_only_request
from ai_code_agent.orchestrator import AgentState
from ai_code_agent.llm.prompts import CODER_SYSTEM_PROMPT
from ai_code_agent.tools.edit_policy import evaluate_edit_path, filter_edit_paths, summarize_edit_policy
from ai_code_agent.tools.file_editor import FileEditor
from ai_code_agent.tools.version_resolution import is_dependency_upgrade_request
from ai_code_agent.tools.workspace_profile import detect_workspace_profile

class CoderAgent(BaseAgent):
    """
    Agent responsible for editing files based on the plan.
    """
    
    def run(self, state: AgentState) -> dict:
        """
        Executes the plan by making changes to files using file_editor tools.
        """
        if self._is_analysis_only(state["issue_description"]):
            return {
                "patches": [],
                "error_message": None,
                "codegen_summary": {
                    "requested_operations": 0,
                    "applied_operations": 0,
                    "failed_operations": [],
                    "skipped_reason": "analysis_only_request",
                },
            }

        editor = FileEditor(state["workspace_dir"])
        workspace_profile = state.get("workspace_profile") or detect_workspace_profile(state["workspace_dir"])
        remediation_context = self._remediation_context(state)
        version_resolution = self._version_resolution(state)
        allow_deterministic_retry = remediation_context is not None and self._can_use_deterministic_nextjs_retry(state)
        if remediation_context is None or allow_deterministic_retry:
            dependency_upgrade_operations = self._build_nextjs_dependency_upgrade_operations(
                state,
                workspace_profile,
                editor,
                version_resolution,
            )
            if dependency_upgrade_operations:
                return self._apply_operations(
                    editor,
                    state,
                    dependency_upgrade_operations,
                    generated_by="nextjs_dependency_upgrade",
                    remediation_context=None,
                )

            nextjs_operations = self._build_nextjs_operations(state, workspace_profile, editor)
            if nextjs_operations:
                return self._apply_operations(
                    editor,
                    state,
                    nextjs_operations,
                    generated_by="nextjs_scaffold",
                    remediation_context=None,
                )

            nestjs_operations = self._build_nestjs_operations(state, workspace_profile, editor)
            if nestjs_operations:
                return self._apply_operations(
                    editor,
                    state,
                    nestjs_operations,
                    generated_by="nestjs_scaffold",
                    remediation_context=None,
                )

        candidate_files = self._candidate_files_for_prompt(state, remediation_context)
        candidate_files, blocked_context_files = filter_edit_paths(
            candidate_files,
            self.config.edit_allow_globs,
            self.config.edit_deny_globs,
        )
        candidate_files = self._filter_out_of_scope(candidate_files, state)
        file_context = []
        for file_path in candidate_files:
            excerpt = editor.view_file(file_path)
            file_context.append({"file_path": file_path, "content": excerpt[:4000]})

        active_tasks = self._active_tasks(state)
        scope = self._task_scope(state)
        prompt_payload = {
            "issue": state["issue_description"],
            "plan": state.get("plan"),
            "edit_intent": self._edit_intent(state),
            "scope": scope,
            "tasks": active_tasks,
            "workspace_profile": workspace_profile,
            "design_brief": self._frontend_design_brief(state),
            "version_resolution": version_resolution,
            "file_edit_policy": state.get("file_edit_policy") or summarize_edit_policy(
                self.config.edit_allow_globs,
                self.config.edit_deny_globs,
            ),
            "retry_count": state.get("retry_count", 0),
            "remediation": remediation_context,
            "files": file_context,
            "schema": {
                "operations": [
                    {
                        "type": "replace_text",
                        "file_path": "relative/path.py",
                        "search": "exact text to replace",
                        "replace": "new text"
                    },
                    {
                        "type": "create_file",
                        "file_path": "relative/new_file.py",
                        "content": "new file content"
                    }
                ]
            },
        }
        response = self.llm.generate_json(CODER_SYSTEM_PROMPT, json.dumps(prompt_payload, indent=2))
        normalized_operations = self._normalize_operations(response.get("operations", []), editor, state)
        result = self._apply_operations(
            editor,
            state,
            normalized_operations,
            generated_by="llm",
            remediation_context=remediation_context,
        )
        if blocked_context_files:
            result.setdefault("codegen_summary", {})["blocked_context_files"] = blocked_context_files
        return result

    def _apply_operations(
        self,
        editor: FileEditor,
        state: AgentState,
        operations: list[dict[str, Any]],
        generated_by: str,
        remediation_context: dict[str, Any] | None,
    ) -> dict:
        patches: list[dict] = []
        failures: list[str] = []
        blocked_operations: list[dict[str, str]] = []

        for operation in operations:
            file_path = operation.get("file_path")
            if isinstance(file_path, str):
                is_allowed, reason = evaluate_edit_path(
                    file_path,
                    self.config.edit_allow_globs,
                    self.config.edit_deny_globs,
                )
                if not is_allowed:
                    blocked_operations.append(
                        {
                            "file_path": file_path.replace("\\", "/"),
                            "reason": reason or "blocked by file edit policy",
                        }
                    )
                    continue
                if self._is_out_of_scope(file_path, state):
                    blocked_operations.append(
                        {
                            "file_path": file_path.replace("\\", "/"),
                            "reason": "blocked by task scope out_of_scope",
                        }
                    )
                    continue
            patch = self._apply_operation(editor, state, operation)
            if patch is not None:
                patches.append(patch)
            else:
                failures.append(self._describe_failed_operation(operation))

        error_message_parts = list(failures)
        if blocked_operations:
            blocked_summary = ", ".join(
                f"{item['file_path']} ({item['reason']})" for item in blocked_operations[:3]
            )
            error_message_parts.append(f"file edit policy blocked operations: {blocked_summary}")

        return {
            "patches": patches,
            "error_message": None if not error_message_parts else "; ".join(error_message_parts),
            "codegen_summary": {
                "requested_operations": len(operations),
                "applied_operations": len(patches),
                "failed_operations": failures,
                "blocked_operations": blocked_operations,
                "file_edit_policy": state.get("file_edit_policy") or summarize_edit_policy(
                    self.config.edit_allow_globs,
                    self.config.edit_deny_globs,
                ),
                "generated_by": generated_by,
                "retry_count": state.get("retry_count", 0),
                "remediation_applied": bool(remediation_context),
                "remediation_focus_count": len(remediation_context.get("focus_areas", [])) if remediation_context else 0,
            },
        }

    def _normalize_operations(self, operations: object, editor: FileEditor, state: AgentState) -> list[dict[str, Any]]:
        if not isinstance(operations, list):
            return []

        normalized: list[dict[str, Any]] = []
        for item in operations:
            if not isinstance(item, dict):
                continue
            operation = dict(item)
            file_path = operation.get("file_path")
            operation_type = operation.get("type")
            if (
                isinstance(file_path, str)
                and file_path.replace("\\", "/") == "package.json"
                and operation_type in {"create_file", "write_file"}
                and isinstance(operation.get("content"), str)
            ):
                operation["content"] = self._normalize_package_json_content(
                    operation["content"],
                    editor,
                    allow_version_changes=is_dependency_upgrade_request(state["issue_description"]),
                )
            normalized.append(operation)
        return normalized

    def _normalize_package_json_content(self, content: str, editor: FileEditor, *, allow_version_changes: bool = False) -> str:
        try:
            package_data = json.loads(content)
        except json.JSONDecodeError:
            return content

        if not isinstance(package_data, dict):
            return content

        existing_package = {}
        if editor.exists("package.json"):
            try:
                existing_package = json.loads(editor.view_file("package.json"))
            except json.JSONDecodeError:
                existing_package = {}

        for scalar_key in ["name", "version", "private"]:
            if not allow_version_changes and scalar_key in existing_package:
                package_data[scalar_key] = existing_package[scalar_key]

        for section in ["dependencies", "devDependencies"]:
            current = package_data.get(section)
            existing = existing_package.get(section)
            normalized_current = {str(key): value for key, value in current.items()} if isinstance(current, dict) else {}
            normalized_existing = {str(key): value for key, value in existing.items()} if isinstance(existing, dict) else {}
            if allow_version_changes:
                merged = dict(normalized_current or normalized_existing)
            else:
                merged = dict(normalized_existing)
                for key, value in normalized_current.items():
                    if key not in normalized_existing or key == "reactflow":
                        merged[key] = value
            if merged:
                package_data[section] = merged
            elif section in package_data:
                del package_data[section]

        current_scripts = package_data.get("scripts") if isinstance(package_data.get("scripts"), dict) else {}
        existing_scripts = existing_package.get("scripts") if isinstance(existing_package.get("scripts"), dict) else {}
        if allow_version_changes:
            merged_scripts = {str(key): value for key, value in current_scripts.items()}
        else:
            merged_scripts = {str(key): value for key, value in existing_scripts.items()}
            for key, value in current_scripts.items():
                if key not in merged_scripts:
                    merged_scripts[str(key)] = value
        if merged_scripts:
            package_data["scripts"] = merged_scripts

        return json.dumps(package_data, indent=2, ensure_ascii=True) + "\n"

    def _can_use_deterministic_nextjs_retry(self, state: AgentState) -> bool:
        workspace_profile = state.get("workspace_profile") if isinstance(state.get("workspace_profile"), dict) else {}
        if not isinstance(workspace_profile.get("nextjs"), dict):
            return False
        issue = state.get("issue_description") if isinstance(state.get("issue_description"), str) else ""
        return self._issue_requests_react_flow(issue)

    def _candidate_files_for_prompt(
        self,
        state: AgentState,
        remediation_context: dict[str, Any] | None,
    ) -> list[str]:
        candidates: list[str] = []
        for file_path in state.get("files_to_edit", []):
            if isinstance(file_path, str) and self._exists(state, file_path):
                candidates.append(file_path)

        if remediation_context:
            for file_path in remediation_context.get("focus_areas", []):
                if isinstance(file_path, str) and self._exists(state, file_path):
                    candidates.append(file_path)

        for intent in self._edit_intent(state):
            file_path = intent.get("file_path") if isinstance(intent, dict) else None
            if isinstance(file_path, str) and self._exists(state, file_path):
                candidates.append(file_path)

        for patch in state.get("patches", []):
            file_path = patch.get("file") if isinstance(patch, dict) else None
            if isinstance(file_path, str) and self._exists(state, file_path):
                candidates.append(file_path)

        deduplicated: list[str] = []
        seen: set[str] = set()
        for file_path in candidates:
            normalized = file_path.replace("\\", "/")
            if normalized in seen:
                continue
            deduplicated.append(normalized)
            seen.add(normalized)
        workspace_profile = state.get("workspace_profile") if isinstance(state.get("workspace_profile"), dict) else {}
        deduplicated = self._expand_nextjs_route_bundle_files(deduplicated, workspace_profile)
        existing = [file_path for file_path in deduplicated if self._exists(state, file_path)]
        return existing[:5]

    def _remediation_context(self, state: AgentState) -> dict[str, Any] | None:
        if int(state.get("retry_count", 0) or 0) <= 0:
            return None

        review_summary = state.get("review_summary") if isinstance(state.get("review_summary"), dict) else {}
        remediation = review_summary.get("remediation") if isinstance(review_summary.get("remediation"), dict) else {}
        required = bool(remediation.get("required"))
        if not required:
            return None

        context = {
            "source": "review_loop",
            "review_status": review_summary.get("status"),
            "failed_validation_labels": [
                label
                for label in remediation.get("failed_validation_labels", [])
                if isinstance(label, str) and label
            ],
            "blocked_file_paths": [
                file_path
                for file_path in remediation.get("blocked_file_paths", [])
                if isinstance(file_path, str) and file_path
            ],
            "failed_operations": [
                item
                for item in remediation.get("failed_operations", [])
                if isinstance(item, str) and item
            ],
            "focus_areas": [
                file_path
                for file_path in remediation.get("focus_areas", [])
                if isinstance(file_path, str) and file_path
            ],
            "guidance": [
                item
                for item in remediation.get("guidance", [])
                if isinstance(item, str) and item
            ],
            "task_remediation": [
                item
                for item in remediation.get("task_remediation", [])
                if isinstance(item, dict) and isinstance(item.get("task_id"), str) and item.get("task_id")
            ],
            "testing_summary": state.get("testing_summary") if isinstance(state.get("testing_summary"), dict) else {},
        }
        workspace_profile = state.get("workspace_profile") if isinstance(state.get("workspace_profile"), dict) else {}
        context["focus_areas"] = self._expand_nextjs_route_bundle_files(context["focus_areas"], workspace_profile)
        if not any(
            context[key]
            for key in ["failed_validation_labels", "blocked_file_paths", "failed_operations", "focus_areas", "guidance", "task_remediation"]
        ):
            return None
        return context

    def _edit_intent(self, state: AgentState) -> list[dict[str, Any]]:
        planning_context = state.get("planning_context") if isinstance(state.get("planning_context"), dict) else {}
        edit_intent = planning_context.get("edit_intent") if isinstance(planning_context.get("edit_intent"), list) else []
        normalized: list[dict[str, Any]] = []
        for item in edit_intent:
            if not isinstance(item, dict):
                continue
            file_path = item.get("file_path")
            if not isinstance(file_path, str) or not file_path:
                continue
            normalized_item: dict[str, Any] = {"file_path": file_path}
            for key in ["intent", "reason"]:
                value = item.get(key)
                if isinstance(value, str) and value:
                    normalized_item[key] = value
            if isinstance(item.get("validation_targets"), list):
                normalized_item["validation_targets"] = [
                    label for label in item.get("validation_targets", []) if isinstance(label, str) and label
                ]
            normalized.append(normalized_item)
        return normalized[:10]

    def _task_scope(self, state: AgentState) -> dict[str, list[str]]:
        planning_context = state.get("planning_context") if isinstance(state.get("planning_context"), dict) else {}
        scope = planning_context.get("scope") if isinstance(planning_context.get("scope"), dict) else {}
        return {
            "in_scope": [
                item for item in scope.get("in_scope", []) if isinstance(item, str) and item
            ],
            "out_of_scope": [
                item for item in scope.get("out_of_scope", []) if isinstance(item, str) and item
            ],
        }

    def _active_tasks(self, state: AgentState) -> list[dict[str, Any]]:
        planning_context = state.get("planning_context") if isinstance(state.get("planning_context"), dict) else {}
        tasks = planning_context.get("tasks") if isinstance(planning_context.get("tasks"), list) else []
        failed_task_ids = set(state.get("failed_task_ids") or [])
        task_statuses = state.get("task_statuses") if isinstance(state.get("task_statuses"), dict) else {}
        active: list[dict[str, Any]] = []
        for task in tasks:
            if not isinstance(task, dict):
                continue
            task_id = task.get("id")
            if not isinstance(task_id, str):
                continue
            status = task_statuses.get(task_id, task.get("status", "pending"))
            if status == "completed" and task_id not in failed_task_ids:
                continue
            active.append(dict(task, status="pending"))
        return active

    def _is_out_of_scope(self, file_path: str, state: AgentState) -> bool:
        scope = self._task_scope(state)
        out_of_scope = scope.get("out_of_scope", [])
        if not out_of_scope:
            return False
        normalized = file_path.replace("\\", "/")
        for pattern in out_of_scope:
            pattern_normalized = pattern.replace("\\", "/").rstrip("/")
            if normalized == pattern_normalized:
                return True
            if normalized.startswith(pattern_normalized + "/"):
                return True
        return False

    def _filter_out_of_scope(self, candidate_files: list[str], state: AgentState) -> list[str]:
        return [f for f in candidate_files if not self._is_out_of_scope(f, state)]

    def _exists(self, state: AgentState, file_path: str) -> bool:
        path = Path(state["workspace_dir"]) / file_path
        return path.exists()

    def _apply_operation(self, editor: FileEditor, state: AgentState, operation: dict[str, Any]) -> dict | None:
        operation_type = operation.get("type", "replace_text")
        applied_operation_type = operation_type
        file_path = operation.get("file_path")
        if not file_path:
            return None

        absolute_path = Path(state["workspace_dir"]) / file_path
        before = editor.view_file(file_path) if absolute_path.exists() else ""

        if operation_type == "replace_text":
            search = operation.get("search")
            replace = operation.get("replace")
            if search is None or replace is None or not absolute_path.exists():
                return None
            if search not in before:
                return None
            if not editor.replace_text(file_path, search, replace):
                return None
        elif operation_type == "create_file":
            content = operation.get("content")
            if content is None:
                return None
            if absolute_path.exists():
                applied_operation_type = "write_file"
                editor.write_file(file_path, content)
            elif not editor.create_file(file_path, content):
                return None
        elif operation_type == "write_file":
            content = operation.get("content")
            if content is None:
                return None
            editor.write_file(file_path, content)
        elif operation_type == "delete_file":
            if not absolute_path.exists() or not editor.delete_file(file_path):
                return None
        elif operation_type == "insert_lines":
            line_number = operation.get("line_number")
            content = operation.get("content")
            if line_number is None or content is None or not absolute_path.exists():
                return None
            if not editor.insert_lines(file_path, int(line_number), content):
                return None
        else:
            return None

        after = editor.view_file(file_path) if absolute_path.exists() else ""
        diff = "\n".join(
            difflib.unified_diff(
                before.splitlines(),
                after.splitlines(),
                fromfile=file_path,
                tofile=file_path,
                lineterm="",
            )
        )
        return {
            "file": file_path,
            "operation": applied_operation_type,
            "diff": diff,
        }

    def _describe_failed_operation(self, operation: dict[str, Any]) -> str:
        operation_type = operation.get("type", "replace_text")
        file_path = operation.get("file_path", "<missing file>")
        return f"{operation_type} failed for {file_path}"

    def _is_analysis_only(self, issue: str) -> bool:
        return is_analysis_only_request(issue)

    def _build_nextjs_operations(
        self,
        state: AgentState,
        workspace_profile: dict[str, Any],
        editor: FileEditor,
    ) -> list[dict[str, Any]]:
        nextjs_profile = workspace_profile.get("nextjs")
        if not nextjs_profile:
            return []

        issue = state["issue_description"]
        lower_issue = issue.lower()
        design_brief = self._frontend_design_brief(state)
        if is_dependency_upgrade_request(issue):
            return []
        if not re.search(r"\b(next|page|layout|component|api|route|handler|hero|card|form|modal|section|dashboard|screen|view)\b", lower_issue):
            return []

        route_slug = self._preferred_next_route_slug(state, nextjs_profile)
        action = "write_file" if re.search(r"\b(update|modify|refactor|revamp|rewrite|redesign)\b", lower_issue) else "create_file"
        operations: list[dict[str, Any]] = []

        component_request = self._extract_component_request(issue, route_slug)

        wants_page = bool(re.search(r"\b(page|screen|view)\b", lower_issue))
        wants_layout = bool(re.search(r"\b(layout|shell|wrapper)\b", lower_issue))
        wants_api_route = bool(re.search(r"\b(api|route handler|endpoint|handler)\b", lower_issue))
        wants_component = component_request is not None or bool(re.search(r"\b(component|card|hero|section|form|modal|panel|table|list)\b", lower_issue))
        wants_react_flow = self._issue_requests_react_flow(issue)

        if wants_react_flow and route_slug:
            component_request = self._preferred_reactflow_component_request(component_request, route_slug)

        component_file = self._resolve_component_file(nextjs_profile, component_request)

        if wants_react_flow and editor.exists("package.json") and self._is_explicit_nextjs_target("package.json", state):
            package_json_content = editor.view_file("package.json")
            updated_package_json = self._ensure_package_dependency(package_json_content, "reactflow", "^11.11.4")
            if updated_package_json != package_json_content:
                operations.append(
                    self._file_operation(editor, "package.json", updated_package_json, preferred_action="write_file")
                )

        reactflow_component_file = self._resolve_reactflow_component_file(nextjs_profile, route_slug) if wants_react_flow else None
        graph_types_file = self._resolve_graph_types_file() if wants_react_flow else None
        graph_data_file = self._resolve_graph_data_file() if wants_react_flow else None
        graph_component_files = self._resolve_graph_component_files(nextjs_profile) if wants_react_flow else {}

        if wants_react_flow and reactflow_component_file is not None:
            if graph_types_file is not None:
                operations.append(
                    self._file_operation(
                        editor,
                        graph_types_file,
                        self._graph_types_template(),
                        preferred_action=action,
                    )
                )
            if graph_data_file is not None:
                operations.append(
                    self._file_operation(
                        editor,
                        graph_data_file,
                        self._graph_data_template(),
                        preferred_action=action,
                    )
                )
            for support_file, template in self._graph_support_operations(graph_component_files, reactflow_component_file).items():
                operations.append(
                    self._file_operation(
                        editor,
                        support_file,
                        template,
                        preferred_action=action,
                    )
                )
            operations.append(
                self._file_operation(
                    editor,
                    reactflow_component_file,
                    self._next_reactflow_workspace_template(route_slug, issue, design_brief),
                    preferred_action=action,
                )
            )

        if wants_component and component_file is not None:
            operations.append(
                self._file_operation(
                    editor,
                    component_file,
                    self._next_component_template(
                        component_request or "Feature Section",
                        issue,
                        route_slug,
                        design_brief,
                        reactflow_component_file=reactflow_component_file,
                    ),
                    preferred_action=action,
                )
            )

        if wants_react_flow:
            root_preview_file = self._resolve_root_preview_file(nextjs_profile)
            if (
                root_preview_file
                and root_preview_file != self._resolve_next_page_file(nextjs_profile, route_slug)
                and editor.exists(root_preview_file)
                and self._is_explicit_nextjs_target(root_preview_file, state)
            ):
                operations.append(
                    self._file_operation(
                        editor,
                        root_preview_file,
                        self._next_graph_home_preview_template(route_slug, issue, design_brief),
                        preferred_action="write_file",
                    )
                )

        if not editor.exists(".gitignore"):
            operations.append(
                self._file_operation(
                    editor,
                    ".gitignore",
                    self._next_gitignore_template(),
                    preferred_action="create_file",
                )
            )

        if wants_page:
            page_file = self._resolve_next_page_file(nextjs_profile, route_slug)
            if page_file is not None:
                operations.append(
                    self._file_operation(
                        editor,
                        page_file,
                        self._next_page_template(page_file, route_slug, component_file, component_request, issue, design_brief),
                        preferred_action=action,
                    )
                )
                if nextjs_profile.get("router_type") == "app":
                    for special_file, template in [
                        (self._resolve_next_special_file(nextjs_profile, route_slug, "loading.tsx"), self._next_loading_template(issue, route_slug, design_brief)),
                        (self._resolve_next_special_file(nextjs_profile, route_slug, "error.tsx"), self._next_error_template(issue, route_slug, design_brief)),
                    ]:
                        if special_file is not None:
                            operations.append(
                                self._file_operation(
                                    editor,
                                    special_file,
                                    template,
                                    preferred_action=action,
                                )
                            )

        if wants_layout:
            layout_file = self._resolve_next_layout_file(nextjs_profile, route_slug)
            if layout_file is not None:
                operations.append(
                    self._file_operation(
                        editor,
                        layout_file,
                        self._next_layout_template(route_slug, issue, design_brief),
                        preferred_action=action,
                    )
                )

        if wants_api_route:
            api_file = self._resolve_next_api_route_file(nextjs_profile, route_slug)
            if api_file is not None:
                operations.append(
                    self._file_operation(
                        editor,
                        api_file,
                        self._next_api_route_template(nextjs_profile, route_slug),
                        preferred_action=action,
                    )
                )

        deduplicated: list[dict[str, Any]] = []
        seen_paths: set[str] = set()
        for operation in operations:
            file_path = operation.get("file_path")
            if not file_path or file_path in seen_paths:
                continue
            deduplicated.append(operation)
            seen_paths.add(file_path)

        return deduplicated

    def _is_explicit_nextjs_target(self, file_path: str, state: AgentState) -> bool:
        normalized_target = file_path.replace("\\", "/")
        if any(isinstance(candidate, str) and candidate.replace("\\", "/") == normalized_target for candidate in state.get("files_to_edit", [])):
            return True
        planning_context = state.get("planning_context") if isinstance(state.get("planning_context"), dict) else {}
        planning_scope = planning_context.get("scope") if isinstance(planning_context.get("scope"), dict) else {}
        if any(isinstance(candidate, str) and candidate.replace("\\", "/") == normalized_target for candidate in planning_scope.get("in_scope", [])):
            return True
        for task in planning_context.get("tasks", []) if isinstance(planning_context.get("tasks"), list) else []:
            if not isinstance(task, dict):
                continue
            if any(isinstance(candidate, str) and candidate.replace("\\", "/") == normalized_target for candidate in task.get("target_files", [])):
                return True
        remediation_context = self._remediation_context(state)
        if remediation_context and any(isinstance(candidate, str) and candidate.replace("\\", "/") == normalized_target for candidate in remediation_context.get("focus_areas", [])):
            return True
        return False

    def _version_resolution(self, state: AgentState) -> dict[str, Any] | None:
        planning_context = state.get("planning_context") if isinstance(state.get("planning_context"), dict) else {}
        version_resolution = planning_context.get("version_resolution")
        return version_resolution if isinstance(version_resolution, dict) else None

    def _build_nextjs_dependency_upgrade_operations(
        self,
        state: AgentState,
        workspace_profile: dict[str, Any],
        editor: FileEditor,
        version_resolution: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        nextjs_profile = workspace_profile.get("nextjs")
        if not nextjs_profile or not isinstance(version_resolution, dict):
            return []
        if not version_resolution.get("dependency_upgrade_request"):
            return []

        selected_version = version_resolution.get("selected_version")
        if not isinstance(selected_version, str) or not selected_version:
            return []

        operations: list[dict[str, Any]] = []
        if editor.exists("package.json"):
            package_json_content = editor.view_file("package.json")
            updated_package_json = self._update_package_dependency_version(package_json_content, "next", selected_version)
            if updated_package_json != package_json_content:
                operations.append(self._file_operation(editor, "package.json", updated_package_json, preferred_action="write_file"))

        if version_resolution.get("requires_version_display"):
            layout_file = self._resolve_next_layout_file(nextjs_profile, "")
            if isinstance(layout_file, str) and editor.exists(layout_file):
                layout_content = editor.view_file(layout_file)
                updated_layout = self._inject_package_version_display(layout_file, layout_content)
                if updated_layout != layout_content:
                    operations.append(self._file_operation(editor, layout_file, updated_layout, preferred_action="write_file"))

        return operations

    def _update_package_dependency_version(self, package_json_content: str, package_name: str, version: str) -> str:
        try:
            package_data = json.loads(package_json_content)
        except json.JSONDecodeError:
            return package_json_content

        dependencies = package_data.get("dependencies") if isinstance(package_data.get("dependencies"), dict) else None
        dev_dependencies = package_data.get("devDependencies") if isinstance(package_data.get("devDependencies"), dict) else None

        if dependencies and package_name in dependencies:
            dependencies[package_name] = version
        elif dev_dependencies and package_name in dev_dependencies:
            dev_dependencies[package_name] = version
        else:
            if dependencies is None:
                package_data["dependencies"] = {}
                dependencies = package_data["dependencies"]
            dependencies[package_name] = version

        return json.dumps(package_data, indent=2, ensure_ascii=True) + "\n"

    def _ensure_package_dependency(self, package_json_content: str, package_name: str, version: str, *, dev: bool = False) -> str:
        try:
            package_data = json.loads(package_json_content)
        except json.JSONDecodeError:
            return package_json_content

        target_section = "devDependencies" if dev else "dependencies"
        target_dependencies = package_data.get(target_section)
        if not isinstance(target_dependencies, dict):
            target_dependencies = {}
            package_data[target_section] = target_dependencies

        target_dependencies[package_name] = version

        other_section = "dependencies" if dev else "devDependencies"
        other_dependencies = package_data.get(other_section)
        if isinstance(other_dependencies, dict) and package_name in other_dependencies:
            del other_dependencies[package_name]

        return json.dumps(package_data, indent=2, ensure_ascii=True) + "\n"

    def _inject_package_version_display(self, layout_file: str, content: str) -> str:
        if "packageJson.version" in content or "v{appVersion}" in content:
            return content

        relative_import = self._relative_package_json_import(layout_file)
        import_statement = f"import packageJson from '{relative_import}';"
        updated = content

        if import_statement not in updated:
            import_lines = list(re.finditer(r"^import .*?;$", updated, re.M))
            if import_lines:
                insert_at = import_lines[-1].end()
                updated = updated[:insert_at] + f"\n{import_statement}" + updated[insert_at:]
            else:
                updated = import_statement + "\n" + updated

        if "const appVersion = packageJson.version;" not in updated:
            export_match = re.search(r"\nexport default (async )?function ", updated)
            if export_match:
                updated = updated[: export_match.start()] + "\nconst appVersion = packageJson.version;\n" + updated[export_match.start() :]
            else:
                updated += "\nconst appVersion = packageJson.version;\n"

        if "</body>" not in updated:
            return updated

        if "<footer" in updated and "</footer>" in updated:
            return updated.replace(
                "</footer>",
                '          <span style={{ marginLeft: "0.5rem" }}>v{appVersion}</span>\n        </footer>',
                1,
            )

        footer_block = (
            "\n        <footer\n"
            "          style={{\n"
            "            padding: \"1rem 1.5rem 2rem\",\n"
            "            textAlign: \"center\",\n"
            "            color: \"#6b5a4a\",\n"
            "            fontSize: \"0.95rem\",\n"
            "          }}\n"
            "        >\n"
            "          App version v{appVersion}\n"
            "        </footer>\n"
            "      "
        )
        return updated.replace("</body>", footer_block + "</body>", 1)

    def _relative_package_json_import(self, layout_file: str) -> str:
        relative_file = PurePosixPath(layout_file.replace("\\", "/"))
        package_path = PurePosixPath("package.json")
        parent = relative_file.parent
        if str(parent) in {".", ""}:
            return "./package.json"

        parent_parts = [part for part in parent.parts if part not in {"."}]
        upward = [".."] * len(parent_parts)
        return "/".join(upward + [package_path.as_posix()])

    def _frontend_design_brief(self, state: AgentState) -> dict[str, Any] | None:
        planning_context = state.get("planning_context") or {}
        design_brief = planning_context.get("design_brief")
        return design_brief if isinstance(design_brief, dict) else None

    def _build_nestjs_operations(
        self,
        state: AgentState,
        workspace_profile: dict[str, Any],
        editor: FileEditor,
    ) -> list[dict[str, Any]]:
        nestjs_profile = workspace_profile.get("nestjs")
        if not nestjs_profile:
            return []

        issue = state["issue_description"]
        lower_issue = issue.lower()
        if not re.search(r"\b(nest|nestjs|module|controller|service|dto|endpoint|api|resource|provider)\b", lower_issue):
            return []

        feature_slug = self._extract_nest_feature_slug(issue)
        feature_name = feature_slug.split("/")[-1]
        source_root = nestjs_profile.get("source_root") or "src"
        feature_dir = f"{source_root}/{feature_slug}" if feature_slug else source_root
        action = "write_file" if re.search(r"\b(update|modify|refactor|revamp|rewrite|redesign)\b", lower_issue) else "create_file"

        wants_controller = bool(re.search(r"\b(controller|endpoint|api|route|http|get|post|put|patch|delete)\b", lower_issue))
        wants_service = bool(re.search(r"\b(service|provider|logic|business)\b", lower_issue)) or wants_controller
        wants_module = bool(re.search(r"\b(module|resource|feature)\b", lower_issue)) or wants_controller or wants_service
        wants_dto = bool(re.search(r"\b(dto|payload|body|request|input|create|post|put|patch)\b", lower_issue))

        http_method = self._extract_http_method(lower_issue)
        route_path = self._extract_nest_route_path(issue, feature_name)

        operations: list[dict[str, Any]] = []
        dto_class_name = f"Create{self._to_component_name(feature_name)}Dto"
        dto_file = f"{feature_dir}/dto/create-{self._slugify(feature_name)}.dto.ts"
        service_file = f"{feature_dir}/{self._slugify(feature_name)}.service.ts"
        controller_file = f"{feature_dir}/{self._slugify(feature_name)}.controller.ts"
        module_file = f"{feature_dir}/{self._slugify(feature_name)}.module.ts"

        if wants_dto:
            operations.append(
                self._file_operation(
                    editor,
                    dto_file,
                    self._nest_dto_template(dto_class_name, issue),
                    preferred_action=action,
                )
            )

        if wants_service:
            operations.append(
                self._file_operation(
                    editor,
                    service_file,
                    self._nest_service_template(feature_name, dto_class_name, wants_dto, http_method),
                    preferred_action=action,
                )
            )

        if wants_controller:
            operations.append(
                self._file_operation(
                    editor,
                    controller_file,
                    self._nest_controller_template(feature_name, route_path, dto_class_name, wants_dto, http_method),
                    preferred_action=action,
                )
            )

        if wants_module:
            operations.append(
                self._file_operation(
                    editor,
                    module_file,
                    self._nest_module_template(feature_name, wants_controller, wants_service),
                    preferred_action=action,
                )
            )

        app_module_operation = self._build_nest_app_module_operation(editor, nestjs_profile, feature_name, module_file)
        if app_module_operation is not None:
            operations.append(app_module_operation)

        deduplicated: list[dict[str, Any]] = []
        seen_paths: set[str] = set()
        for operation in operations:
            file_path = operation.get("file_path")
            if not file_path or file_path in seen_paths:
                continue
            deduplicated.append(operation)
            seen_paths.add(file_path)

        return deduplicated

    def _next_gitignore_template(self) -> str:
        return "\n".join(
            [
                "node_modules",
                ".next",
                "out",
                "coverage",
                "dist",
                "*.tsbuildinfo",
                ".env.local",
                ".env.development.local",
                ".env.test.local",
                ".env.production.local",
                ".ai-code-agent/",
                "",
            ]
        )

    def _file_operation(self, editor: FileEditor, file_path: str, content: str, preferred_action: str) -> dict[str, Any]:
        exists = editor.exists(file_path)
        operation_type = preferred_action if exists else "create_file"
        if operation_type == "create_file" and exists:
            operation_type = "write_file"
        return {
            "type": operation_type,
            "file_path": file_path,
            "content": content,
        }

    def _extract_route_slug(self, issue: str) -> str:
        structured_title = self._extract_issue_section(issue, "title")
        structured_description = self._extract_issue_section(issue, "description")

        candidates = [segment for segment in [structured_title, structured_description, issue] if isinstance(segment, str) and segment.strip()]
        for candidate in candidates:
            extracted = self._extract_route_slug_from_text(candidate)
            if extracted:
                return extracted
        return "feature"

    def _extract_route_slug_from_text(self, text: str) -> str | None:
        sanitized_issue = self._sanitize_issue_for_route_detection(text)
        explicit_match = re.search(r"(?:(?:path|route|url|page)\s+)?/([a-z0-9\-/]+)", sanitized_issue)
        if explicit_match:
            return explicit_match.group(1).strip("/")

        phrase_match = re.search(r"\b([a-z0-9-]+)\s+(?:page|screen|view|route|layout|api)\b", sanitized_issue)
        if phrase_match:
            return phrase_match.group(1)

        preferred_terms = [
            "github",
            "dashboard",
            "admin",
            "settings",
            "profile",
            "users",
            "billing",
            "analytics",
            "reports",
            "login",
            "signup",
        ]
        lower_issue = sanitized_issue
        for term in preferred_terms:
            if term in lower_issue:
                return term

        tokens = [token for token in re.findall(r"[a-z0-9-]+", lower_issue) if token not in {"add", "create", "update", "new", "next", "component", "page", "layout", "api", "route", "handler"}]
        return tokens[0] if tokens else None

    def _extract_issue_section(self, issue: str, label: str) -> str | None:
        if not isinstance(issue, str) or not issue.strip():
            return None
        pattern = rf"^\s*{re.escape(label)}\s*:\s*(.+)$"
        match = re.search(pattern, issue, re.IGNORECASE | re.MULTILINE)
        if not match:
            return None
        value = match.group(1).strip()
        return value or None

    def _sanitize_issue_for_route_detection(self, issue: str) -> str:
        sanitized = re.sub(r"https?://\S+", " ", issue.lower())
        sanitized = re.sub(r"\b(issue provider|source url|github issue|azure devops work item):[^\n]*", " ", sanitized)
        return sanitized

    def _preferred_next_route_slug(self, state: AgentState, nextjs_profile: dict[str, Any]) -> str:
        focus_files: list[str] = []
        for file_path in state.get("files_to_edit", []):
            if isinstance(file_path, str):
                focus_files.append(file_path)
        planning_context = state.get("planning_context") if isinstance(state.get("planning_context"), dict) else {}
        planning_scope = planning_context.get("scope") if isinstance(planning_context.get("scope"), dict) else {}
        for file_path in planning_scope.get("in_scope", []):
            if isinstance(file_path, str):
                focus_files.append(file_path)
        for task in planning_context.get("tasks", []) if isinstance(planning_context.get("tasks"), list) else []:
            if not isinstance(task, dict):
                continue
            for file_path in task.get("target_files", []):
                if isinstance(file_path, str):
                    focus_files.append(file_path)
        remediation_context = self._remediation_context(state)
        if remediation_context:
            for file_path in remediation_context.get("focus_areas", []):
                if isinstance(file_path, str):
                    focus_files.append(file_path)

        route_slug = self._route_slug_from_files(focus_files, nextjs_profile)
        if route_slug is not None:
            return route_slug
        issue_route_slug = self._extract_route_slug(state["issue_description"])
        return issue_route_slug

    def _route_slug_from_files(self, file_paths: list[str], nextjs_profile: dict[str, Any]) -> str | None:
        app_dir = (nextjs_profile.get("app_dir") or "app").replace("\\", "/")
        pages_dir = (nextjs_profile.get("pages_dir") or "pages").replace("\\", "/")
        normalized_paths = [file_path.replace("\\", "/") for file_path in file_paths if isinstance(file_path, str)]
        for normalized in normalized_paths:
            if normalized.startswith(f"{app_dir}/") and normalized.endswith("/page.tsx"):
                slug = normalized[len(app_dir) + 1 : -len("/page.tsx")]
                if slug:
                    return slug
        for normalized in normalized_paths:
            stripped = normalized.rstrip("/")
            if stripped.startswith(f"{app_dir}/") and "/" not in stripped[len(app_dir) + 1:]:
                route_segment = stripped[len(app_dir) + 1:]
                if route_segment and Path(route_segment).suffix == "":
                    return route_segment
        for file_path in file_paths:
            normalized = file_path.replace("\\", "/")
            stripped = normalized.rstrip("/")
            if stripped == app_dir:
                return ""
            if normalized == f"{app_dir}/page.tsx":
                return ""
            if normalized == f"{pages_dir}/index.tsx":
                return ""
            if normalized.startswith(f"{pages_dir}/") and normalized.endswith(".tsx"):
                return normalized[len(pages_dir) + 1 : -len(".tsx")]
        return None

    def _expand_nextjs_route_bundle_files(self, file_paths: list[str], workspace_profile: dict[str, Any]) -> list[str]:
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

    def _extract_component_request(self, issue: str, route_slug: str) -> str | None:
        specific_matches = re.findall(
            r"\b([a-z0-9-]+(?: [a-z0-9-]+){0,2})\s+(card|hero|section|form|modal|panel|table|list)\b",
            issue.lower(),
        )
        if specific_matches:
            phrase, noun = specific_matches[-1]
            return self._humanize_identifier(self._clean_component_phrase(f"{phrase} {noun}"))

        generic_matches = re.findall(
            r"\b([a-z0-9-]+(?: [a-z0-9-]+){0,2})\s+component\b",
            issue.lower(),
        )
        if generic_matches:
            return self._humanize_identifier(self._clean_component_phrase(generic_matches[-1]))

        if route_slug:
            return self._humanize_identifier(f"{route_slug.split('/')[-1]} section")
        return None

    def _clean_component_phrase(self, phrase: str) -> str:
        tokens = [
            token
            for token in re.findall(r"[a-z0-9-]+", phrase.lower())
            if token not in {"add", "create", "update", "with", "and", "page", "api", "route", "handler", "new"}
        ]
        return " ".join(tokens[-3:]) if tokens else phrase

    def _resolve_component_file(self, nextjs_profile: dict[str, Any], component_request: str | None) -> str | None:
        if component_request is None:
            return None
        component_dirs = nextjs_profile.get("component_directories", [])
        target_dir = component_dirs[0] if component_dirs else "components"
        file_name = self._slugify(component_request)
        return f"{target_dir}/{file_name}.tsx"

    def _preferred_reactflow_component_request(self, component_request: str | None, route_slug: str) -> str:
        route_section = self._humanize_identifier(f"{route_slug.split('/')[-1]} section")
        if not component_request:
            return route_section
        lowered = component_request.lower()
        if any(token in lowered for token in ["layout", "page", "route", "view", "screen"]):
            return route_section
        return component_request

    def _resolve_reactflow_component_file(self, nextjs_profile: dict[str, Any], route_slug: str) -> str | None:
        component_dirs = nextjs_profile.get("component_directories", [])
        target_dir = component_dirs[0] if component_dirs else "components"
        route_name = route_slug.split("/")[-1] if route_slug else "home"
        return f"{target_dir}/react-flow/{self._slugify(route_name)}-react-flow-workspace.tsx"

    def _resolve_graph_types_file(self) -> str:
        return "components/graph/types.ts"

    def _resolve_graph_data_file(self) -> str:
        return "components/graph/graph-data.ts"

    def _resolve_graph_component_files(self, nextjs_profile: dict[str, Any]) -> dict[str, str]:
        component_dirs = nextjs_profile.get("component_directories", [])
        target_dir = component_dirs[0] if component_dirs else "components"
        graph_dir = f"{target_dir}/graph"
        return {
            "workspace": f"{graph_dir}/GraphWorkspace.tsx",
            "legend": f"{graph_dir}/GraphLegend.tsx",
            "summary": f"{graph_dir}/GraphSummary.tsx",
            "empty": f"{graph_dir}/GraphEmptyState.tsx",
        }

    def _resolve_root_preview_file(self, nextjs_profile: dict[str, Any]) -> str | None:
        if nextjs_profile.get("router_type") == "app":
            app_dir = nextjs_profile.get("app_dir") or "app"
            return f"{app_dir}/page.tsx"
        if nextjs_profile.get("router_type") == "pages":
            pages_dir = nextjs_profile.get("pages_dir") or "pages"
            return f"{pages_dir}/index.tsx"
        return None

    def _resolve_next_page_file(self, nextjs_profile: dict[str, Any], route_slug: str) -> str | None:
        router_type = nextjs_profile.get("router_type")
        if router_type == "app":
            app_dir = nextjs_profile.get("app_dir") or "app"
            return f"{app_dir}/{route_slug}/page.tsx" if route_slug else f"{app_dir}/page.tsx"
        if router_type == "pages":
            pages_dir = nextjs_profile.get("pages_dir") or "pages"
            return f"{pages_dir}/{route_slug}.tsx" if route_slug else f"{pages_dir}/index.tsx"
        return None

    def _resolve_next_layout_file(self, nextjs_profile: dict[str, Any], route_slug: str) -> str | None:
        router_type = nextjs_profile.get("router_type")
        if router_type == "app":
            app_dir = nextjs_profile.get("app_dir") or "app"
            return f"{app_dir}/{route_slug}/layout.tsx" if route_slug else f"{app_dir}/layout.tsx"
        if router_type == "pages":
            pages_dir = nextjs_profile.get("pages_dir") or "pages"
            return f"{pages_dir}/_app.tsx"
        return None

    def _resolve_next_api_route_file(self, nextjs_profile: dict[str, Any], route_slug: str) -> str | None:
        route_slug = route_slug or "status"
        router_type = nextjs_profile.get("router_type")
        if router_type == "app":
            app_dir = nextjs_profile.get("app_dir") or "app"
            return f"{app_dir}/api/{route_slug}/route.ts"
        if router_type == "pages":
            pages_dir = nextjs_profile.get("pages_dir") or "pages"
            return f"{pages_dir}/api/{route_slug}.ts"
        return None

    def _resolve_next_special_file(self, nextjs_profile: dict[str, Any], route_slug: str, file_name: str) -> str | None:
        if nextjs_profile.get("router_type") != "app":
            return None
        app_dir = nextjs_profile.get("app_dir") or "app"
        return f"{app_dir}/{route_slug}/{file_name}" if route_slug else f"{app_dir}/{file_name}"

    def _next_page_template(
        self,
        page_file: str,
        route_slug: str,
        component_file: str | None,
        component_request: str | None,
        issue: str,
        design_brief: dict[str, Any] | None = None,
    ) -> str:
        page_name = self._humanize_identifier(route_slug or "home")
        component_name = self._to_component_name(component_request or f"{page_name} section")
        design = self._next_design_direction(issue, route_slug or component_request or page_name, design_brief)
        imports = []
        body_lines = [
            "    <main style={pageStyles.shell}>",
            "      <section style={pageStyles.hero}>",
            f"        <p style={{pageStyles.eyebrow}}>{design['eyebrow']}</p>",
            f"        <h1 style={{pageStyles.title}}>{page_name}</h1>",
            f"        <p style={{pageStyles.description}}>{design['description']}</p>",
            '        <p style={pageStyles.note}>Demo content preview until live data is connected.</p>',
            "      </section>",
        ]
        if component_file is not None:
            import_path = self._relative_import(page_file, component_file)
            imports.append(f'import {{ {component_name} }} from "{import_path}";')
            body_lines.append(f"      <{component_name} state=\"ready\" items={{sampleItems}} />")
        body_lines.append("    </main>")

        import_block = "\n".join(imports)
        if import_block:
            import_block += "\n\n"
        return (
            f"{import_block}const sampleItems = [\n"
            f"  {{ label: \"Sample metric\", value: \"{design['primary_metric']}\", detail: \"{design['metric_caption']}\" }},\n"
            f"  {{ label: \"Example trend\", value: \"{design['secondary_metric']}\", detail: \"{design['secondary_caption']}\" }},\n"
            "];\n\n"
            "const pageStyles = {\n"
            f"  shell: {{ minHeight: \"100vh\", padding: \"4rem 1.5rem\", background: \"{design['background']}\", color: \"{design['text']}\" }},\n"
            f"  hero: {{ maxWidth: \"56rem\", margin: \"0 auto 2rem\", padding: \"2rem\", borderRadius: \"28px\", background: \"{design['panel']}\", boxShadow: \"0 24px 60px rgba(15, 23, 42, 0.12)\" }},\n"
            f"  eyebrow: {{ margin: 0, textTransform: \"uppercase\", letterSpacing: \"0.18em\", fontSize: \"0.72rem\", color: \"{design['accent']}\" }},\n"
            "  title: { margin: \"0.75rem 0 0\", fontSize: \"clamp(2.5rem, 6vw, 4.75rem)\", lineHeight: 0.95 },\n"
            f"  description: {{ maxWidth: \"42rem\", margin: \"1rem 0 0\", fontSize: \"1.05rem\", color: \"{design['muted']}\" }},\n"
            f"  note: {{ margin: \"1rem 0 0\", fontSize: \"0.9rem\", color: \"{design['muted']}\" }},\n"
            "};\n\n"
            f"export default function {self._to_component_name(page_name)}Page() {{\n"
            "  return (\n"
            + "\n".join(body_lines)
            + "\n  );\n"
            "}\n"
        )

    def _next_layout_template(self, route_slug: str, issue: str, design_brief: dict[str, Any] | None = None) -> str:
        section_name = self._humanize_identifier(route_slug or "app")
        design = self._next_design_direction(issue, route_slug or section_name, design_brief)
        return (
            'import type { ReactNode } from "react";\n\n'
            "type LayoutProps = {\n"
            "  children: ReactNode;\n"
            "};\n\n"
            "const layoutStyles = {\n"
            f"  shell: {{ minHeight: \"100vh\", background: \"{design['background']}\", color: \"{design['text']}\" }},\n"
            f"  header: {{ maxWidth: \"72rem\", margin: \"0 auto\", padding: \"1.5rem 1.5rem 0\" }},\n"
            f"  eyebrow: {{ margin: 0, textTransform: \"uppercase\", letterSpacing: \"0.18em\", fontSize: \"0.72rem\", color: \"{design['accent']}\" }},\n"
            "  title: { margin: \"0.35rem 0 0\", fontSize: \"1.5rem\" },\n"
            "  content: { maxWidth: \"72rem\", margin: \"0 auto\", padding: \"1.5rem\" },\n"
            "};\n\n"
            f"export default function {self._to_component_name(section_name)}Layout({{ children }}: LayoutProps) {{\n"
            "  return (\n"
            "    <section style={layoutStyles.shell}>\n"
            "      <header style={layoutStyles.header}>\n"
            f"        <p style={{layoutStyles.eyebrow}}>{design['eyebrow']}</p>\n"
            f"        <h1 style={{layoutStyles.title}}>{section_name}</h1>\n"
            "      </header>\n"
            "      <div style={layoutStyles.content}>{children}</div>\n"
            "    </section>\n"
            "  );\n"
            "}\n"
        )

    def _next_component_template(
        self,
        component_request: str,
        issue: str,
        route_slug: str,
        design_brief: dict[str, Any] | None = None,
        reactflow_component_file: str | None = None,
    ) -> str:
        component_name = self._to_component_name(component_request)
        title = self._humanize_identifier(component_request)
        design = self._next_design_direction(issue, route_slug or component_request, design_brief)
        if reactflow_component_file is not None:
            return self._next_reactflow_section_template(
                component_name,
                title,
                issue,
                route_slug,
                reactflow_component_file,
                design_brief,
            )
        return (
            f"type {component_name}State = \"loading\" | \"empty\" | \"error\" | \"ready\";\n\n"
            f"type {component_name}Item = {{\n"
            "  label: string;\n"
            "  value: string;\n"
            "  detail?: string;\n"
            "};\n\n"
            f"type {component_name}Props = {{\n"
            f"  state?: {component_name}State;\n"
            f"  items?: {component_name}Item[];\n"
            "};\n\n"
            "const sectionStyles = {\n"
            f"  shell: {{ borderRadius: \"28px\", padding: \"1.5rem\", background: \"{design['panel']}\", color: \"{design['text']}\", boxShadow: \"0 24px 60px rgba(15, 23, 42, 0.12)\" }},\n"
            f"  eyebrow: {{ margin: 0, textTransform: \"uppercase\", letterSpacing: \"0.16em\", fontSize: \"0.72rem\", color: \"{design['accent']}\" }},\n"
            "  title: { margin: \"0.5rem 0 0\", fontSize: \"1.5rem\" },\n"
            f"  description: {{ margin: \"0.75rem 0 0\", color: \"{design['muted']}\" }},\n"
            "  grid: { display: \"grid\", gap: \"1rem\", gridTemplateColumns: \"repeat(auto-fit, minmax(12rem, 1fr))\", marginTop: \"1.5rem\" },\n"
            f"  card: {{ padding: \"1rem\", borderRadius: \"20px\", background: \"{design['surface']}\" }},\n"
            f"  value: {{ margin: \"0.35rem 0 0\", fontSize: \"1.9rem\", color: \"{design['text']}\" }},\n"
            f"  detail: {{ margin: \"0.5rem 0 0\", color: \"{design['muted']}\", fontSize: \"0.95rem\" }},\n"
            f"  statePanel: {{ marginTop: \"1.5rem\", padding: \"1.25rem\", borderRadius: \"20px\", background: \"{design['surface']}\", color: \"{design['muted']}\" }},\n"
            "};\n\n"
            f"export function {component_name}({{ state = \"ready\", items = [] }}: {component_name}Props) {{\n"
            "  if (state === \"loading\") {\n"
            "    return (\n"
            "      <section style={sectionStyles.shell}>\n"
            f"        <p style={{sectionStyles.eyebrow}}>{design['eyebrow']}</p>\n"
            f"        <h2 style={{sectionStyles.title}}>{title}</h2>\n"
            f"        <p style={{sectionStyles.description}}>{design['loading_copy']}</p>\n"
            "      </section>\n"
            "    );\n"
            "  }\n\n"
            f"  if (state === \"error\") {{\n"
            "    return (\n"
            "      <section style={sectionStyles.shell}>\n"
            f"        <p style={{sectionStyles.eyebrow}}>{design['eyebrow']}</p>\n"
            f"        <h2 style={{sectionStyles.title}}>{title}</h2>\n"
            f"        <div style={{sectionStyles.statePanel}}>{design['error_copy']}</div>\n"
            "      </section>\n"
            "    );\n"
            "  }\n\n"
            "  if (state === \"empty\" || items.length === 0) {\n"
            "    return (\n"
            "      <section style={sectionStyles.shell}>\n"
            f"        <p style={{sectionStyles.eyebrow}}>{design['eyebrow']}</p>\n"
            f"        <h2 style={{sectionStyles.title}}>{title}</h2>\n"
            f"        <div style={{sectionStyles.statePanel}}>{design['empty_copy']}</div>\n"
            "      </section>\n"
            "    );\n"
            "  }\n\n"
            "  return (\n"
            "    <section style={sectionStyles.shell}>\n"
            f"      <p style={{sectionStyles.eyebrow}}>{design['eyebrow']}</p>\n"
            f"      <h2 style={{sectionStyles.title}}>{title}</h2>\n"
            f"      <p style={{sectionStyles.description}}>{design['success_copy']}</p>\n"
            "      <div style={sectionStyles.grid}>\n"
            "        {items.map((item) => (\n"
            "          <article key={item.label} style={sectionStyles.card}>\n"
            "            <p style={sectionStyles.eyebrow}>{item.label}</p>\n"
            "            <p style={sectionStyles.value}>{item.value}</p>\n"
            "            {item.detail ? <p style={sectionStyles.detail}>{item.detail}</p> : null}\n"
            "          </article>\n"
            "        ))}\n"
            "      </div>\n"
            "    </section>\n"
            "  );\n"
            "}\n"
        )

    def _next_loading_template(self, issue: str, route_slug: str, design_brief: dict[str, Any] | None = None) -> str:
        design = self._next_design_direction(issue, route_slug or "feature", design_brief)
        return (
            "export default function Loading() {\n"
            "  return (\n"
            f"    <div style={{{{ minHeight: \"40vh\", display: \"grid\", placeItems: \"center\", padding: \"2rem\", borderRadius: \"24px\", background: \"{design['panel']}\", color: \"{design['muted']}\" }}}}>\n"
            f"      <p>{design['loading_copy']}</p>\n"
            "    </div>\n"
            "  );\n"
            "}\n"
        )

    def _next_error_template(self, issue: str, route_slug: str, design_brief: dict[str, Any] | None = None) -> str:
        design = self._next_design_direction(issue, route_slug or "feature", design_brief)
        return (
            '"use client";\n\n'
            'type ErrorProps = {\n'
            '  error: Error;\n'
            '  reset: () => void;\n'
            '};\n\n'
            "export default function ErrorBoundary({ error: _error, reset }: ErrorProps) {\n"
            "  return (\n"
            f"    <div style={{{{ padding: \"2rem\", borderRadius: \"24px\", background: \"{design['panel']}\", color: \"{design['text']}\" }}}}>\n"
            f"      <h2>{design['error_title']}</h2>\n"
            f"      <p style={{{{ color: \"{design['muted']}\" }}}}>{design['error_copy']}</p>\n"
            '      <p style={{ color: "#b91c1c" }}>Try again, and if the problem continues, inspect the latest logs or server response.</p>\n'
            '      <button type="button" onClick={reset} style={{ marginTop: "1rem" }}>Try again</button>\n'
            "    </div>\n"
            "  );\n"
            "}\n"
        )

    def _next_design_direction(self, issue: str, context_hint: str, design_brief: dict[str, Any] | None = None) -> dict[str, str]:
        style_family = (design_brief or {}).get("style_family")
        lower_issue = f"{issue} {context_hint}".lower()
        if style_family == "dashboard" or re.search(r"\b(dashboard|analytics|metric|report|signal)\b", lower_issue):
            design = {
                "eyebrow": "Signal-rich dashboard",
                "description": "A bold control room layout with clear hierarchy, warm surfaces, and decisive contrast.",
                "background": "linear-gradient(180deg, #f5efe4 0%, #ebe4d8 100%)",
                "panel": "#fffaf0",
                "surface": "#f0e7d8",
                "accent": "#0f766e",
                "text": "#1f2937",
                "muted": "#5b6470",
                "primary_metric": "Example value",
                "secondary_metric": "Sample trend",
                "metric_caption": "Placeholder content to show hierarchy before live data is connected.",
                "secondary_caption": "Replace with a real data source or clearer product copy before shipping.",
                "loading_copy": "Loading the latest signals and arranging the board.",
                "empty_copy": "No highlights are available yet. Connect a data source or publish the first event.",
                "error_title": "Something interrupted the signal feed",
                "error_copy": "The page is intact, but the live content could not be refreshed just now.",
                "success_copy": "Designed to surface the strongest numbers first while still leaving room for narrative context.",
            }
        elif style_family == "calm" or re.search(r"\b(login|auth|profile|account|setting)\b", lower_issue):
            design = {
                "eyebrow": "Calm product surface",
                "description": "A restrained layout with soft contrast, deliberate spacing, and a calmer tone for high-focus flows.",
                "background": "linear-gradient(180deg, #f3f7f6 0%, #e6efec 100%)",
                "panel": "#ffffff",
                "surface": "#eef6f4",
                "accent": "#0f766e",
                "text": "#16302b",
                "muted": "#56716a",
                "primary_metric": "Ready",
                "secondary_metric": "Secure",
                "metric_caption": "Primary account action is available.",
                "secondary_caption": "Access and profile controls are in sync.",
                "loading_copy": "Preparing the workspace and verifying account context.",
                "empty_copy": "There is nothing to show yet. Start by adding the first account detail.",
                "error_title": "We could not finish setting up this view",
                "error_copy": "The shell loaded correctly, but a critical account detail failed to arrive.",
                "success_copy": "Built for focus-heavy product flows with clear copy and generous breathing room.",
            }
        else:
            design = {
                "eyebrow": "Editorial product surface",
                "description": "A modern product page with higher contrast, stronger typography, and clear state transitions.",
                "background": "linear-gradient(180deg, #f6f1ea 0%, #e8ddd0 100%)",
                "panel": "#fffaf5",
                "surface": "#f1e4d4",
                "accent": "#b45309",
                "text": "#2f241c",
                "muted": "#6b5a4a",
                "primary_metric": "Example value",
                "secondary_metric": "Sample status",
                "metric_caption": "Use this area for clearly labeled sample content or real data.",
                "secondary_caption": "Avoid authoritative-looking operational numbers until the surface is wired to live data.",
                "loading_copy": "Preparing the surface and staging the first interaction states.",
                "empty_copy": "This section is ready, but it has no content yet. Add the first record to bring it to life.",
                "error_title": "This section needs another pass",
                "error_copy": "The shell is present, but the content layer hit an unexpected error.",
                "success_copy": "Structured for a more intentional first impression than a plain scaffold.",
            }

        visual_tone = (design_brief or {}).get("visual_tone")
        if isinstance(visual_tone, str) and visual_tone.strip():
            design["eyebrow"] = self._humanize_identifier(visual_tone)

        palette_hint = (design_brief or {}).get("palette_hint")
        if palette_hint == "cool":
            design.update(
                {
                    "background": "linear-gradient(180deg, #eef6f8 0%, #dbe9ef 100%)",
                    "panel": "#f7fbfc",
                    "surface": "#deedf1",
                    "accent": "#0f766e",
                    "text": "#16343a",
                    "muted": "#58727a",
                }
            )
        elif palette_hint == "neutral":
            design.update(
                {
                    "background": "linear-gradient(180deg, #f5f5f4 0%, #e7e5e4 100%)",
                    "panel": "#fafaf9",
                    "surface": "#eceae7",
                    "accent": "#57534e",
                    "text": "#292524",
                    "muted": "#6b7280",
                }
            )

        return design

    def _issue_requests_react_flow(self, issue: str) -> bool:
        lowered = issue.lower()
        return bool(re.search(r"react\s*flow|reactflow", lowered)) or "graph workspace" in lowered

    def _next_reactflow_section_template(
        self,
        component_name: str,
        title: str,
        issue: str,
        route_slug: str,
        reactflow_component_file: str,
        design_brief: dict[str, Any] | None = None,
    ) -> str:
        design = self._next_design_direction(issue, route_slug or title, design_brief)
        import_path = self._relative_import(
            f"components/{self._slugify(component_name)}.tsx",
            reactflow_component_file,
        )
        workspace_component = Path(reactflow_component_file).stem
        workspace_component_name = self._to_component_name(workspace_component)
        return (
            f'import {{ {workspace_component_name} }} from "{import_path}";\n\n'
            f"type {component_name}State = \"loading\" | \"empty\" | \"error\" | \"ready\";\n\n"
            f"type {component_name}Item = {{\n"
            "  label: string;\n"
            "  value: string;\n"
            "  detail?: string;\n"
            "};\n\n"
            f"type {component_name}Props = {{\n"
            f"  state?: {component_name}State;\n"
            f"  items?: {component_name}Item[];\n"
            "};\n\n"
            "const sectionStyles = {\n"
            f"  shell: {{ borderRadius: \"28px\", padding: \"1.5rem\", background: \"{design['panel']}\", color: \"{design['text']}\", boxShadow: \"0 24px 60px rgba(15, 23, 42, 0.12)\" }},\n"
            f"  eyebrow: {{ margin: 0, textTransform: \"uppercase\", letterSpacing: \"0.16em\", fontSize: \"0.72rem\", color: \"{design['accent']}\" }},\n"
            "  title: { margin: \"0.5rem 0 0\", fontSize: \"1.5rem\" },\n"
            f"  description: {{ margin: \"0.75rem 0 0\", color: \"{design['muted']}\" }},\n"
            f"  statePanel: {{ marginTop: \"1.5rem\", padding: \"1.25rem\", borderRadius: \"20px\", background: \"{design['surface']}\", color: \"{design['muted']}\" }},\n"
            "  workspace: { marginTop: \"1.5rem\" },\n"
            "};\n\n"
            f"export function {component_name}({{ state = \"ready\", items = [] }}: {component_name}Props) {{\n"
            "  if (state === \"loading\") {\n"
            "    return (\n"
            "      <section style={sectionStyles.shell}>\n"
            f"        <p style={{sectionStyles.eyebrow}}>{design['eyebrow']}</p>\n"
            f"        <h2 style={{sectionStyles.title}}>{title}</h2>\n"
            f"        <p style={{sectionStyles.description}}>{design['loading_copy']}</p>\n"
            "      </section>\n"
            "    );\n"
            "  }\n\n"
            "  if (state === \"error\") {\n"
            "    return (\n"
            "      <section style={sectionStyles.shell}>\n"
            f"        <p style={{sectionStyles.eyebrow}}>{design['eyebrow']}</p>\n"
            f"        <h2 style={{sectionStyles.title}}>{title}</h2>\n"
            f"        <div style={{sectionStyles.statePanel}}>{design['error_copy']}</div>\n"
            "      </section>\n"
            "    );\n"
            "  }\n\n"
            "  if (state === \"empty\" || items.length === 0) {\n"
            "    return (\n"
            "      <section style={sectionStyles.shell}>\n"
            f"        <p style={{sectionStyles.eyebrow}}>{design['eyebrow']}</p>\n"
            f"        <h2 style={{sectionStyles.title}}>{title}</h2>\n"
            f"        <div style={{sectionStyles.statePanel}}>{design['empty_copy']}</div>\n"
            "      </section>\n"
            "    );\n"
            "  }\n\n"
            "  return (\n"
            "    <section style={sectionStyles.shell}>\n"
            f"      <p style={{sectionStyles.eyebrow}}>{design['eyebrow']}</p>\n"
            f"      <h2 style={{sectionStyles.title}}>{title}</h2>\n"
            f"      <p style={{sectionStyles.description}}>{design['success_copy']}</p>\n"
            "      <div style={sectionStyles.workspace}>\n"
            f"        <{workspace_component_name} />\n"
            "      </div>\n"
            "    </section>\n"
            "  );\n"
            "}\n"
        )

    def _next_reactflow_workspace_template(self, route_slug: str, issue: str, design_brief: dict[str, Any] | None = None) -> str:
        design = self._next_design_direction(issue, route_slug or "graph", design_brief)
        workspace_name = self._to_component_name(f"{route_slug.split('/')[-1] if route_slug else 'home'} react flow workspace")
        return (
            '"use client";\n\n'
            'import { useMemo, useState } from "react";\n'
            'import ReactFlow, { Background, Controls, MarkerType, MiniMap, type Edge, type Node, type OnSelectionChangeParams } from "reactflow";\n'
            'import "reactflow/dist/style.css";\n\n'
            'import { graphEdges, graphNodes, graphToneByKind } from "../graph/graph-data";\n'
            'import type { GraphNodeData, GraphNodeKind } from "../graph/types";\n\n'
            'const toneByKind: Record<GraphNodeKind, { background: string; color: string; border: string }> = graphToneByKind;\n\n'
            'const nodes: Node<GraphNodeData>[] = graphNodes.map((node) => ({\n'
            '  ...node,\n'
            '  style: { ...toneByKind[node.data.kind], borderRadius: 18, padding: 12, width: node.style?.width ?? 180, boxShadow: node.data.kind === "pipeline" ? "0 10px 30px rgba(47, 36, 28, 0.08)" : undefined },\n'
            '}));\n\n'
            'const edges: Edge[] = graphEdges.map((edge) => ({\n'
            '  ...edge,\n'
            '  markerEnd: { type: MarkerType.ArrowClosed },\n'
            '}));\n\n'
            'const styles = {\n'
            '  shell: { display: "grid", gap: "1rem" },\n'
            '  frame: { height: "min(68vh, 42rem)", minHeight: "26rem", borderRadius: "28px", overflow: "hidden", border: "1px solid rgba(139, 94, 60, 0.18)", background: "linear-gradient(180deg, rgba(255, 250, 245, 0.98) 0%, rgba(245, 237, 227, 0.96) 100%)", boxShadow: "0 24px 60px rgba(47, 36, 28, 0.1)" },\n'
            '  detailGrid: { display: "grid", gap: "1rem", gridTemplateColumns: "repeat(auto-fit, minmax(15rem, 1fr))" },\n'
            '  detailCard: { borderRadius: "22px", padding: "1rem", background: "rgba(255, 250, 245, 0.92)", border: "1px solid rgba(139, 94, 60, 0.14)" },\n'
            f'  label: {{ margin: 0, textTransform: "uppercase", letterSpacing: "0.14em", fontSize: "0.7rem", color: "{design["accent"]}" }},\n'
            f'  value: {{ margin: "0.5rem 0 0", fontSize: "1.1rem", color: "{design["text"]}", fontWeight: 700 }},\n'
            f'  small: {{ margin: "0.5rem 0 0", color: "{design["muted"]}", lineHeight: 1.55 }},\n'
            '};\n\n'
            f'export function {workspace_name}() {{\n'
            '  const [selectedNodeId, setSelectedNodeId] = useState<string>("pipeline");\n\n'
            '  const fallbackNode = nodes.find((node) => node.id === "pipeline") ?? nodes[0];\n'
            '  const selectedNode = useMemo<Node<GraphNodeData>>(() => nodes.find((node) => node.id === selectedNodeId) ?? fallbackNode, [selectedNodeId]);\n\n'
            '  const handleSelectionChange = ({ nodes: selectedNodes }: OnSelectionChangeParams) => {\n'
            '    const firstSelectedNode = selectedNodes[0];\n'
            '    if (firstSelectedNode) {\n'
            '      setSelectedNodeId(firstSelectedNode.id);\n'
            '    }\n'
            '  };\n\n'
            '  return (\n'
            '    <section style={styles.shell}>\n'
            '      <div style={styles.frame}>\n'
            '        <ReactFlow\n'
            '          nodes={nodes}\n'
            '          edges={edges}\n'
            '          fitView\n'
            '          minZoom={0.6}\n'
            '          maxZoom={1.4}\n'
            '          nodesDraggable={false}\n'
            '          nodesConnectable={false}\n'
            '          onSelectionChange={handleSelectionChange}\n'
            '          proOptions={{ hideAttribution: true }}\n'
            '        >\n'
            '          <MiniMap pannable zoomable style={{ background: "rgba(255,250,245,0.95)", border: "1px solid rgba(139,94,60,0.14)" }} />\n'
            '          <Controls showInteractive={false} />\n'
            '          <Background color="#d6c4b2" gap={18} />\n'
            '        </ReactFlow>\n'
            '      </div>\n'
            '      <div style={styles.detailGrid}>\n'
            '        <article style={styles.detailCard}>\n'
            '          <p style={styles.label}>Selected node</p>\n'
            '          <p style={styles.value}>{selectedNode.data.label}</p>\n'
            '          <p style={styles.small}>{selectedNode.data.summary}</p>\n'
            '        </article>\n'
            '        <article style={styles.detailCard}>\n'
            '          <p style={styles.label}>Demo note</p>\n'
            '          <p style={styles.small}>This workspace uses typed sample data to demonstrate the React Flow surface until a live data source is connected.</p>\n'
            '        </article>\n'
            '      </div>\n'
            '    </section>\n'
            '  );\n'
            '}\n'
        )

    def _graph_types_template(self) -> str:
        return (
            'export type GraphNodeKind = "source" | "pipeline" | "destination";\n\n'
            'export type GraphNodeData = {\n'
            '  label: string;\n'
            '  summary: string;\n'
            '  kind: GraphNodeKind;\n'
            '};\n\n'
            'export type GraphSummaryItem = {\n'
            '  label: string;\n'
            '  value: string;\n'
            '  detail: string;\n'
            '};\n'
        )

    def _graph_data_template(self) -> str:
        return (
            'import type { Edge, Node } from "reactflow";\n'
            'import type { GraphNodeData, GraphNodeKind, GraphSummaryItem } from "./types";\n\n'
            'export const graphToneByKind: Record<GraphNodeKind, { background: string; color: string; border: string }> = {\n'
            '  source: { background: "#fff7ed", color: "#7c2d12", border: "1px solid rgba(194, 120, 3, 0.28)" },\n'
            '  pipeline: { background: "#fffaf5", color: "#2f241c", border: "1px solid rgba(139, 94, 60, 0.24)" },\n'
            '  destination: { background: "#ecfccb", color: "#365314", border: "1px solid rgba(101, 163, 13, 0.26)" },\n'
            '};\n\n'
            'export const graphSummaryItems: GraphSummaryItem[] = [\n'
            '  { label: "Sample metric", value: "Example value", detail: "Use this area for clearly labeled sample content or real data." },\n'
            '  { label: "Example trend", value: "Sample status", detail: "Avoid authoritative-looking operational numbers until the surface is wired to live data." },\n'
            '];\n\n'
            'export const graphNodes: Node<GraphNodeData>[] = [\n'
            '  { id: "source", position: { x: 40, y: 140 }, data: { label: "Source", summary: "Incoming signals or repository events.", kind: "source" }, style: { width: 170 } },\n'
            '  { id: "pipeline", position: { x: 320, y: 120 }, data: { label: "Pipeline", summary: "Validation, orchestration, and review flow.", kind: "pipeline" }, style: { width: 190 } },\n'
            '  { id: "destination", position: { x: 640, y: 140 }, data: { label: "Destination", summary: "Published UI or downstream delivery target.", kind: "destination" }, style: { width: 180 } },\n'
            '];\n\n'
            'export const graphEdges: Edge[] = [\n'
            '  { id: "source-pipeline", source: "source", target: "pipeline", label: "validated", style: { stroke: "#8b5e3c", strokeWidth: 1.8 } },\n'
            '  { id: "pipeline-destination", source: "pipeline", target: "destination", label: "delivers", style: { stroke: "#4d7c0f", strokeWidth: 1.8 } },\n'
            '];\n'
        )

    def _graph_support_operations(self, graph_component_files: dict[str, str], reactflow_component_file: str) -> dict[str, str]:
        if not graph_component_files:
            return {}
        return {
            graph_component_files["empty"]: self._graph_empty_state_template(),
            graph_component_files["legend"]: self._graph_legend_template(),
            graph_component_files["summary"]: self._graph_summary_template(),
            graph_component_files["workspace"]: self._graph_workspace_wrapper_template(
                graph_component_files,
                reactflow_component_file,
            ),
        }

    def _graph_empty_state_template(self) -> str:
        return (
            'export function GraphEmptyState() {\n'
            '  return (\n'
            '    <div style={{ borderRadius: "22px", padding: "1.25rem", background: "#f1e4d4", color: "#6b5a4a" }}>\n'
            '      This graph surface is ready, but it has no live content yet. Connect a data source to replace the sample workspace.\n'
            '    </div>\n'
            '  );\n'
            '}\n'
        )

    def _graph_legend_template(self) -> str:
        return (
            'import { graphToneByKind } from "./graph-data";\n\n'
            'export function GraphLegend() {\n'
            '  return (\n'
            '    <div style={{ display: "grid", gap: "0.75rem", gridTemplateColumns: "repeat(auto-fit, minmax(12rem, 1fr))" }}>\n'
            '      {Object.entries(graphToneByKind).map(([key, tone]) => (\n'
            '        <div key={key} style={{ borderRadius: "18px", padding: "0.9rem", ...tone }}>\n'
            '          <strong style={{ textTransform: "capitalize" }}>{key}</strong>\n'
            '        </div>\n'
            '      ))}\n'
            '    </div>\n'
            '  );\n'
            '}\n'
        )

    def _graph_summary_template(self) -> str:
        return (
            'import { graphSummaryItems } from "./graph-data";\n\n'
            'export function GraphSummary() {\n'
            '  return (\n'
            '    <div style={{ display: "grid", gap: "1rem", gridTemplateColumns: "repeat(auto-fit, minmax(12rem, 1fr))" }}>\n'
            '      {graphSummaryItems.map((item) => (\n'
            '        <article key={item.label} style={{ borderRadius: "20px", padding: "1rem", background: "#f1e4d4" }}>\n'
            '          <p style={{ margin: 0, textTransform: "uppercase", letterSpacing: "0.14em", fontSize: "0.72rem", color: "#b45309" }}>{item.label}</p>\n'
            '          <p style={{ margin: "0.4rem 0 0", fontSize: "1.15rem", color: "#2f241c", fontWeight: 700 }}>{item.value}</p>\n'
            '          <p style={{ margin: "0.5rem 0 0", color: "#6b5a4a" }}>{item.detail}</p>\n'
            '        </article>\n'
            '      ))}\n'
            '    </div>\n'
            '  );\n'
            '}\n'
        )

    def _graph_workspace_wrapper_template(self, graph_component_files: dict[str, str], reactflow_component_file: str) -> str:
        wrapper_file = graph_component_files["workspace"]
        import_workspace = self._relative_import(wrapper_file, reactflow_component_file)
        import_legend = self._relative_import(wrapper_file, graph_component_files["legend"])
        import_summary = self._relative_import(wrapper_file, graph_component_files["summary"])
        import_empty = self._relative_import(wrapper_file, graph_component_files["empty"])
        workspace_component_name = self._to_component_name(Path(reactflow_component_file).stem)
        return (
            f'import {{ {workspace_component_name} }} from "{import_workspace}";\n'
            f'import {{ GraphLegend }} from "{import_legend}";\n'
            f'import {{ GraphSummary }} from "{import_summary}";\n'
            f'import {{ GraphEmptyState }} from "{import_empty}";\n\n'
            'type GraphWorkspaceProps = {\n'
            '  hasContent?: boolean;\n'
            '};\n\n'
            'export function GraphWorkspace({ hasContent = true }: GraphWorkspaceProps) {\n'
            '  if (!hasContent) {\n'
            '    return <GraphEmptyState />;\n'
            '  }\n\n'
            '  return (\n'
            '    <div style={{ display: "grid", gap: "1rem" }}>\n'
            '      <GraphSummary />\n'
            '      <GraphLegend />\n'
            f'      <{workspace_component_name} />\n'
            '    </div>\n'
            '  );\n'
            '}\n'
        )

    def _next_graph_home_preview_template(self, route_slug: str, issue: str, design_brief: dict[str, Any] | None = None) -> str:
        design = self._next_design_direction(issue, route_slug or "graph", design_brief)
        destination = f"/{route_slug}" if route_slug else "/"
        return (
            'import Link from "next/link";\n\n'
            'const homeStyles = {\n'
            f'  shell: {{ minHeight: "100vh", padding: "4rem 1.5rem", background: "{design["background"]}", color: "{design["text"]}" }},\n'
            f'  panel: {{ maxWidth: "56rem", margin: "0 auto", padding: "2rem", borderRadius: "28px", background: "{design["panel"]}", boxShadow: "0 24px 60px rgba(15, 23, 42, 0.12)" }},\n'
            f'  eyebrow: {{ margin: 0, textTransform: "uppercase", letterSpacing: "0.18em", fontSize: "0.72rem", color: "{design["accent"]}" }},\n'
            '  title: { margin: "0.75rem 0 0", fontSize: "clamp(2.4rem, 6vw, 4.5rem)", lineHeight: 0.95 },\n'
            f'  description: {{ maxWidth: "42rem", margin: "1rem 0 0", fontSize: "1.05rem", color: "{design["muted"]}" }},\n'
            '  link: { display: "inline-flex", marginTop: "1.5rem", padding: "0.85rem 1.15rem", borderRadius: "999px", textDecoration: "none", background: "#2f241c", color: "#fffaf5", fontWeight: 600 },\n'
            '};\n\n'
            'export default function HomePage() {\n'
            '  return (\n'
            '    <main style={homeStyles.shell}>\n'
            '      <section style={homeStyles.panel}>\n'
            '        <p style={homeStyles.eyebrow}>Graph preview</p>\n'
            '        <h1 style={homeStyles.title}>SmartFarm operations, mapped.</h1>\n'
            '        <p style={homeStyles.description}>Open the graph experience to inspect the relationship map, supporting summaries, and state-aware UI that now live in the dedicated route.</p>\n'
            f'        <Link href="{destination}" style={{homeStyles.link}}>Open graph experience</Link>\n'
            '      </section>\n'
            '    </main>\n'
            '  );\n'
            '}\n'
        )

    def _next_api_route_template(self, nextjs_profile: dict[str, Any], route_slug: str) -> str:
        route_name = route_slug or "status"
        if nextjs_profile.get("router_type") == "app":
            return (
                'import { NextResponse } from "next/server";\n\n'
                "export async function GET() {\n"
                "  return NextResponse.json({\n"
                '    ok: true,\n'
                f'    route: "{route_name}",\n'
                "  });\n"
                "}\n"
            )
        return (
            'import type { NextApiRequest, NextApiResponse } from "next";\n\n'
            "export default function handler(req: NextApiRequest, res: NextApiResponse) {\n"
            "  res.status(200).json({\n"
            '    ok: true,\n'
            f'    route: "{route_name}",\n'
            '    method: req.method ?? "GET",\n'
            "  });\n"
            "}\n"
        )

    def _extract_nest_feature_slug(self, issue: str) -> str:
        explicit_route = re.search(r"/(?:api/)?([a-z0-9\-/]+)", issue.lower())
        if explicit_route:
            return explicit_route.group(1).strip("/")

        noun_match = re.search(
            r"\b([a-z0-9-]+)\s+(?:module|controller|service|dto|endpoint|resource|api)\b",
            issue.lower(),
        )
        if noun_match:
            return noun_match.group(1)

        preferred_terms = [
            "users",
            "auth",
            "orders",
            "billing",
            "products",
            "notifications",
            "reports",
            "analytics",
            "profile",
        ]
        lower_issue = issue.lower()
        for term in preferred_terms:
            if term in lower_issue:
                return term

        tokens = [
            token
            for token in re.findall(r"[a-z0-9-]+", lower_issue)
            if token not in {"add", "create", "update", "new", "nest", "nestjs", "module", "controller", "service", "dto", "endpoint", "api", "resource"}
        ]
        return tokens[0] if tokens else "feature"

    def _extract_nest_route_path(self, issue: str, fallback: str) -> str:
        explicit_route = re.search(r"/(?:api/)?([a-z0-9\-/]+)", issue.lower())
        if explicit_route:
            return explicit_route.group(1).strip("/")
        return self._slugify(fallback)

    def _extract_http_method(self, issue: str) -> str:
        if re.search(r"\b(post|create)\b", issue):
            return "POST"
        if re.search(r"\b(put|replace)\b", issue):
            return "PUT"
        if re.search(r"\b(patch|update)\b", issue):
            return "PATCH"
        if re.search(r"\b(delete|remove)\b", issue):
            return "DELETE"
        return "GET"

    def _nest_module_template(self, feature_name: str, has_controller: bool, has_service: bool) -> str:
        class_name = self._to_component_name(feature_name)
        controller_import = f'import {{ {class_name}Controller }} from "./{self._slugify(feature_name)}.controller";\n' if has_controller else ""
        service_import = f'import {{ {class_name}Service }} from "./{self._slugify(feature_name)}.service";\n' if has_service else ""
        controllers = f"[{class_name}Controller]" if has_controller else "[]"
        providers = f"[{class_name}Service]" if has_service else "[]"
        return (
            'import { Module } from "@nestjs/common";\n'
            f"{controller_import}"
            f"{service_import}"
            "\n"
            "@Module({\n"
            f"  controllers: {controllers},\n"
            f"  providers: {providers},\n"
            "})\n"
            f"export class {class_name}Module {{}}\n"
        )

    def _nest_controller_template(
        self,
        feature_name: str,
        route_path: str,
        dto_class_name: str,
        has_dto: bool,
        http_method: str,
    ) -> str:
        class_name = self._to_component_name(feature_name)
        decorators = ["Controller"]
        imports = ["Controller"]
        method_name = "list"
        service_call = f"this.{self._camel_case(feature_name)}Service.list()"
        method_signature = f"get{class_name}()"
        body_argument = ""

        if http_method == "POST":
            decorators.append("Post")
            imports.extend(["Body", "Post"])
            method_name = "create"
            service_call = f"this.{self._camel_case(feature_name)}Service.create(input)"
            method_signature = f"create{class_name}(@Body() input: {dto_class_name})"
            body_argument = f'import {{ {dto_class_name} }} from "./dto/create-{self._slugify(feature_name)}.dto";\n'
        elif http_method == "PUT":
            decorators.append("Put")
            imports.extend(["Body", "Put"])
            method_name = "replace"
            service_call = f"this.{self._camel_case(feature_name)}Service.replace(input)"
            method_signature = f"replace{class_name}(@Body() input: {dto_class_name})"
            body_argument = f'import {{ {dto_class_name} }} from "./dto/create-{self._slugify(feature_name)}.dto";\n'
        elif http_method == "PATCH":
            decorators.append("Patch")
            imports.extend(["Body", "Patch"])
            method_name = "update"
            service_call = f"this.{self._camel_case(feature_name)}Service.update(input)"
            method_signature = f"update{class_name}(@Body() input: {dto_class_name})"
            body_argument = f'import {{ {dto_class_name} }} from "./dto/create-{self._slugify(feature_name)}.dto";\n'
        elif http_method == "DELETE":
            decorators.append("Delete")
            imports.append("Delete")
            method_name = "remove"
            service_call = f"this.{self._camel_case(feature_name)}Service.remove()"
            method_signature = f"remove{class_name}()"
        else:
            decorators.append("Get")
            imports.append("Get")

        imports = sorted(set(imports))
        http_decorator = decorators[-1]
        dto_import = body_argument if has_dto and http_method in {"POST", "PUT", "PATCH"} else ""
        return (
            f'import {{ {", ".join(imports)} }} from "@nestjs/common";\n'
            f'import {{ {class_name}Service }} from "./{self._slugify(feature_name)}.service";\n'
            f"{dto_import}"
            "\n"
            f'@Controller("{route_path}")\n'
            f"export class {class_name}Controller {{\n"
            f"  constructor(private readonly {self._camel_case(feature_name)}Service: {class_name}Service) {{}}\n\n"
            f"  @{http_decorator}()\n"
            f"  {method_signature} {{\n"
            f"    return {service_call};\n"
            "  }\n"
            "}\n"
        )

    def _nest_service_template(self, feature_name: str, dto_class_name: str, has_dto: bool, http_method: str) -> str:
        class_name = self._to_component_name(feature_name)
        dto_import = f'import {{ {dto_class_name} }} from "./dto/create-{self._slugify(feature_name)}.dto";\n' if has_dto else ""

        methods = [
            "  list() {",
            f'    return [{{ id: "{self._slugify(feature_name)}-1", name: "Sample {self._humanize_identifier(feature_name)}" }}];',
            "  }",
        ]
        if http_method in {"POST", "PUT", "PATCH"}:
            service_method = "create" if http_method == "POST" else "replace" if http_method == "PUT" else "update"
            methods.extend(
                [
                    "",
                    f"  {service_method}(input: {dto_class_name}) {{",
                    f'    return {{ id: "{self._slugify(feature_name)}-1", ...input }};',
                    "  }",
                ]
            )
        if http_method == "DELETE":
            methods.extend(
                [
                    "",
                    "  remove() {",
                    '    return { ok: true };',
                    "  }",
                ]
            )

        return (
            'import { Injectable } from "@nestjs/common";\n'
            f"{dto_import}"
            "\n"
            "@Injectable()\n"
            f"export class {class_name}Service {{\n"
            + "\n".join(methods)
            + "\n}\n"
        )

    def _nest_dto_template(self, dto_class_name: str, issue: str) -> str:
        field_name = "name"
        if re.search(r"\b(email)\b", issue.lower()):
            field_name = "email"
        elif re.search(r"\b(title)\b", issue.lower()):
            field_name = "title"
        elif re.search(r"\b(status)\b", issue.lower()):
            field_name = "status"
        return (
            f"export class {dto_class_name} {{\n"
            f"  {field_name}!: string;\n"
            "}\n"
        )

    def _build_nest_app_module_operation(
        self,
        editor: FileEditor,
        nestjs_profile: dict[str, Any],
        feature_name: str,
        module_file: str,
    ) -> dict[str, Any] | None:
        app_module_file = nestjs_profile.get("app_module_file")
        if not app_module_file or not editor.exists(app_module_file):
            return None

        current_content = editor.view_file(app_module_file)
        module_class_name = f"{self._to_component_name(feature_name)}Module"
        if module_class_name in current_content:
            return None

        import_path = self._relative_import(app_module_file, module_file)
        import_statement = f'import {{ {module_class_name} }} from "{import_path}";'
        updated_content = current_content
        if import_statement not in updated_content:
            updated_content = f"{import_statement}\n{updated_content}"

        imports_match = re.search(r"imports:\s*\[(.*?)\]", updated_content, re.S)
        if imports_match is not None:
            existing_imports = imports_match.group(1).strip()
            replacement = f"imports: [{existing_imports}, {module_class_name}]" if existing_imports else f"imports: [{module_class_name}]"
            updated_content = (
                updated_content[:imports_match.start()]
                + replacement
                + updated_content[imports_match.end():]
            )
        else:
            module_match = re.search(r"@Module\(\{", updated_content)
            if module_match is None:
                return None
            insert_index = module_match.end()
            updated_content = f"{updated_content[:insert_index]}\n  imports: [{module_class_name}],{updated_content[insert_index:]}"

        return {
            "type": "write_file",
            "file_path": app_module_file,
            "content": updated_content,
        }

    def _relative_import(self, from_file: str, to_file: str) -> str:
        from posixpath import relpath
        base_dir = PurePosixPath(from_file).parent
        target = PurePosixPath(to_file).with_suffix("")
        computed_rel = relpath(target.as_posix(), start=base_dir.as_posix())
        normalized = computed_rel
        if not normalized.startswith("."):
            normalized = f"./{normalized}"
        return normalized.replace("\\", "/")

    def _to_component_name(self, value: str) -> str:
        tokens = re.findall(r"[A-Za-z0-9]+", value)
        return "".join(token.capitalize() for token in tokens) or "FeatureComponent"

    def _camel_case(self, value: str) -> str:
        component_name = self._to_component_name(value)
        return component_name[:1].lower() + component_name[1:] if component_name else "feature"

    def _slugify(self, value: str) -> str:
        tokens = re.findall(r"[A-Za-z0-9]+", value.lower())
        return "-".join(tokens) or "feature"

    def _humanize_identifier(self, value: str) -> str:
        return " ".join(token.capitalize() for token in re.findall(r"[A-Za-z0-9]+", value)) or "Feature"
