import json
import re

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

        candidate_files: list[str] = []
        for keyword in keywords[:6]:
            for match in search.search_text(keyword)[:6]:
                file_path = match.split(":", 1)[0].replace("\\", "/")
                if file_path.startswith("artifact/"):
                    continue
                if file_path not in candidate_files:
                    candidate_files.append(file_path)

        if not candidate_files:
            candidate_files = [
                file_path for file_path in search.list_files("ai_code_agent") if file_path.endswith(".py")
            ][:10]

        prompt_payload = {
            "issue": issue,
            "candidate_files": candidate_files[:10],
        }
        response = self.llm.generate_json(PLANNER_SYSTEM_PROMPT, json.dumps(prompt_payload, indent=2))
        plan = response.get("plan") or self._fallback_plan(issue, candidate_files)
        files_to_edit = response.get("files_to_edit") or candidate_files[:10]

        return {
            "plan": plan,
            "files_to_edit": files_to_edit,
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
