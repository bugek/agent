from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ai_code_agent.agents.coder import CoderAgent
from ai_code_agent.config import AgentConfig


class StubCoderLLM:
    def generate_json(self, system_prompt: str, user_prompt: str, schema: dict | None = None) -> dict:
        return {
            "operations": [
                {"type": "write_file", "file_path": "artifact/fixtures/demo.txt", "content": "blocked\n"},
                {"type": "write_file", "file_path": "docs/allowed.txt", "content": "allowed\n"},
            ]
        }


class CoderFilePolicyTest(unittest.TestCase):
    def test_run_blocks_operations_outside_policy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "artifact/fixtures").mkdir(parents=True)
            (workspace / "docs").mkdir(parents=True)
            (workspace / "artifact/fixtures/demo.txt").write_text("fixture\n", encoding="utf-8")
            (workspace / "docs/allowed.txt").write_text("before\n", encoding="utf-8")

            config = AgentConfig(workspace_dir=temp_dir)
            config.edit_allow_globs = ["docs/**"]
            config.edit_deny_globs = ["artifact/fixtures/**"]
            coder = CoderAgent(config, StubCoderLLM())

            result = coder.run(
                {
                    "issue_description": "update documentation content",
                    "workspace_dir": temp_dir,
                    "files_to_edit": ["artifact/fixtures/demo.txt", "docs/allowed.txt"],
                    "plan": "Update docs only.",
                    "planning_context": {},
                }
            )

            self.assertEqual(len(result["patches"]), 1)
            self.assertEqual(result["patches"][0]["file"], "docs/allowed.txt")
            self.assertEqual(
                result["codegen_summary"]["blocked_operations"],
                [{"file_path": "artifact/fixtures/demo.txt", "reason": "matched deny rule: artifact/fixtures/**"}],
            )
            self.assertIn("file edit policy blocked operations", result["error_message"])
            self.assertEqual((workspace / "docs/allowed.txt").read_text(encoding="utf-8"), "allowed\n")
            self.assertEqual((workspace / "artifact/fixtures/demo.txt").read_text(encoding="utf-8"), "fixture\n")


if __name__ == "__main__":
    unittest.main()