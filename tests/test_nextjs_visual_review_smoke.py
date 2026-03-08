from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from artifact import run_nextjs_visual_review_smoke


class NextjsVisualReviewSmokeTest(unittest.TestCase):
    def test_playwright_install_command_uses_with_deps_on_linux(self) -> None:
        with patch("artifact.run_nextjs_visual_review_smoke.sys.platform", "linux"), patch(
            "artifact.run_nextjs_visual_review_smoke.os.name", "posix"
        ):
            command = run_nextjs_visual_review_smoke._playwright_install_command()

        self.assertEqual(command, ["npx", "playwright", "install", "--with-deps", "chromium"])

    def test_artifact_paths_from_manifest_only_keeps_existing_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace_dir = Path(temp_dir)
            artifact_root = workspace_dir / run_nextjs_visual_review_smoke.ARTIFACT_ROOT
            (artifact_root / "screenshots").mkdir(parents=True)
            (artifact_root / "screenshots/home.png").write_bytes(b"png-bytes")

            manifest = {
                "artifacts": [
                    {"path": "screenshots/home.png"},
                    {"path": "screenshots/missing.png"},
                    {"path": ""},
                    {},
                ]
            }

            paths = run_nextjs_visual_review_smoke._artifact_paths_from_manifest(workspace_dir, manifest)

            self.assertEqual(paths, [".ai-code-agent/visual-review/screenshots/home.png"])

    def test_collect_result_requires_manifest_and_screenshots(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace_dir = Path(temp_dir)
            artifact_root = workspace_dir / run_nextjs_visual_review_smoke.ARTIFACT_ROOT
            screenshots_dir = artifact_root / "screenshots"
            screenshots_dir.mkdir(parents=True)
            (screenshots_dir / "home.png").write_bytes(b"png-bytes")
            (screenshots_dir / "home-mobile.png").write_bytes(b"png-bytes")
            (artifact_root / "manifest.json").write_text(
                json.dumps(
                    {
                        "artifacts": [
                            {"path": "screenshots/home.png", "viewport": {"width": 1440, "height": 960}},
                            {"path": "screenshots/home-mobile.png", "viewport": {"width": 393, "height": 852}},
                        ]
                    }
                ),
                encoding="utf-8",
            )

            result = run_nextjs_visual_review_smoke._collect_result(workspace_dir)

            self.assertTrue(result["passed"])
            self.assertEqual(result["artifact_count"], 2)
            self.assertEqual(result["viewport_categories"], ["desktop", "mobile"])
            self.assertEqual(
                result["screenshot_files"],
                [
                    ".ai-code-agent/visual-review/screenshots/home-mobile.png",
                    ".ai-code-agent/visual-review/screenshots/home.png",
                ],
            )

    def test_viewport_categories_require_mobile_and_desktop(self) -> None:
        manifest = {
            "artifacts": [
                {"path": "screenshots/home.png", "viewport": {"width": 1440, "height": 960}},
                {"path": "screenshots/home-mobile.png", "viewport": {"width": 393, "height": 852}},
            ]
        }

        categories = run_nextjs_visual_review_smoke._viewport_categories_from_manifest(manifest)

        self.assertEqual(categories, {"desktop", "mobile"})


if __name__ == "__main__":
    unittest.main()