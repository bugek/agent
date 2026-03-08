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
        changed_files = sorted({patch.get("file") for patch in patches if patch.get("file")})
        validation_signals = self._extract_validation_signals(state.get("test_results", ""))
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
            "changed_files": changed_files,
            "validation_signals": validation_signals,
            "visual_review": state.get("visual_review"),
            "codegen_summary": state.get("codegen_summary", {}),
            "analysis_only": analysis_only,
        }
        llm_review = self.llm.generate_json(REVIEWER_SYSTEM_PROMPT, json.dumps(review_payload, indent=2))
        comments.extend(self._normalize_comments(llm_review.get("review_comments", [])))
        comments.extend(self._visual_review_comments(state.get("visual_review"), analysis_only))

        review_approved = state.get("test_passed", False) and (analysis_only or bool(patches))
        if not analysis_only and "review_approved" in llm_review:
            review_approved = review_approved and bool(llm_review["review_approved"])
        if not analysis_only and self._visual_review_has_blockers(state.get("visual_review")):
            review_approved = False

        if not comments:
            comments.append("Review passed.")

        return {
            "review_approved": review_approved,
            "review_comments": comments,
        }

    def _extract_validation_signals(self, test_results: str) -> list[dict[str, object]]:
        signals: list[dict[str, object]] = []
        for match in re.finditer(r"^([A-Za-z0-9:_-]+)\(exit=(\d+)\):", test_results or "", re.M):
            signals.append({
                "label": match.group(1),
                "exit_code": int(match.group(2)),
            })
        return signals

    def _normalize_comments(self, raw_comments: object) -> list[str]:
        if isinstance(raw_comments, str):
            return [raw_comments]
        if isinstance(raw_comments, list):
            return [comment for comment in raw_comments if isinstance(comment, str) and comment.strip()]
        return []

    def _visual_review_comments(self, visual_review: object, analysis_only: bool) -> list[str]:
        if analysis_only or not isinstance(visual_review, dict) or not visual_review.get("enabled"):
            return []

        state_coverage = visual_review.get("state_coverage") or {}
        comments: list[str] = []
        missing_states = [
            state_name
            for state_name, covered in state_coverage.items()
            if state_name in {"loading_state", "empty_state", "error_state", "success_state"} and not covered
        ]
        if missing_states:
            comments.append(f"Frontend visual review is missing component states: {', '.join(sorted(missing_states))}.")

        if not state_coverage.get("loading_file"):
            comments.append("Frontend visual review did not find a loading.tsx/loading.ts companion file for the changed route.")
        if not state_coverage.get("error_file"):
            comments.append("Frontend visual review did not find an error.tsx/error.ts companion file for the changed route.")

        screenshot_status = visual_review.get("screenshot_status")
        if screenshot_status == "failed":
            comments.append("Frontend screenshot or visual-review command failed.")
        elif screenshot_status == "missing_artifacts":
            comments.append("Frontend screenshot command completed without producing any screenshot artifacts or manifest metadata.")
        elif screenshot_status == "not_configured":
            comments.append("Frontend screenshot review is not configured; relying on structural visual checks only.")

        responsive_review = visual_review.get("responsive_review") or {}
        missing_categories = responsive_review.get("missing_categories") or []
        if screenshot_status == "passed" and missing_categories:
            comments.append(
                f"Frontend visual review is missing responsive viewport coverage for: {', '.join(missing_categories)}."
            )

        missing_viewport_metadata = responsive_review.get("missing_viewport_metadata") or []
        if screenshot_status == "passed" and missing_viewport_metadata:
            comments.append("Frontend visual review produced screenshots without viewport metadata, so responsive coverage could not be verified.")

        return comments

    def _visual_review_has_blockers(self, visual_review: object) -> bool:
        if not isinstance(visual_review, dict) or not visual_review.get("enabled"):
            return False
        state_coverage = visual_review.get("state_coverage") or {}
        required_flags = ["loading_state", "empty_state", "error_state", "success_state", "loading_file", "error_file"]
        if any(not state_coverage.get(flag) for flag in required_flags):
            return True
        responsive_review = visual_review.get("responsive_review") or {}
        if visual_review.get("screenshot_status") == "passed":
            if responsive_review.get("missing_categories"):
                return True
            if responsive_review.get("missing_viewport_metadata"):
                return True
        return visual_review.get("screenshot_status") in {"failed", "missing_artifacts"}
