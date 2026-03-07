import difflib
import json
from pathlib import Path
import re
from typing import Any

from ai_code_agent.agents.base import BaseAgent
from ai_code_agent.orchestrator import AgentState
from ai_code_agent.llm.prompts import CODER_SYSTEM_PROMPT
from ai_code_agent.tools.file_editor import FileEditor

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
        patches: list[dict] = []
        failures: list[str] = []

        for operation in response.get("operations", []):
            patch = self._apply_operation(editor, state, operation)
            if patch is not None:
                patches.append(patch)
            else:
                failures.append(self._describe_failed_operation(operation))

        return {
            "patches": patches,
            "error_message": None if not failures else "; ".join(failures),
            "codegen_summary": {
                "requested_operations": len(response.get("operations", [])),
                "applied_operations": len(patches),
                "failed_operations": failures,
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
