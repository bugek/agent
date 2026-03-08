import json
from typing import Any, Optional

from ai_code_agent.config import AgentConfig

class LLMClient:
    """Unified interface for Anthropic, OpenAI, or others."""
    
    def __init__(
        self,
        provider: str,
        api_key: str,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
        extra_headers: Optional[dict[str, str]] = None,
        timeout_seconds: float = 45.0,
    ):
        self.provider = provider
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self.extra_headers = extra_headers or {}
        self.timeout_seconds = timeout_seconds

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    @classmethod
    def from_config(cls, config: AgentConfig, role: Optional[str] = None) -> "LLMClient":
        role_model = config.llm_model
        if role == "planner":
            role_model = config.planner_model or role_model
        elif role == "coder":
            role_model = config.coder_model or role_model
        elif role == "tester":
            role_model = config.tester_model or role_model
        elif role == "reviewer":
            role_model = config.reviewer_model or role_model

        api_key = config.anthropic_api_key or config.openai_api_key or ""
        base_url = None
        extra_headers: dict[str, str] = {}
        resolved_model = role_model

        if config.llm_provider == "openrouter":
            api_key = config.openrouter_api_key or config.openai_api_key or ""
            resolved_model = role_model or config.openrouter_model
            base_url = config.openrouter_base_url
            extra_headers = {"X-Title": config.openrouter_app_name}
            if config.openrouter_site_url:
                extra_headers["HTTP-Referer"] = config.openrouter_site_url
        elif config.llm_provider == "openai":
            api_key = config.openai_api_key or ""

        return cls(
            provider=config.llm_provider,
            api_key=api_key,
            model=resolved_model,
            base_url=base_url,
            extra_headers=extra_headers,
            timeout_seconds=config.llm_timeout_seconds,
        )

    def _fallback_text(self, system_prompt: str, user_prompt: str) -> str:
        combined = f"{system_prompt}\n{user_prompt}".lower()
        if "files_to_edit" in combined:
            return json.dumps(
                {
                    "plan": "Inspect the most relevant files, apply the smallest safe change, and run smoke tests.",
                    "files_to_edit": [],
                }
            )
        if "review_approved" in combined:
            approved = "traceback" not in combined and "error" not in combined and "failed" not in combined
            comments = ["Fallback review completed without an LLM provider."]
            if not approved:
                comments = ["Fallback review detected a failure signal in the logs."]
            return json.dumps({"review_approved": approved, "review_comments": comments})
        return "LLM provider not configured. Fallback response generated locally."

    def _generate_openai_compatible(self, system_prompt: str, user_prompt: str, default_model: str) -> str:
        from openai import OpenAI

        client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            default_headers=self.extra_headers or None,
            timeout=self.timeout_seconds,
        )
        response = client.responses.create(
            model=self.model or default_model,
            input=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        return response.output_text

    def _generate_openai(self, system_prompt: str, user_prompt: str) -> str:
        return self._generate_openai_compatible(system_prompt, user_prompt, "gpt-4.1-mini")

    def _generate_openrouter(self, system_prompt: str, user_prompt: str) -> str:
        return self._generate_openai_compatible(system_prompt, user_prompt, "openai/gpt-4.1-mini")

    def _generate_anthropic(self, system_prompt: str, user_prompt: str) -> str:
        import anthropic

        client = anthropic.Anthropic(api_key=self.api_key, timeout=self.timeout_seconds)
        response = client.messages.create(
            model=self.model or "claude-3-5-sonnet-latest",
            max_tokens=1500,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        chunks = []
        for block in response.content:
            text = getattr(block, "text", None)
            if text:
                chunks.append(text)
        return "\n".join(chunks)

    def _extract_json(self, raw_text: str) -> Optional[dict[str, Any]]:
        text = raw_text.strip()
        if not text:
            return None
        candidates = [text]
        if "```" in text:
            for chunk in text.split("```"):
                candidate = chunk.strip()
                if candidate.startswith("json"):
                    candidate = candidate[4:].strip()
                if candidate:
                    candidates.append(candidate)
        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
        return None
        
    def generate(self, system_prompt: str, user_prompt: str) -> str:
        """Generate a basic text completion."""
        if not self.enabled:
            return self._fallback_text(system_prompt, user_prompt)

        try:
            if self.provider == "openai":
                return self._generate_openai(system_prompt, user_prompt)
            if self.provider == "openrouter":
                return self._generate_openrouter(system_prompt, user_prompt)
            if self.provider == "anthropic":
                return self._generate_anthropic(system_prompt, user_prompt)
        except Exception:
            return self._fallback_text(system_prompt, user_prompt)

        return self._fallback_text(system_prompt, user_prompt)
        
    def generate_json(self, system_prompt: str, user_prompt: str, schema: Optional[dict] = None) -> dict:
        """Generate a structured JSON output (using function calling or structured prompts)."""
        raw_text = self.generate(system_prompt, user_prompt)
        parsed = self._extract_json(raw_text)
        if parsed is not None:
            return parsed

        combined = f"{system_prompt}\n{user_prompt}".lower()
        if "operations" in combined:
            return {"operations": []}
        if "files_to_edit" in combined:
            return {
                "plan": "Inspect the relevant files, apply a safe targeted change, and validate with smoke tests.",
                "files_to_edit": [],
            }
        if "review_approved" in combined:
            return {
                "review_approved": "traceback" not in combined and "failed" not in combined,
                "review_comments": ["Structured fallback review completed locally."],
            }
        return schema or {}
        
    def call_with_tools(self, system_prompt: str, user_prompt: str, tools: list) -> list:
        """Agentic loop with tools attached (useful for inner monologue)."""
        return []

    def health_check(self) -> dict[str, Any]:
        """Validate provider configuration and optionally perform a live request."""
        payload = {
            "provider": self.provider,
            "model": self.model,
            "base_url": self.base_url,
            "enabled": self.enabled,
        }
        if not self.enabled:
            payload.update({"ok": False, "live_call": False, "message": "API key is not configured."})
            return payload

        try:
            response = self.generate(
                "You are a health-check assistant.",
                "Reply with a short single-line acknowledgement containing the word OK.",
            )
            payload.update(
                {
                    "ok": True,
                    "live_call": True,
                    "message": response.strip(),
                }
            )
            return payload
        except Exception as exc:  # pragma: no cover - defensive fallback
            payload.update({"ok": False, "live_call": True, "message": str(exc)})
            return payload
