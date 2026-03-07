import json
import re
from collections import defaultdict
from pathlib import Path

from ai_code_agent.agents.base import BaseAgent
from ai_code_agent.orchestrator import AgentState
from ai_code_agent.llm.prompts import PLANNER_SYSTEM_PROMPT
from ai_code_agent.tools.code_search import CodeSearch

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
        workspace_profile = self._detect_workspace_profile(state["workspace_dir"])
        scored_files = self._score_candidate_files(search, keywords)

        candidate_files = [file_path for file_path, _ in scored_files[:10]]
        candidate_files = self._prioritize_profile_files(candidate_files, workspace_profile)

        if not candidate_files:
            candidate_files = [
                file_path for file_path in search.list_files("ai_code_agent") if file_path.endswith(".py")
            ][:10]

        prompt_payload = {
            "issue": issue,
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

    def _detect_workspace_profile(self, workspace_dir: str) -> dict:
        root = Path(workspace_dir)
        profile = {
            "has_python": (root / "pyproject.toml").exists(),
            "has_package_json": (root / "package.json").exists(),
            "frameworks": [],
            "package_manager": None,
            "scripts": [],
            "priority_files": [],
        }

        if profile["has_python"]:
            profile["frameworks"].append("python")
            profile["priority_files"].extend(["pyproject.toml"])

        if profile["has_package_json"]:
            package_json_path = root / "package.json"
            try:
                package_data = json.loads(package_json_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                package_data = {}

            dependencies = {
                **package_data.get("dependencies", {}),
                **package_data.get("devDependencies", {}),
            }
            scripts = package_data.get("scripts", {})
            profile["scripts"] = sorted(scripts.keys())
            profile["priority_files"].append("package.json")

            if "next" in dependencies or any((root / name).exists() for name in ["next.config.js", "next.config.mjs", "next.config.ts"]):
                profile["frameworks"].append("nextjs")
                profile["priority_files"].extend([
                    "package.json",
                    "next.config.js",
                    "next.config.mjs",
                    "next.config.ts",
                    "app/page.tsx",
                    "src/app/page.tsx",
                ])

            if "@nestjs/core" in dependencies or (root / "nest-cli.json").exists():
                profile["frameworks"].append("nestjs")
                profile["priority_files"].extend([
                    "package.json",
                    "nest-cli.json",
                    "src/app.module.ts",
                ])

            if (root / "pnpm-lock.yaml").exists():
                profile["package_manager"] = "pnpm"
            elif (root / "yarn.lock").exists():
                profile["package_manager"] = "yarn"
            elif (root / "package-lock.json").exists():
                profile["package_manager"] = "npm"
            else:
                profile["package_manager"] = "npm"

        return profile

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
