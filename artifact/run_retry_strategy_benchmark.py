from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai_code_agent.agents.tester import TesterAgent
from ai_code_agent.config import AgentConfig
from ai_code_agent.tools.workspace_profile import detect_workspace_profile


class NullLLM:
    def generate_json(self, system_prompt: str, user_prompt: str, schema: dict | None = None) -> dict:
        return {}


def benchmark_workspace(agent: TesterAgent, workspace_dir: Path, state: dict) -> dict[str, object]:
    profile = detect_workspace_profile(str(workspace_dir))
    full_plan = agent._build_validation_plan({**state, "retry_count": 0}, profile)
    retry_plan = agent._build_validation_plan(state, profile)
    return {
        "workspace": workspace_dir.as_posix(),
        "frameworks": profile.get("frameworks", []),
        "full_labels": full_plan["selected_labels"],
        "retry_labels": retry_plan["selected_labels"],
        "skipped_labels": retry_plan["skipped_labels"],
        "retry_strategy": retry_plan["strategy"],
        "command_reduction": len(full_plan["selected_labels"]) - len(retry_plan["selected_labels"]),
    }


def main() -> None:
    root = ROOT
    agent = TesterAgent(AgentConfig(workspace_dir=str(root)), NullLLM())
    cases = [
        {
            "workspace_dir": root / "artifact/fixtures/nextjs-visual-review",
            "state": {
                "workspace_dir": str(root / "artifact/fixtures/nextjs-visual-review"),
                "retry_count": 1,
                "testing_summary": {"failed_commands": ["script:test", "script:visual-review"]},
                "review_summary": {
                    "status": "changes_required",
                    "visual_review": {"screenshot_status": "missing_artifacts", "missing_states": [], "missing_responsive_categories": []},
                    "remediation": {"required": True, "failed_validation_labels": ["script:test"]},
                },
            },
        },
        {
            "workspace_dir": root / "artifact/fixtures/nestjs-smoke",
            "state": {
                "workspace_dir": str(root / "artifact/fixtures/nestjs-smoke"),
                "retry_count": 1,
                "testing_summary": {"failed_commands": ["script:build"]},
                "review_summary": {
                    "status": "changes_required",
                    "remediation": {"required": True, "failed_validation_labels": ["script:build"]},
                },
            },
        },
    ]
    results = [benchmark_workspace(agent, case["workspace_dir"], case["state"]) for case in cases]
    print(json.dumps({"results": results}, indent=2, ensure_ascii=True))


if __name__ == "__main__":
    main()