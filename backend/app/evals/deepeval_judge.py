"""DeepEval judge adapter for the configured OpenAI-compatible provider."""

from __future__ import annotations

import json
import re
from typing import Any

import httpx
from deepeval.models import DeepEvalBaseLLM
from pydantic import BaseModel

from app.llm.factory import LLMConfig


class OpenAICompatibleDeepEvalJudge(DeepEvalBaseLLM):
    """Use the project's LLM provider as a schema-aware DeepEval judge."""

    def __init__(self, config: LLMConfig | None = None) -> None:
        self.config = config or LLMConfig.from_env()
        if not self.config.enabled:
            raise ValueError("LLM_ENABLED must be true for DeepEval judge runs.")
        if not self.config.base_url or not self.config.api_key or not self.config.model:
            raise ValueError("LLM_BASE_URL, LLM_API_KEY, and LLM_MODEL are required.")

    def load_model(self) -> "OpenAICompatibleDeepEvalJudge":
        """Return this lightweight HTTP-backed judge adapter."""
        return self

    def get_model_name(self) -> str:
        """Return the configured judge model name."""
        return f"openai-compatible:{self.config.model}"

    def generate(
        self,
        prompt: str,
        schema: type[BaseModel] | None = None,
        **_: Any,
    ) -> str | BaseModel:
        """Generate one judge response, optionally validated against a schema."""
        with httpx.Client(timeout=self.config.timeout_seconds) as client:
            response = client.post(
                self._endpoint,
                headers=self._headers,
                json=self._payload(prompt, schema),
            )
            response.raise_for_status()
            content = _response_content(response)
        return _validated_output(content, schema)

    async def a_generate(
        self,
        prompt: str,
        schema: type[BaseModel] | None = None,
        **_: Any,
    ) -> str | BaseModel:
        """Asynchronously generate one schema-aware judge response."""
        async with httpx.AsyncClient(timeout=self.config.timeout_seconds) as client:
            response = await client.post(
                self._endpoint,
                headers=self._headers,
                json=self._payload(prompt, schema),
            )
            response.raise_for_status()
            content = _response_content(response)
        return _validated_output(content, schema)

    @property
    def _endpoint(self) -> str:
        return f"{self.config.base_url.rstrip('/')}/chat/completions"

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }

    def _payload(
        self,
        prompt: str,
        schema: type[BaseModel] | None,
    ) -> dict[str, object]:
        system_prompt = "You are a careful LLM evaluation judge."
        payload: dict[str, object] = {
            "model": self.config.model or "",
            "temperature": 0.0,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
        }
        if schema is not None:
            schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False)
            payload["response_format"] = {"type": "json_object"}
            payload["messages"] = [
                {
                    "role": "system",
                    "content": (
                        "You are a careful LLM evaluation judge. Return only valid JSON "
                        f"matching this JSON Schema: {schema_json}"
                    ),
                },
                {"role": "user", "content": prompt},
            ]
        return payload


def _response_content(response: httpx.Response) -> str:
    """Extract non-empty text from an OpenAI-compatible response."""
    data = response.json()
    choices = data.get("choices", []) if isinstance(data, dict) else []
    if not choices:
        raise ValueError("DeepEval judge response did not include choices.")
    message = choices[0].get("message", {})
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("DeepEval judge response did not include text content.")
    return content.strip()


def _validated_output(
    content: str,
    schema: type[BaseModel] | None,
) -> str | BaseModel:
    """Return raw text or a DeepEval-supplied validated schema instance."""
    if schema is None:
        return content
    normalized = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.IGNORECASE)
    payload = json.loads(normalized)
    if not isinstance(payload, dict):
        raise ValueError("DeepEval judge structured response must be an object.")
    return schema.model_validate(payload)
