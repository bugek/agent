# AI Code Agent - Project Structure & Agent Guide

Welcome to the AI Code Agent project! This file serves as the primary documentation for both human developers and AI coding assistants navigating this repository.

## Purpose

This project is an autonomous software engineering agent system. It is designed to take an issue description, search the codebase, formulate a plan, edit code, run tests within a sandbox, and ultimately submit a Pull Request.

## Architecture Highlights

We use a Multi-Agent architecture orchestrated via a State Machine. When `langgraph` is installed the project uses it directly; otherwise it falls back to a local in-process executor so the CLI can still run smoke checks.

### Code Structure

- `/ai_code_agent/orchestrator.py`: The heart of the system. Defines the `AgentState` and the LangGraph flow connecting all agents.
- `/ai_code_agent/agents/`: Directory containing specific agent logic (`planner.py`, `coder.py`, `reviewer.py`, `tester.py`).
- `/ai_code_agent/tools/`: The Agent-Computer Interface (ACI). Tools for agents to touch the real world safely (e.g., `file_editor.py`, `code_search.py`, `sandbox.py`).
- `/ai_code_agent/integrations/`: Connectors to GitHub, Azure DevOps, etc.
- `/ai_code_agent/llm/`: Centralized LLM client and prompts.
- `/ai_code_agent/main.py`: CLI Entrypoint.
- `/ai_code_agent/webhook.py`: Server entrypoint for responding to events.

## Agent Workflow Loop

1. **Planner**: Decides *what* to do based on the issue and code context.
2. **Coder**: Edits files based on the plan.
3. **Tester**: Runs smoke checks in a sandbox, falling back to local execution when Docker is unavailable.
4. **Reviewer**: Evaluates diffs and test results.
5. **Decide**: The orchestrator assesses the Reviewer's feedback. If failed, it loops back to Coder. If passed, it interacts with Git to create a PR.

## LLM Providers

- `anthropic`: direct Anthropic API
- `openai`: direct OpenAI API
- `openrouter`: OpenAI-compatible client pointed at OpenRouter so one API key can route across multiple upstream models

Use `LLM_PROVIDER=openrouter` with `OPENROUTER_API_KEY` and optionally `OPENROUTER_MODEL` to select the routed model.
You can also override models per role with `PLANNER_MODEL`, `CODER_MODEL`, `TESTER_MODEL`, and `REVIEWER_MODEL`.

## Getting Started

1. Copy `.env.example` to `.env` and fill in credentials.
2. Install dependencies via `poetry install`.
3. Build the sandbox image if you want Docker-backed execution: `docker build -t ai-code-agent-sandbox:latest .`
4. Run the CLI for an issue workflow: `poetry run ai-code-agent run --issue <issue_url> --repo <path>`
5. Run the CLI without API keys to use fallback mode for planning and smoke-test execution only.
6. Run `poetry run ai-code-agent health --role planner` to verify provider wiring and the effective model for a role.
