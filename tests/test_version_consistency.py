from __future__ import annotations

import re
import unittest
from pathlib import Path

from ai_code_agent import __version__


class VersionConsistencyTest(unittest.TestCase):
    def test_package_version_matches_pyproject(self) -> None:
        pyproject_text = Path("pyproject.toml").read_text(encoding="utf-8")
        match = re.search(r'^version\s*=\s*"([^"]+)"', pyproject_text, re.M)
        self.assertIsNotNone(match)
        self.assertEqual(__version__, match.group(1))


if __name__ == "__main__":
    unittest.main()