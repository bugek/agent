from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ai_code_agent.orchestrator import AgentState, build_graph


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "nestjs-smoke"
DEFAULT_ISSUE = "add a NestJS users module with controller service dto and GET /users endpoint"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the committed NestJS smoke fixture through the AI Code Agent workflow.")
    parser.add_argument("--issue", default=DEFAULT_ISSUE, help="Issue prompt to run against the NestJS fixture.")
    parser.add_argument("--keep-workspace", action="store_true", help="Keep the temporary workspace for inspection.")
    parser.add_argument("--json", action="store_true", help="Print the final workflow state as JSON.")
    return parser.parse_args()


def build_initial_state(issue: str, workspace_dir: str) -> AgentState:
    return {
        "issue_description": issue,
        "workspace_dir": workspace_dir,
        "plan": None,
        "files_to_edit": [],
        "patches": [],
        "test_results": None,
        "test_passed": False,
        "review_comments": [],
        "review_approved": False,
        "retry_count": 0,
        "error_message": None,
        "created_pr_url": None,
        "execution_log": [],
        "execution_events": [],
    }


def run_fixture(issue: str, keep_workspace: bool) -> tuple[dict, str | None]:
    temp_dir = tempfile.mkdtemp(prefix="nestjs-smoke-")
    workspace_dir = Path(temp_dir) / "workspace"
    shutil.copytree(FIXTURE_DIR, workspace_dir)

    os.environ.setdefault("SANDBOX_MODE", "local")

    graph = build_graph()
    final_state = graph.invoke(build_initial_state(issue, str(workspace_dir)))

    if keep_workspace:
        return final_state, str(workspace_dir)

    shutil.rmtree(temp_dir, ignore_errors=True)
    return final_state, None


def main() -> int:
    args = parse_args()
    final_state, workspace_dir = run_fixture(args.issue, args.keep_workspace)

    if args.json:
        print(json.dumps(final_state, indent=2, ensure_ascii=True))
    else:
        print(f"Issue: {args.issue}")
        print(f"Tests passed: {final_state.get('test_passed', False)}")
        print(f"Review approved: {final_state.get('review_approved', False)}")
        print(f"Changed files: {', '.join(patch.get('file', '') for patch in final_state.get('patches', []))}")
        print("Review comments:")
        for comment in final_state.get("review_comments", []):
            print(f"- {comment}")
        if workspace_dir:
            print(f"Workspace kept at: {workspace_dir}")

    if final_state.get("test_passed") and final_state.get("review_approved"):
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())