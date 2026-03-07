import json
import re

from ai_code_agent.agents.base import BaseAgent
from ai_code_agent.orchestrator import AgentState
from ai_code_agent.llm.prompts import REVIEWER_SYSTEM_PROMPT

class ReviewerAgent(BaseAgent):
    """
    Agent responsible for evaluating the code changes and test results.
    """
    
    def run(self, state: AgentState) -> dict:
        """
        Reviews patches and test output to decide if the code is ready for PR.
        """
        analysis_only = bool(re.search(r"\b(analyze|inspect|summari[sz]e|review)\b", state["issue_description"], re.I))
        patches = state.get("patches", [])
        comments: list[str] = []

        if not state.get("test_passed", False):
            comments.append("Smoke tests failed.")

        if not patches and not analysis_only:
            comments.append("No code changes were produced for a change-oriented request.")

        if state.get("error_message"):
            comments.append(state["error_message"])

        review_payload = {
            "issue": state["issue_description"],
            "test_results": state.get("test_results", ""),
            "patch_count": len(patches),
            "analysis_only": analysis_only,
        }
        llm_review = self.llm.generate_json(REVIEWER_SYSTEM_PROMPT, json.dumps(review_payload, indent=2))
        comments.extend(llm_review.get("review_comments", []))

        review_approved = state.get("test_passed", False) and (analysis_only or bool(patches))
        if "review_approved" in llm_review:
            review_approved = review_approved and bool(llm_review["review_approved"])

        if not comments:
            comments.append("Review passed.")

        return {
            "review_approved": review_approved,
            "review_comments": comments,
        }
