# AI Code Agent - Project Scaffolding Walkthrough

The project structure for the **AI Code Agent** has been successfully set up in `d:\work\agent`. This repository is now ready for the underlying logic to be implemented.

## What Was Created

I have created all the foundational files and a robust architecture layout based on a multi-agent orchestrated pattern.

### 1. Root Configuration files
- **[pyproject.toml](file:///d:/work/agent/pyproject.toml)**: Set up with Poetry, specifying dependencies such as `langchain`, `langgraph`, `anthropic`, `pygithub`, and `docker`.
- **[.env.example](file:///d:/work/agent/.env.example)**: Environment variable template for LLM keys, version control tokens, and sandbox configs.
- **[Dockerfile](file:///d:/work/agent/Dockerfile)**: A base image configuration for running the safe sandbox environment (execution agent).
- **[AGENTS.md](file:///d:/work/agent/AGENTS.md)**: Primary documentation detailing the project's purpose and agent workflow loop.

### 2. The Agent Core (`ai_code_agent/`)
- **[orchestrator.py](file:///d:/work/agent/ai_code_agent/orchestrator.py)**: The heart of the state machine using LangGraph. It sets up the workflow: Plan ➡ Code ➡ Test ➡ Review ➡ Create PR.
- **[config.py](file:///d:/work/agent/ai_code_agent/config.py)**: Dotenv loader for keys and tokens.

### 3. Agent Microservices (`ai_code_agent/agents/`)
Each agent inherits from [base.py](file:///d:/work/agent/ai_code_agent/agents/base.py) and is strictly responsible for one stage:
- [planner.py](file:///d:/work/agent/ai_code_agent/agents/planner.py): Gathers context and formulates a plan.
- [coder.py](file:///d:/work/agent/ai_code_agent/agents/coder.py): Applies changes to specific files.
- [tester.py](file:///d:/work/agent/ai_code_agent/agents/tester.py): Runs execution scripts inside the Docker sandbox.
- [reviewer.py](file:///d:/work/agent/ai_code_agent/agents/reviewer.py): Critiques the patches and tests to decide if a PR can be merged.

### 4. Agent-Computer Interface Tools (`ai_code_agent/tools/`)
These tools are how the agents touch the local system securely:
- Search: [code_search.py](file:///d:/work/agent/ai_code_agent/tools/code_search.py)
- Editing: [file_editor.py](file:///d:/work/agent/ai_code_agent/tools/file_editor.py)
- Execution: [sandbox.py](file:///d:/work/agent/ai_code_agent/tools/sandbox.py)
- Quality Control: [linter.py](file:///d:/work/agent/ai_code_agent/tools/linter.py)
- VCS: [git_ops.py](file:///d:/work/agent/ai_code_agent/tools/git_ops.py)

### 5. Integrations & LLM (`ai_code_agent/integrations/` & `ai_code_agent/llm/`)
- Unified SDK wrappers for [github_client.py](file:///d:/work/agent/ai_code_agent/integrations/github_client.py) and [azure_devops_client.py](file:///d:/work/agent/ai_code_agent/integrations/azure_devops_client.py).
- A common unified interface [client.py](file:///d:/work/agent/ai_code_agent/llm/client.py) to wrap logic around OpenAI and Anthropic SDKs.
- Clear and strict systemic roles documented in [prompts.py](file:///d:/work/agent/ai_code_agent/llm/prompts.py).

## Next Steps
All architecture modules exist as functional stubs. The next immediate steps for a human/agent developer would be:
1. Initialize the directory with `poetry install`.
2. Fill out `.env` from the example file.
3. Start implementing the internal logic of the tools (`code_search`, `file_editor`, `sandbox`) which power the rest of the orchestration.
