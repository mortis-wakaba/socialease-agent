"""Environment-backed LLM client construction."""

from dataclasses import dataclass
import os

from app.llm.base import BaseLLMClient
from app.llm.openai_compatible import OpenAICompatibleLLMClient


@dataclass(frozen=True)
class LLMConfig:
    """Runtime configuration for optional LLM support."""

    enabled: bool
    provider: str
    base_url: str | None
    api_key: str | None
    model: str | None
    timeout_seconds: float

    @classmethod
    def from_env(cls) -> "LLMConfig":
        """Load LLM configuration from process environment variables."""
        enabled = os.getenv("LLM_ENABLED", "false").casefold() == "true"
        return cls(
            enabled=enabled,
            provider=os.getenv("LLM_PROVIDER", "openai_compatible"),
            base_url=os.getenv("LLM_BASE_URL"),
            api_key=os.getenv("LLM_API_KEY"),
            model=os.getenv("LLM_MODEL"),
            timeout_seconds=float(os.getenv("LLM_TIMEOUT_SECONDS", "30")),
        )


def create_llm_client(config: LLMConfig | None = None) -> BaseLLMClient | None:
    """Create an optional provider client, or None when LLM support is disabled."""
    resolved = config or LLMConfig.from_env()
    if not resolved.enabled:
        return None
    if resolved.provider != "openai_compatible":
        raise ValueError(f"Unsupported LLM provider: {resolved.provider}")
    if not resolved.base_url or not resolved.api_key or not resolved.model:
        raise ValueError("LLM is enabled but base_url, api_key, or model is missing.")
    return OpenAICompatibleLLMClient(
        base_url=resolved.base_url,
        api_key=resolved.api_key,
        model=resolved.model,
        timeout_seconds=resolved.timeout_seconds,
    )
