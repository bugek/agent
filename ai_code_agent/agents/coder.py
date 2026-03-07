import difflib
import json
from pathlib import Path

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
                        "file_path": "relative/path.py",
                        "search": "exact text to replace",
                        "replace": "new text",
                    }
                ]
            },
        }
        response = self.llm.generate_json(CODER_SYSTEM_PROMPT, json.dumps(prompt_payload, indent=2))
        patches: list[dict] = []

        for operation in response.get("operations", []):
            patch = self._apply_operation(editor, state, operation)
            if patch is not None:
                patches.append(patch)

        return {
            "patches": patches,
            "error_message": None if patches else state.get("error_message"),
        }

    def _exists(self, state: AgentState, file_path: str) -> bool:
        path = Path(state["workspace_dir"]) / file_path
        return path.exists()

    def _apply_operation(self, editor: FileEditor, state: AgentState, operation: dict) -> dict | None:
        file_path = operation.get("file_path")
        search = operation.get("search")
        replace = operation.get("replace")
        if not file_path or search is None or replace is None:
            return None

        absolute_path = Path(state["workspace_dir"]) / file_path
        if not absolute_path.exists():
            return None

        before = editor.view_file(file_path)
        if search not in before:
            return None

        if not editor.replace_text(file_path, search, replace):
            return None

        after = editor.view_file(file_path)
        diff = "\n".join(
            difflib.unified_diff(
                before.splitlines(),
                after.splitlines(),
                fromfile=file_path,
                tofile=file_path,
                lineterm="",
            )
        )
        return {"file": file_path, "diff": diff}
