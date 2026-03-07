import difflib
import json
from pathlib import PurePosixPath
from pathlib import Path
import re
from typing import Any

from ai_code_agent.agents.base import BaseAgent
from ai_code_agent.orchestrator import AgentState
from ai_code_agent.llm.prompts import CODER_SYSTEM_PROMPT
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
        nextjs_operations = self._build_nextjs_operations(state, workspace_profile, editor)
        if nextjs_operations:
            return self._apply_operations(editor, state, nextjs_operations, generated_by="nextjs_scaffold")

        candidate_files = [
            file_path for file_path in state.get("files_to_edit", []) if self._exists(state, file_path)
        ][:5]
        file_context = []
        for file_path in candidate_files:
            excerpt = editor.view_file(file_path)
            file_context.append({"file_path": file_path, "content": excerpt[:4000]})

        prompt_payload = {
            "issue": state["issue_description"],
            "plan": state.get("plan"),
            "workspace_profile": workspace_profile,
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
        return self._apply_operations(editor, state, response.get("operations", []), generated_by="llm")

    def _apply_operations(
        self,
        editor: FileEditor,
        state: AgentState,
        operations: list[dict[str, Any]],
        generated_by: str,
    ) -> dict:
        patches: list[dict] = []
        failures: list[str] = []

        for operation in operations:
            patch = self._apply_operation(editor, state, operation)
            if patch is not None:
                patches.append(patch)
            else:
                failures.append(self._describe_failed_operation(operation))

        return {
            "patches": patches,
            "error_message": None if not failures else "; ".join(failures),
            "codegen_summary": {
                "requested_operations": len(operations),
                "applied_operations": len(patches),
                "failed_operations": failures,
                "generated_by": generated_by,
            },
        }

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
                    self._next_component_template(component_request or "Feature Section"),
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
                        self._next_page_template(page_file, route_slug, component_file, component_request),
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
                        self._next_layout_template(route_slug),
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

    def _next_page_template(
        self,
        page_file: str,
        route_slug: str,
        component_file: str | None,
        component_request: str | None,
    ) -> str:
        page_name = self._humanize_identifier(route_slug or "home")
        component_name = self._to_component_name(component_request or f"{page_name} section")
        imports = []
        body_lines = [
            "    <main>",
            f"      <h1>{page_name}</h1>",
            f"      <p>{page_name} experience scaffolded by AI Code Agent.</p>",
        ]
        if component_file is not None:
            import_path = self._relative_import(page_file, component_file)
            imports.append(f'import {{ {component_name} }} from "{import_path}";')
            body_lines.append(f"      <{component_name} />")
        body_lines.append("    </main>")

        import_block = "\n".join(imports)
        if import_block:
            import_block += "\n\n"
        return (
            f"{import_block}export default function {self._to_component_name(page_name)}Page() {{\n"
            "  return (\n"
            + "\n".join(body_lines)
            + "\n  );\n"
            "}\n"
        )

    def _next_layout_template(self, route_slug: str) -> str:
        section_name = self._humanize_identifier(route_slug or "app")
        return (
            'import type { ReactNode } from "react";\n\n'
            "type LayoutProps = {\n"
            "  children: ReactNode;\n"
            "};\n\n"
            f"export default function {self._to_component_name(section_name)}Layout({{ children }}: LayoutProps) {{\n"
            "  return (\n"
            "    <section>\n"
            f"      <header><h1>{section_name}</h1></header>\n"
            "      <div>{children}</div>\n"
            "    </section>\n"
            "  );\n"
            "}\n"
        )

    def _next_component_template(self, component_request: str) -> str:
        component_name = self._to_component_name(component_request)
        title = self._humanize_identifier(component_request)
        return (
            f"export function {component_name}() {{\n"
            "  return (\n"
            "    <section>\n"
            f"      <h2>{title}</h2>\n"
            f"      <p>{title} content scaffolded by AI Code Agent.</p>\n"
            "    </section>\n"
            "  );\n"
            "}\n"
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

    def _slugify(self, value: str) -> str:
        tokens = re.findall(r"[A-Za-z0-9]+", value.lower())
        return "-".join(tokens) or "feature"

    def _humanize_identifier(self, value: str) -> str:
        return " ".join(token.capitalize() for token in re.findall(r"[A-Za-z0-9]+", value)) or "Feature"
