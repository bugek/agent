from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ai_code_agent.skills import SkillManifestError, discover_local_skills, partition_skills_by_permission, select_skills


class SkillsTest(unittest.TestCase):
    def test_discover_local_skills_reads_skill_markdown_frontmatter(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            skill_dir = Path(temp_dir) / "skills" / "frontend-visual-review"
            skill_dir.mkdir(parents=True, exist_ok=True)
            (skill_dir / "SKILL.md").write_text(
                """---
name: frontend-visual-review
version: 0.1.0
title: Frontend Visual Review
description: Keep screenshot-backed UI checks visible in planning.
tags: frontend, ui, screenshot
triggers: screenshot, visual review, loading state
frameworks: nextjs, react
permission: read-only
sandbox: optional
input_schema: {"type": "object", "properties": {"issue": {"type": "string"}}, "required": ["issue"]}
output_schema: {"type": "object", "properties": {"plan_notes": {"type": "array"}}, "required": ["plan_notes"]}
---

Use this skill for UI work.
""",
                encoding="utf-8",
            )

            skills = discover_local_skills(temp_dir, ["skills"])

        self.assertEqual(len(skills), 1)
        self.assertEqual(skills[0].name, "frontend-visual-review")
        self.assertEqual(skills[0].version, "0.1.0")
        self.assertEqual(skills[0].title, "Frontend Visual Review")
        self.assertEqual(skills[0].tags, ["frontend", "ui", "screenshot"])
        self.assertEqual(skills[0].frameworks, ["nextjs", "react"])
        self.assertEqual(skills[0].path, "skills/frontend-visual-review/SKILL.md")
        self.assertEqual(skills[0].input_schema["type"], "object")
        self.assertEqual(skills[0].output_schema["type"], "object")
        self.assertIn("Use this skill", skills[0].instructions)

    def test_select_skills_scores_issue_and_workspace_matches(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            visual_skill_dir = Path(temp_dir) / "skills" / "frontend-visual-review"
            visual_skill_dir.mkdir(parents=True, exist_ok=True)
            (visual_skill_dir / "SKILL.md").write_text(
                """---
name: frontend-visual-review
version: 0.1.0
title: Frontend Visual Review
description: Keep screenshot-backed UI checks visible in planning.
tags: frontend, ui, screenshot
triggers: screenshot, visual review, loading state
frameworks: nextjs, react
permission: read-only
sandbox: optional
input_schema: {"type": "object", "properties": {"issue": {"type": "string"}}, "required": ["issue"]}
output_schema: {"type": "object", "properties": {"plan_notes": {"type": "array"}}, "required": ["plan_notes"]}
---

Use this skill for UI work.
""",
                encoding="utf-8",
            )
            release_skill_dir = Path(temp_dir) / "skills" / "release-readiness"
            release_skill_dir.mkdir(parents=True, exist_ok=True)
            (release_skill_dir / "SKILL.md").write_text(
                """---
name: release-readiness
version: 0.1.0
title: Release Readiness
description: Keep validation and release criteria visible.
tags: release, validation, checklist
triggers: 1.0 checklist, release readiness
frameworks: python
permission: read-only
sandbox: optional
input_schema: {"type": "object", "properties": {"issue": {"type": "string"}}, "required": ["issue"]}
output_schema: {"type": "object", "properties": {"release_checks": {"type": "array"}}, "required": ["release_checks"]}
---

Use this skill for release work.
""",
                encoding="utf-8",
            )

            skills = discover_local_skills(temp_dir, ["skills"])
            selected = select_skills(
                skills,
                "review screenshot coverage and loading state for the dashboard page",
                {"frameworks": ["nextjs"]},
            )

        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["name"], "frontend-visual-review")
        self.assertGreater(selected[0]["score"], 0)
        self.assertTrue(selected[0]["reasons"])
        self.assertEqual(selected[0]["version"], "0.1.0")
        self.assertEqual(selected[0]["input_schema"]["type"], "object")
        self.assertEqual(selected[0]["output_schema"]["type"], "object")
        self.assertIn("instructions", selected[0])

    def test_discover_local_skills_fails_with_clear_manifest_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            skill_dir = Path(temp_dir) / "skills" / "broken-skill"
            skill_dir.mkdir(parents=True, exist_ok=True)
            (skill_dir / "SKILL.md").write_text(
                """---
name: broken-skill
permission: root
input_schema: []
---

""",
                encoding="utf-8",
            )

            with self.assertRaises(SkillManifestError) as captured:
                discover_local_skills(temp_dir, ["skills"])

        message = str(captured.exception)
        self.assertIn("skills/broken-skill/SKILL.md", message)
        self.assertIn("missing required field 'version'", message)
        self.assertIn("missing required field 'description'", message)
        self.assertIn("field 'permission' must be one of", message)
        self.assertIn("missing required field 'input_schema' as a JSON object", message)
        self.assertIn("missing required field 'output_schema' as a JSON object", message)

    def test_partition_skills_by_permission_blocks_disallowed_entries(self) -> None:
        permitted, blocked = partition_skills_by_permission(
            [
                {"name": "frontend-visual-review", "permission": "read-only"},
                {"name": "compose-stack", "permission": "sandbox"},
            ],
            ["read-only"],
        )

        self.assertEqual([item["name"] for item in permitted], ["frontend-visual-review"])
        self.assertEqual([item["name"] for item in blocked], ["compose-stack"])
        self.assertEqual(blocked[0]["blocked_reason"], "permission_not_allowed:sandbox")


if __name__ == "__main__":
    unittest.main()