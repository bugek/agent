from abc import ABC, abstractmethod
import re
from typing import Any
from ai_code_agent.config import AgentConfig
from ai_code_agent.orchestrator import AgentState


ANALYSIS_ONLY_PATTERN = re.compile(r"(?<!-)\b(analyze|inspect|summari[sz]e|review|readiness)\b(?!-)", re.I)


def is_analysis_only_request(issue: str) -> bool:
    if not isinstance(issue, str) or not issue.strip():
        return False
    return bool(ANALYSIS_ONLY_PATTERN.search(issue))

class BaseAgent(ABC):
    """Abstract base class for all specialized agents."""
    
    def __init__(self, config: AgentConfig, llm_client: Any):
        self.config = config
        self.llm = llm_client
        
    @abstractmethod
    def run(self, state: AgentState) -> dict:
        """
        Execute the agent's logic.
        Returns a dictionary containing the delta to be applied to the AgentState.
        """
        raise NotImplementedError
