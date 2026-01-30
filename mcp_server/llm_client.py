from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from openai import OpenAI

from .config import Settings

Role = Literal["system", "user", "assistant"]


class LLMClient:
    """
    Minimal LLM wrapper.

    Currently supports OpenAI's Chat Completions API via the `openai` SDK.
    The interface is intentionally simple so we can later add support for
    other providers (Anthropic, etc.) behind the same methods.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        if settings.llm_provider != "openai":
            raise ValueError(
                f"Unsupported LLM provider '{settings.llm_provider}'. "
                "For now only 'openai' is wired; extend LLMClient as needed."
            )
        self._client = OpenAI(api_key=settings.llm_api_key)

    def complete_json(
        self,
        system_prompt: str,
        user_prompt: str,
        response_schema: Optional[Dict[str, Any]] = None,
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Request a JSON response from the LLM.

        This is a thin wrapper that can evolve to use structured outputs.
        """
        messages: List[Dict[str, str]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        resp = self._client.chat.completions.create(
            model=model or "gpt-4o-mini",
            messages=messages,  # type: ignore[arg-type]
            temperature=0.2,
            response_format={"type": "json_object"},
        )
        content = resp.choices[0].message.content or "{}"
        import json

        return json.loads(content)

