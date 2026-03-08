from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_code_agent.tools.version_resolution import is_dependency_upgrade_request, resolve_workspace_version_context


class VersionResolutionTest(unittest.TestCase):
    def test_detects_dependency_upgrade_requests(self) -> None:
        self.assertTrue(is_dependency_upgrade_request("upgrade Next.js and display app version from package.json"))
        self.assertFalse(is_dependency_upgrade_request("revamp dashboard hero section"))

    def test_prefers_project_baseline_for_supported_upgrade_requests(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "package.json").write_text(
                '{\n  "name": "demo",\n  "version": "0.1.0",\n  "dependencies": {\n    "next": "14.2.16"\n  }\n}\n',
                encoding="utf-8",
            )
            profile = {"frameworks": ["nextjs"]}
            with patch("ai_code_agent.tools.version_resolution._fixture_baseline_version", return_value="16.1.6"), patch(
                "ai_code_agent.tools.version_resolution._npm_dist_tags",
                return_value={"latest": "16.1.6", "next-14": "14.2.35"},
            ), patch(
                "ai_code_agent.tools.version_resolution._runtime_node_version",
                return_value="22.17.0",
            ), patch(
                "ai_code_agent.tools.version_resolution._package_node_engine",
                return_value=">=20.9.0",
            ):
                result = resolve_workspace_version_context(
                    temp_dir,
                    "upgrade the app from the current Next.js version to the current project baseline or a compatible newer supported version",
                    profile,
                )

        self.assertIsNotNone(result)
        self.assertEqual(result["current_version"], "14.2.16")
        self.assertEqual(result["selected_version"], "16.1.6")
        self.assertEqual(result["selection_reason"], "prefer_project_baseline")

    def test_falls_back_to_runtime_compatible_version_when_baseline_needs_newer_node(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "package.json").write_text(
                '{\n  "name": "demo",\n  "version": "0.1.0",\n  "dependencies": {\n    "next": "14.2.16"\n  }\n}\n',
                encoding="utf-8",
            )
            profile = {"frameworks": ["nextjs"]}
            with patch("ai_code_agent.tools.version_resolution._fixture_baseline_version", return_value="16.1.6"), patch(
                "ai_code_agent.tools.version_resolution._npm_dist_tags",
                return_value={"latest": "16.1.6", "backport": "15.5.12", "next-14": "14.2.35"},
            ), patch(
                "ai_code_agent.tools.version_resolution._runtime_node_version",
                return_value="18.20.4",
            ), patch(
                "ai_code_agent.tools.version_resolution._package_node_engine",
                side_effect=lambda package_name, version: {"16.1.6": ">=20.9.0", "15.5.12": "^18.18.0 || ^19.8.0 || >= 20.0.0", "14.2.35": ">=18.17.0"}.get(version),
            ):
                result = resolve_workspace_version_context(
                    temp_dir,
                    "upgrade the app from the current Next.js version to the current project baseline or a compatible newer supported version",
                    profile,
                )

        self.assertIsNotNone(result)
        self.assertEqual(result["selected_version"], "15.5.12")
        self.assertEqual(result["selection_reason"], "fallback_runtime_compatible_from_prefer_project_baseline")


if __name__ == "__main__":
    unittest.main()