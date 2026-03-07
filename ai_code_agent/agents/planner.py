import json
import re
from collections import defaultdict
from pathlib import Path

from ai_code_agent.agents.base import BaseAgent
from ai_code_agent.orchestrator import AgentState
from ai_code_agent.llm.prompts import PLANNER_SYSTEM_PROMPT
from ai_code_agent.tools.code_search import CodeSearch
from ai_code_agent.tools.workspace_profile import detect_workspace_profile

class PlannerAgent(BaseAgent):
    """
    Agent responsible for analyzing the issue, 
    searching the codebase, and formulating a plan.
    """
    
    def run(self, state: AgentState) -> dict:
        """
        Analyzes the issue description, searches code context,
        and outputs a high-level plan and target files to edit.
        """
        issue = state["issue_description"]
        search = CodeSearch(state["workspace_dir"])
        keywords = self._extract_keywords(issue)
        workspace_profile = detect_workspace_profile(state["workspace_dir"])
        scored_files = self._score_candidate_files(search, keywords)
        scored_files = self._merge_scored_files(scored_files, self._score_nextjs_candidates(state["workspace_dir"], workspace_profile, keywords))

        candidate_files = [file_path for file_path, _ in scored_files[:10]]
        candidate_files = self._prioritize_profile_files(candidate_files, workspace_profile)

        if not candidate_files:
            candidate_files = [
                file_path for file_path in search.list_files("ai_code_agent") if file_path.endswith(".py")
            ][:10]

        prompt_payload = {
            "issue": issue,
            "workspace_profile": workspace_profile,
            "candidate_files": candidate_files[:10],
        }
        response = self.llm.generate_json(PLANNER_SYSTEM_PROMPT, json.dumps(prompt_payload, indent=2))
        plan = self._normalize_plan(response.get("plan")) or self._fallback_plan(issue, candidate_files)
        files_to_edit = response.get("files_to_edit") or candidate_files[:10]

        return {
            "plan": plan,
            "files_to_edit": files_to_edit,
            "workspace_profile": workspace_profile,
            "planning_context": {
                "keywords": keywords[:10],
                "workspace_profile": workspace_profile,
                "candidate_scores": [
                    {"file_path": file_path, "score": score} for file_path, score in scored_files[:10]
                ],
            },
        }

    def _extract_keywords(self, issue: str) -> list[str]:
        words = re.findall(r"[A-Za-z_][A-Za-z0-9_\-]+", issue.lower())
        stop_words = {
            "the",
            "and",
            "for",
            "with",
            "that",
            "this",
            "from",
            "make",
            "into",
            "agent",
            "code",
        }
        return [word for word in words if len(word) > 2 and word not in stop_words]

    def _fallback_plan(self, issue: str, candidate_files: list[str]) -> str:
        steps = [
            f"Review the issue: {issue}",
            "Inspect the most relevant files and determine the smallest safe implementation change.",
            "Apply the code changes and run smoke tests.",
        ]
        if candidate_files:
            steps.insert(1, f"Start with: {', '.join(candidate_files[:5])}")
        return "\n".join(f"- {step}" for step in steps)

    def _score_candidate_files(self, search: CodeSearch, keywords: list[str]) -> list[tuple[str, int]]:
        scores: dict[str, int] = defaultdict(int)
        for keyword in keywords[:8]:
            for match in search.search_text(keyword)[:8]:
                file_path = match.split(":", 1)[0].replace("\\", "/")
                if self._skip_file(file_path):
                    continue
                scores[file_path] += 2
            for match in search.search_symbol(keyword)[:4]:
                file_path = match.split(":", 1)[0].replace("\\", "/")
                if self._skip_file(file_path):
                    continue
                scores[file_path] += 3

        ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        return ranked

    def _skip_file(self, file_path: str) -> bool:
        return file_path.startswith("artifact/") or file_path.startswith(".git/")

    def _prioritize_profile_files(self, candidate_files: list[str], workspace_profile: dict) -> list[str]:
        prioritized: list[str] = []
        seen: set[str] = set()
        workspace_root = Path(self.config.workspace_dir)

        for file_path in workspace_profile.get("priority_files", []):
            normalized = file_path.replace("\\", "/")
            if normalized not in seen and (workspace_root / normalized).exists():
                prioritized.append(normalized)
                seen.add(normalized)

        for file_path in candidate_files:
            if file_path not in seen:
                prioritized.append(file_path)
                seen.add(file_path)

        return prioritized[:10]

    def _normalize_plan(self, plan: object) -> str:
        if isinstance(plan, str):
            return plan
        if isinstance(plan, list):
            return "\n".join(f"- {item}" for item in plan if isinstance(item, str))
        return ""

    def _score_nextjs_candidates(self, workspace_dir: str, workspace_profile: dict, keywords: list[str]) -> list[tuple[str, int]]:
        nextjs_profile = workspace_profile.get("nextjs")
        if not nextjs_profile:
            return []

        root = Path(workspace_dir)
        scores: dict[str, int] = defaultdict(int)
        normalized_keywords = [keyword.lower() for keyword in keywords]

        for file_path in nextjs_profile.get("route_files", []):
            score = self._score_path_keywords(file_path, normalized_keywords)
            if file_path.endswith(("page.tsx", "page.ts", "page.jsx", "page.js", "index.tsx", "index.ts", "index.jsx", "index.js")):
                score += 3
            if score:
                scores[file_path] += score

        for file_path in nextjs_profile.get("layout_files", []):
            score = self._score_path_keywords(file_path, normalized_keywords)
            scores[file_path] += score + 2

        for file_path in nextjs_profile.get("special_files", []):
            score = self._score_path_keywords(file_path, normalized_keywords)
            if score:
                scores[file_path] += score + 1

        for file_path in nextjs_profile.get("api_routes", []):
            score = self._score_path_keywords(file_path, normalized_keywords)
            if score:
                scores[file_path] += score + 2

        for directory in nextjs_profile.get("component_directories", []):
            base = root / directory
            for file_path in base.rglob("*"):
                if not file_path.is_file() or file_path.suffix not in {".tsx", ".ts", ".jsx", ".js"}:
                    continue
                relative_path = file_path.relative_to(root).as_posix()
                score = self._score_path_keywords(relative_path, normalized_keywords)
                if score:
                    scores[relative_path] += score + 1

        return sorted(scores.items(), key=lambda item: (-item[1], item[0]))

    def _merge_scored_files(
        self,
        base_scores: list[tuple[str, int]],
        extra_scores: list[tuple[str, int]],
    ) -> list[tuple[str, int]]:
        merged: dict[str, int] = defaultdict(int)
        for file_path, score in [*base_scores, *extra_scores]:
            merged[file_path] += score
        return sorted(merged.items(), key=lambda item: (-item[1], item[0]))

    def _score_path_keywords(self, file_path: str, keywords: list[str]) -> int:
        normalized = file_path.lower()
        score = 0
        for keyword in keywords:
            if keyword in normalized:
                score += 4
        return score
