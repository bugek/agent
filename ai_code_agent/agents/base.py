from abc import ABC, abstractmethod
from typing import Any
from ai_code_agent.config import AgentConfig
from ai_code_agent.orchestrator import AgentState

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
