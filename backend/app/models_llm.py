"""Shared models for optional LLM execution metadata."""

from pydantic import BaseModel


class LLMUsage(BaseModel):
    """Small response-level record of optional LLM usage."""

    used: bool = False
    fallback_used: bool = False
    error_category: str | None = None
