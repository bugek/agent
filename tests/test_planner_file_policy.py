from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ai_code_agent.agents.planner import PlannerAgent
from ai_code_agent.config import AgentConfig


class StubPlannerLLM:
    def generate_json(self, system_prompt: str, user_prompt: str, schema: dict | None = None) -> dict:
        return {
            "plan": "Update the allowed source file only.",
            "files_to_edit": ["artifact/fixtures/demo.txt", "ai_code_agent/sample.py"],
        }


class PlannerFilePolicyTest(unittest.TestCase):
    def test_run_filters_blocked_files_to_edit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "ai_code_agent").mkdir()
            (workspace / "artifact/fixtures").mkdir(parents=True)
            (workspace / "ai_code_agent/sample.py").write_text("print('ok')\n", encoding="utf-8")
            (workspace / "artifact/fixtures/demo.txt").write_text("fixture\n", encoding="utf-8")

            config = AgentConfig(workspace_dir=temp_dir)
            config.edit_deny_globs = ["artifact/fixtures/**"]
            planner = PlannerAgent(config, StubPlannerLLM())

            result = planner.run({"issue_description": "update sample implementation", "workspace_dir": temp_dir})

        self.assertEqual(result["files_to_edit"], ["ai_code_agent/sample.py"])
        self.assertEqual(result["file_edit_policy"]["deny_globs"], ["artifact/fixtures/**"])
        self.assertEqual(
            result["planning_context"]["blocked_files_to_edit"],
            [{"file_path": "artifact/fixtures/demo.txt", "reason": "matched deny rule: artifact/fixtures/**"}],
        )


if __name__ == "__main__":
    unittest.main()