from __future__ import annotations

import re
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class RuntimePolicyTest(unittest.TestCase):
    def test_ci_node_version_matches_runtime_matrix_recommendation(self) -> None:
        workflow = (REPO_ROOT / ".github/workflows/validation.yml").read_text(encoding="utf-8")
        matrix = (REPO_ROOT / "artifact/runtime_matrix.md").read_text(encoding="utf-8")

        ci_node_match = re.search(r"node-version:\s*'([^']+)'", workflow)
        self.assertIsNotNone(ci_node_match)
        ci_node_version = ci_node_match.group(1)

        self.assertIn("| CI validation workflow | Node.js | 22.x |", matrix)
        self.assertIn("use Node 22.", matrix)
        self.assertEqual(ci_node_version, "22")

    def test_fixture_engine_floors_match_runtime_matrix(self) -> None:
        next_package = (REPO_ROOT / "artifact/fixtures/nextjs-visual-review/package.json").read_text(encoding="utf-8")
        nest_package = (REPO_ROOT / "artifact/fixtures/nestjs-smoke/package.json").read_text(encoding="utf-8")
        matrix = (REPO_ROOT / "artifact/runtime_matrix.md").read_text(encoding="utf-8")

        self.assertIn('"node": ">=20.9.0"', next_package)
        self.assertIn('"node": ">=20"', nest_package)
        self.assertIn("| Next.js visual-review fixture | Node.js | >=20.9.0 |", matrix)
        self.assertIn("| NestJS smoke fixture | Node.js | >=20 |", matrix)

    def test_python_baseline_matches_runtime_matrix(self) -> None:
        pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        matrix = (REPO_ROOT / "artifact/runtime_matrix.md").read_text(encoding="utf-8")

        self.assertIn('python = "^3.11"', pyproject)
        self.assertIn("| Main repo CLI and validation entrypoints | Python | 3.11 |", matrix)

    def test_next_fixture_playwright_pin_matches_runtime_matrix(self) -> None:
        next_package = (REPO_ROOT / "artifact/fixtures/nextjs-visual-review/package.json").read_text(encoding="utf-8")
        matrix = (REPO_ROOT / "artifact/runtime_matrix.md").read_text(encoding="utf-8")

        self.assertIn('"@playwright/test": "1.58.2"', next_package)
        self.assertIn("@playwright/test 1.58.2", matrix)


if __name__ == "__main__":
    unittest.main()