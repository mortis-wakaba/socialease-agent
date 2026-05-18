"""Provider-agnostic LLM client interface."""

from typing import Protocol


class BaseLLMClient(Protocol):
    """Minimal text-generation contract used by agent modules."""

    async def generate_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.2,
    ) -> str:
        """Generate one text response from system and user prompts."""
        ...
