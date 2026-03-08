import os
from dataclasses import dataclass, field
from typing import Optional

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional dependency
    load_dotenv = None

if load_dotenv is not None:
    load_dotenv()


def _split_globs(value: str | None, default: list[str] | None = None) -> list[str]:
    raw_value = value if value is not None else ",".join(default or [])
    return [item.strip() for item in raw_value.split(",") if item.strip()]

@dataclass
class AgentConfig:
    """Configuration for the AI Code Agent."""
    
    # LLM Settings
    llm_provider: str = field(default_factory=lambda: os.getenv("LLM_PROVIDER", "anthropic"))
    llm_model: Optional[str] = field(default_factory=lambda: os.getenv("LLM_MODEL"))
    planner_model: Optional[str] = field(default_factory=lambda: os.getenv("PLANNER_MODEL"))
    coder_model: Optional[str] = field(default_factory=lambda: os.getenv("CODER_MODEL"))
    tester_model: Optional[str] = field(default_factory=lambda: os.getenv("TESTER_MODEL"))
    reviewer_model: Optional[str] = field(default_factory=lambda: os.getenv("REVIEWER_MODEL"))
    anthropic_api_key: Optional[str] = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY"))
    openai_api_key: Optional[str] = field(default_factory=lambda: os.getenv("OPENAI_API_KEY"))
    openrouter_api_key: Optional[str] = field(default_factory=lambda: os.getenv("OPENROUTER_API_KEY"))
    openrouter_model: str = field(default_factory=lambda: os.getenv("OPENROUTER_MODEL", "openai/gpt-4.1-mini"))
    openrouter_base_url: str = field(default_factory=lambda: os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"))
    openrouter_app_name: str = field(default_factory=lambda: os.getenv("OPENROUTER_APP_NAME", "ai-code-agent"))
    openrouter_site_url: Optional[str] = field(default_factory=lambda: os.getenv("OPENROUTER_SITE_URL"))
    llm_timeout_seconds: float = field(default_factory=lambda: float(os.getenv("LLM_TIMEOUT_SECONDS", "45")))
    
    # Integrations
    github_token: Optional[str] = field(default_factory=lambda: os.getenv("GITHUB_TOKEN"))
    github_repo: Optional[str] = field(default_factory=lambda: os.getenv("GITHUB_REPO"))
    github_base_branch: str = field(default_factory=lambda: os.getenv("GITHUB_BASE_BRANCH", "main"))
    azure_devops_pat: Optional[str] = field(default_factory=lambda: os.getenv("AZURE_DEVOPS_PAT"))
    azure_devops_org_url: Optional[str] = field(default_factory=lambda: os.getenv("AZURE_DEVOPS_ORG_URL"))
    azure_devops_project: Optional[str] = field(default_factory=lambda: os.getenv("AZURE_DEVOPS_PROJECT"))
    azure_devops_repo: Optional[str] = field(default_factory=lambda: os.getenv("AZURE_DEVOPS_REPO"))
    azure_devops_target_branch: str = field(default_factory=lambda: os.getenv("AZURE_DEVOPS_TARGET_BRANCH", "main"))
    
    # Sandbox
    sandbox_mode: str = field(default_factory=lambda: os.getenv("SANDBOX_MODE", "auto"))
    docker_image: str = field(default_factory=lambda: os.getenv("DOCKER_IMAGE_NAME", "ai-code-agent-sandbox:latest"))

    # Runtime behavior
    workspace_dir: str = field(default_factory=lambda: os.getenv("AGENT_WORKSPACE_DIR", "."))
    auto_commit: bool = field(default_factory=lambda: os.getenv("AUTO_COMMIT", "false").lower() == "true")
    auto_push: bool = field(default_factory=lambda: os.getenv("AUTO_PUSH", "false").lower() == "true")
    retrieval_mode: str = field(default_factory=lambda: os.getenv("RETRIEVAL_MODE", "hybrid"))
    edit_allow_globs: list[str] = field(default_factory=lambda: _split_globs(os.getenv("AGENT_EDIT_ALLOW_GLOBS")))
    edit_deny_globs: list[str] = field(default_factory=lambda: _split_globs(os.getenv("AGENT_EDIT_DENY_GLOBS"), [".git/**"]))
    
    # Internal orchestrator limits
    max_retries: int = int(os.getenv("MAX_RETRIES", "3"))
    retry_history_window: int = int(os.getenv("RETRY_HISTORY_WINDOW", "10"))
    retry_policy_min_samples: int = int(os.getenv("RETRY_POLICY_MIN_SAMPLES", "2"))
    retry_policy_min_confidence_gap: float = float(os.getenv("RETRY_POLICY_MIN_CONFIDENCE_GAP", "0.15"))
    retry_policy_stop_success_rate: float = float(os.getenv("RETRY_POLICY_STOP_SUCCESS_RATE", "0.25"))
