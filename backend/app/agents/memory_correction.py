"""Strict LLM adapter for correction text without record-selection authority."""

from __future__ import annotations

import json
import re

from pydantic import ValidationError

from app.llm.base import BaseLLMClient, JSONModeLLMClient
from app.llm.prompts import (
    build_memory_correction_system_prompt,
    build_memory_correction_user_prompt,
)
from app.llm.retry import ProviderError
from app.models_memory_correction import MemoryCorrectionExtraction


class MemoryCorrectionExtractionError(RuntimeError):
    """Content-free extraction failure safe for API diagnostics."""

    def __init__(self, error_category: str) -> None:
        super().__init__(error_category)
        self.error_category = error_category


class MemoryCorrectionExtractor:
    """Extract target text and replacement text while rejecting authority fields."""

    _EXPECTED_KEYS = {"action", "target_query", "replacement_summary"}

    def __init__(self, llm_client: BaseLLMClient | None) -> None:
        self._llm_client = llm_client

    @property
    def enabled(self) -> bool:
        """Return whether correction extraction has a configured provider."""
        return self._llm_client is not None

    async def extract(self, statement: str) -> MemoryCorrectionExtraction:
        """Return a bounded proposal with no owner, record ID, or lifecycle field."""
        client = self._llm_client
        if client is None:
            raise MemoryCorrectionExtractionError("LLM_DISABLED")
        try:
            kwargs = {
                "system_prompt": build_memory_correction_system_prompt(),
                "user_prompt": build_memory_correction_user_prompt(statement),
                "temperature": 0.0,
            }
            raw = (
                await client.generate_json_text(**kwargs)
                if isinstance(client, JSONModeLLMClient)
                and client.json_mode_enabled
                else await client.generate_text(**kwargs)
            )
            payload = _json_object(raw)
            if set(payload) != self._EXPECTED_KEYS:
                raise ValueError("memory correction fields do not match exact schema")
            return MemoryCorrectionExtraction.model_validate(payload)
        except (json.JSONDecodeError, ValidationError, ValueError) as error:
            raise MemoryCorrectionExtractionError(
                "SCHEMA_VALIDATION_ERROR"
            ) from error
        except MemoryCorrectionExtractionError:
            raise
        except Exception as error:
            category = (
                error.category.value
                if isinstance(error, ProviderError)
                else "TRANSIENT_PROVIDER_ERROR"
            )
            raise MemoryCorrectionExtractionError(category) from error


def _json_object(raw: str) -> dict[str, object]:
    normalized = raw.strip()
    fenced = re.fullmatch(
        r"```(?:json)?\s*(?P<payload>.*?)\s*```",
        normalized,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if fenced is not None:
        normalized = fenced.group("payload")
    payload = json.loads(normalized)
    if not isinstance(payload, dict):
        raise ValueError("memory correction output must be an object")
    return payload
