from __future__ import annotations

import unittest

from ai_code_agent.tools.edit_policy import evaluate_edit_path, filter_edit_paths, summarize_edit_policy


class EditPolicyTest(unittest.TestCase):
    def test_deny_rule_blocks_matching_path(self) -> None:
        allowed, reason = evaluate_edit_path("artifact/fixtures/demo.txt", [], ["artifact/fixtures/**"])

        self.assertFalse(allowed)
        self.assertEqual(reason, "matched deny rule: artifact/fixtures/**")

    def test_allowlist_blocks_path_outside_scope(self) -> None:
        allowed, reason = evaluate_edit_path("docs/readme.md", ["src/**"], [])

        self.assertFalse(allowed)
        self.assertEqual(reason, "outside allowed edit paths")

    def test_filter_edit_paths_deduplicates_and_summarizes_policy(self) -> None:
        allowed, blocked = filter_edit_paths(
            ["src/app.ts", "src\\app.ts", "artifact/fixtures/demo.txt"],
            ["src/**"],
            ["artifact/fixtures/**"],
        )
        summary = summarize_edit_policy(["src/**"], ["artifact/fixtures/**"])

        self.assertEqual(allowed, ["src/app.ts"])
        self.assertEqual(blocked, [{"file_path": "artifact/fixtures/demo.txt", "reason": "matched deny rule: artifact/fixtures/**"}])
        self.assertEqual(summary["allow_globs"], ["src/**"])
        self.assertEqual(summary["deny_globs"], ["artifact/fixtures/**"])


if __name__ == "__main__":
    unittest.main()