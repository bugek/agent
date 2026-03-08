import difflib
import json
from pathlib import PurePosixPath
from pathlib import Path
import re
from typing import Any

from ai_code_agent.agents.base import BaseAgent
from ai_code_agent.orchestrator import AgentState
from ai_code_agent.llm.prompts import CODER_SYSTEM_PROMPT
from ai_code_agent.tools.edit_policy import evaluate_edit_path, filter_edit_paths, summarize_edit_policy
from ai_code_agent.tools.file_editor import FileEditor
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
        if remediation_context is None:
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
        file_context = []
        for file_path in candidate_files:
            excerpt = editor.view_file(file_path)
            file_context.append({"file_path": file_path, "content": excerpt[:4000]})

        prompt_payload = {
            "issue": state["issue_description"],
            "plan": state.get("plan"),
            "edit_intent": self._edit_intent(state),
            "workspace_profile": workspace_profile,
            "design_brief": self._frontend_design_brief(state),
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
        result = self._apply_operations(
            editor,
            state,
            response.get("operations", []),
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
        return deduplicated[:5]

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
            "testing_summary": state.get("testing_summary") if isinstance(state.get("testing_summary"), dict) else {},
        }
        if not any(
            context[key]
            for key in ["failed_validation_labels", "blocked_file_paths", "failed_operations", "focus_areas", "guidance"]
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

    def _exists(self, state: AgentState, file_path: str) -> bool:
        path = Path(state["workspace_dir"]) / file_path
        return path.exists()

    def _apply_operation(self, editor: FileEditor, state: AgentState, operation: dict[str, Any]) -> dict | None:
        operation_type = operation.get("type", "replace_text")
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
            if content is None or absolute_path.exists():
                return None
            if not editor.create_file(file_path, content):
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
            "operation": operation_type,
            "diff": diff,
        }

    def _describe_failed_operation(self, operation: dict[str, Any]) -> str:
        operation_type = operation.get("type", "replace_text")
        file_path = operation.get("file_path", "<missing file>")
        return f"{operation_type} failed for {file_path}"

    def _is_analysis_only(self, issue: str) -> bool:
        return bool(re.search(r"\b(analyze|inspect|summari[sz]e|review|readiness)\b", issue, re.I))

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
        if not re.search(r"\b(next|page|layout|component|api|route|handler|hero|card|form|modal|section|dashboard|screen|view)\b", lower_issue):
            return []

        route_slug = self._extract_route_slug(issue)
        action = "write_file" if re.search(r"\b(update|modify|refactor|revamp|rewrite|redesign)\b", lower_issue) else "create_file"
        operations: list[dict[str, Any]] = []

        component_request = self._extract_component_request(issue, route_slug)
        component_file = self._resolve_component_file(nextjs_profile, component_request)

        wants_page = bool(re.search(r"\b(page|screen|view)\b", lower_issue))
        wants_layout = bool(re.search(r"\b(layout|shell|wrapper)\b", lower_issue))
        wants_api_route = bool(re.search(r"\b(api|route handler|endpoint|handler)\b", lower_issue))
        wants_component = component_request is not None or bool(re.search(r"\b(component|card|hero|section|form|modal|panel|table|list)\b", lower_issue))

        if wants_component and component_file is not None:
            operations.append(
                self._file_operation(
                    editor,
                    component_file,
                    self._next_component_template(component_request or "Feature Section", issue, route_slug, design_brief),
                    preferred_action=action,
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
        explicit_match = re.search(r"/([a-z0-9\-/]+)", issue.lower())
        if explicit_match:
            return explicit_match.group(1).strip("/")

        phrase_match = re.search(r"\b([a-z0-9-]+)\s+(?:page|screen|view|route|layout|api)\b", issue.lower())
        if phrase_match:
            return phrase_match.group(1)

        preferred_terms = [
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
        lower_issue = issue.lower()
        for term in preferred_terms:
            if term in lower_issue:
                return term

        tokens = [token for token in re.findall(r"[a-z0-9-]+", lower_issue) if token not in {"add", "create", "update", "new", "next", "component", "page", "layout", "api", "route", "handler"}]
        return tokens[0] if tokens else "feature"

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
            "      </section>",
        ]
        if component_file is not None:
            import_path = self._relative_import(page_file, component_file)
            imports.append(f'import {{ {component_name} }} from "{import_path}";')
            body_lines.append(f"      <{component_name} state=\"ready\" items={{previewItems}} />")
        body_lines.append("    </main>")

        import_block = "\n".join(imports)
        if import_block:
            import_block += "\n\n"
        return (
            f"{import_block}const previewItems = [\n"
            f"  {{ label: \"Primary signal\", value: \"{design['primary_metric']}\", detail: \"{design['metric_caption']}\" }},\n"
            f"  {{ label: \"Momentum\", value: \"{design['secondary_metric']}\", detail: \"{design['secondary_caption']}\" }},\n"
            "];\n\n"
            "const pageStyles = {\n"
            f"  shell: {{ minHeight: \"100vh\", padding: \"4rem 1.5rem\", background: \"{design['background']}\", color: \"{design['text']}\" }},\n"
            f"  hero: {{ maxWidth: \"56rem\", margin: \"0 auto 2rem\", padding: \"2rem\", borderRadius: \"28px\", background: \"{design['panel']}\", boxShadow: \"0 24px 60px rgba(15, 23, 42, 0.12)\" }},\n"
            f"  eyebrow: {{ margin: 0, textTransform: \"uppercase\", letterSpacing: \"0.18em\", fontSize: \"0.72rem\", color: \"{design['accent']}\" }},\n"
            "  title: { margin: \"0.75rem 0 0\", fontSize: \"clamp(2.5rem, 6vw, 4.75rem)\", lineHeight: 0.95 },\n"
            f"  description: {{ maxWidth: \"42rem\", margin: \"1rem 0 0\", fontSize: \"1.05rem\", color: \"{design['muted']}\" }},\n"
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

    def _next_component_template(self, component_request: str, issue: str, route_slug: str, design_brief: dict[str, Any] | None = None) -> str:
        component_name = self._to_component_name(component_request)
        title = self._humanize_identifier(component_request)
        design = self._next_design_direction(issue, route_slug or component_request, design_brief)
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
            "export default function ErrorBoundary({ error, reset }: ErrorProps) {\n"
            "  return (\n"
            f"    <div style={{{{ padding: \"2rem\", borderRadius: \"24px\", background: \"{design['panel']}\", color: \"{design['text']}\" }}}}>\n"
            f"      <h2>{design['error_title']}</h2>\n"
            f"      <p style={{{{ color: \"{design['muted']}\" }}}}>{design['error_copy']}</p>\n"
            '      <p style={{ color: "#b91c1c" }}>{error.message}</p>\n'
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
                "primary_metric": "$128k",
                "secondary_metric": "+18%",
                "metric_caption": "Net uplift since the last release window.",
                "secondary_caption": "Week-on-week momentum across the main surface.",
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
                "primary_metric": "24 live",
                "secondary_metric": "4 queues",
                "metric_caption": "Primary content is surfaced above the fold.",
                "secondary_caption": "Supporting actions stay visible without crowding the layout.",
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
