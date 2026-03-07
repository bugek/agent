from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from statistics import mean

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ai_code_agent.agents.planner import PlannerAgent
from ai_code_agent.config import AgentConfig
from ai_code_agent.tools.workspace_profile import detect_workspace_profile

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "retrieval-eval-sample"
CASES_PATH = FIXTURE_DIR / "cases.json"


class NullLLM:
    def generate_json(self, system_prompt: str, user_prompt: str, schema: dict | None = None) -> dict:
        return {}


def load_cases() -> list[dict]:
    return json.loads(CASES_PATH.read_text(encoding="utf-8"))


def rank_issue(issue: str, workspace_dir: str, retrieval_mode: str) -> list[str]:
    previous_mode = os.environ.get("RETRIEVAL_MODE")
    os.environ["RETRIEVAL_MODE"] = retrieval_mode
    try:
        config = AgentConfig(workspace_dir=workspace_dir)
        planner = PlannerAgent(config, NullLLM())
        state = {
            "issue_description": issue,
            "workspace_dir": workspace_dir,
        }
        result = planner.run(state)
        return [file_path for file_path in result.get("files_to_edit", []) if is_code_file(file_path)]
    finally:
        if previous_mode is None:
            os.environ.pop("RETRIEVAL_MODE", None)
        else:
            os.environ["RETRIEVAL_MODE"] = previous_mode


def is_code_file(file_path: str) -> bool:
    return file_path.endswith((".py", ".ts", ".tsx", ".js", ".jsx"))


def precision_at_k(ranked: list[str], expected: list[str], k: int) -> float:
    top_k = ranked[:k]
    if not top_k:
        return 0.0
    hits = sum(1 for file_path in top_k if file_path in expected)
    return hits / k


def recall_at_k(ranked: list[str], expected: list[str], k: int) -> float:
    if not expected:
        return 0.0
    top_k = set(ranked[:k])
    hits = sum(1 for file_path in expected if file_path in top_k)
    return hits / len(expected)


def reciprocal_rank(ranked: list[str], expected: list[str]) -> float:
    expected_set = set(expected)
    for index, file_path in enumerate(ranked, start=1):
        if file_path in expected_set:
            return 1.0 / index
    return 0.0


def ndcg_at_k(ranked: list[str], expected: list[str], k: int) -> float:
    expected_set = set(expected)
    dcg = 0.0
    for index, file_path in enumerate(ranked[:k], start=1):
        if file_path in expected_set:
            dcg += 1.0 / index

    ideal_hits = min(len(expected), k)
    if ideal_hits == 0:
        return 0.0
    ideal_dcg = sum(1.0 / index for index in range(1, ideal_hits + 1))
    return dcg / ideal_dcg


def evaluate_mode(retrieval_mode: str, cases: list[dict]) -> dict:
    results: list[dict] = []
    for case in cases:
        ranked = rank_issue(case["issue"], str(FIXTURE_DIR), retrieval_mode)
        top_k = int(case.get("top_k", 5))
        results.append(
            {
                "name": case["name"],
                "issue": case["issue"],
                "top_k": top_k,
                "expected_files": case["expected_files"],
                "ranked_files": ranked[:top_k],
                "precision_at_k": precision_at_k(ranked, case["expected_files"], top_k),
                "recall_at_k": recall_at_k(ranked, case["expected_files"], top_k),
                "reciprocal_rank": reciprocal_rank(ranked, case["expected_files"]),
                "ndcg_at_k": ndcg_at_k(ranked, case["expected_files"], top_k),
            }
        )

    return {
        "mode": retrieval_mode,
        "workspace_profile": detect_workspace_profile(str(FIXTURE_DIR)),
        "cases": results,
        "mean_precision_at_k": mean(case["precision_at_k"] for case in results),
        "mean_recall_at_k": mean(case["recall_at_k"] for case in results),
        "mean_reciprocal_rank": mean(case["reciprocal_rank"] for case in results),
        "mean_ndcg_at_k": mean(case["ndcg_at_k"] for case in results),
    }


def main() -> int:
    cases = load_cases()
    baseline = evaluate_mode("baseline", cases)
    hybrid = evaluate_mode("hybrid", cases)

    summary = {
        "fixture": str(FIXTURE_DIR),
        "baseline": baseline,
        "hybrid": hybrid,
        "delta": {
            "mean_precision_at_k": hybrid["mean_precision_at_k"] - baseline["mean_precision_at_k"],
            "mean_recall_at_k": hybrid["mean_recall_at_k"] - baseline["mean_recall_at_k"],
            "mean_reciprocal_rank": hybrid["mean_reciprocal_rank"] - baseline["mean_reciprocal_rank"],
            "mean_ndcg_at_k": hybrid["mean_ndcg_at_k"] - baseline["mean_ndcg_at_k"],
        },
    }
    print(json.dumps(summary, indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
