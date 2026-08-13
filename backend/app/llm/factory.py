"""Environment-backed LLM client construction."""

from dataclasses import dataclass
import os

import httpx

from app.llm.base import BaseLLMClient
from app.llm.openai_compatible import OpenAICompatibleLLMClient
from app.llm.retry import CircuitBreaker, ProviderConcurrencyLimiter, RetryPolicy


_GLOBAL_CONCURRENCY_LIMITERS: dict[tuple[int, float], ProviderConcurrencyLimiter] = {}


@dataclass(frozen=True)
class LLMConfig:
    """Runtime configuration for optional LLM support."""

    enabled: bool
    provider: str
    base_url: str | None
    api_key: str | None
    model: str | None
    timeout_seconds: float
    json_mode_enabled: bool = False
    reasoning_effort: str | None = None
    retry_max_attempts: int = 3
    retry_initial_backoff_seconds: float = 0.25
    circuit_failure_threshold: int = 3
    circuit_recovery_seconds: float = 30.0
    max_concurrency: int = 0
    concurrency_wait_seconds: float = 0.25

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
            json_mode_enabled=(
                os.getenv("LLM_JSON_MODE_ENABLED", "false").casefold() == "true"
            ),
            reasoning_effort=os.getenv("LLM_REASONING_EFFORT") or None,
            retry_max_attempts=int(os.getenv("LLM_RETRY_MAX_ATTEMPTS", "3")),
            retry_initial_backoff_seconds=float(
                os.getenv("LLM_RETRY_INITIAL_BACKOFF_SECONDS", "0.25")
            ),
            circuit_failure_threshold=int(
                os.getenv("LLM_CIRCUIT_FAILURE_THRESHOLD", "3")
            ),
            circuit_recovery_seconds=float(os.getenv("LLM_CIRCUIT_RECOVERY_SECONDS", "30")),
            max_concurrency=int(
                os.getenv(
                    "LLM_MAX_CONCURRENCY",
                    os.getenv("SOCIALEASE_LLM_MAX_CONCURRENCY", "0"),
                )
            ),
            concurrency_wait_seconds=float(os.getenv("LLM_CONCURRENCY_WAIT_SECONDS", "0.25")),
        )

    @classmethod
    def local_from_env(cls) -> "LLMConfig":
        """Load the local lightweight model used for routing and compaction."""
        enabled = os.getenv("LOCAL_LLM_ENABLED", "false").casefold() == "true"
        return cls(
            enabled=enabled,
            provider=os.getenv("LOCAL_LLM_PROVIDER", "openai_compatible"),
            base_url=os.getenv("LOCAL_LLM_BASE_URL"),
            api_key=os.getenv("LOCAL_LLM_API_KEY"),
            model=os.getenv("LOCAL_LLM_MODEL"),
            timeout_seconds=float(os.getenv("LOCAL_LLM_TIMEOUT_SECONDS", "10")),
            json_mode_enabled=(
                os.getenv("LOCAL_LLM_JSON_MODE_ENABLED", "true").casefold() == "true"
            ),
            reasoning_effort=os.getenv("LOCAL_LLM_REASONING_EFFORT", "none") or None,
            retry_max_attempts=int(os.getenv("LOCAL_LLM_RETRY_MAX_ATTEMPTS", "1")),
            retry_initial_backoff_seconds=float(
                os.getenv("LOCAL_LLM_RETRY_INITIAL_BACKOFF_SECONDS", "0.1")
            ),
            circuit_failure_threshold=int(
                os.getenv("LOCAL_LLM_CIRCUIT_FAILURE_THRESHOLD", "2")
            ),
            circuit_recovery_seconds=float(
                os.getenv("LOCAL_LLM_CIRCUIT_RECOVERY_SECONDS", "15")
            ),
            max_concurrency=int(os.getenv("LOCAL_LLM_MAX_CONCURRENCY", "2")),
            concurrency_wait_seconds=float(
                os.getenv("LOCAL_LLM_CONCURRENCY_WAIT_SECONDS", "0.25")
            ),
        )


def create_llm_client(
    config: LLMConfig | None = None,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> BaseLLMClient | None:
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
        json_mode_enabled=resolved.json_mode_enabled,
        reasoning_effort=resolved.reasoning_effort,
        http_client=http_client,
        retry_policy=RetryPolicy(
            max_attempts=resolved.retry_max_attempts,
            initial_backoff_seconds=resolved.retry_initial_backoff_seconds,
        ),
        circuit_breaker=CircuitBreaker(
            failure_threshold=resolved.circuit_failure_threshold,
            recovery_seconds=resolved.circuit_recovery_seconds,
        ),
        concurrency_limiter=_global_concurrency_limiter(
            max_concurrency=resolved.max_concurrency,
            acquire_timeout_seconds=resolved.concurrency_wait_seconds,
        ),
    )


def create_local_llm_client(
    config: LLMConfig | None = None,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> BaseLLMClient | None:
    """Create the optional local model reserved for bounded lightweight tasks."""
    return create_llm_client(
        config or LLMConfig.local_from_env(),
        http_client=http_client,
    )


def _global_concurrency_limiter(
    *,
    max_concurrency: int,
    acquire_timeout_seconds: float,
) -> ProviderConcurrencyLimiter:
    """Return one process-wide limiter per configured capacity."""
    key = (max(0, max_concurrency), max(0.0, acquire_timeout_seconds))
    limiter = _GLOBAL_CONCURRENCY_LIMITERS.get(key)
    if limiter is None:
        limiter = ProviderConcurrencyLimiter(
            max_concurrency=key[0],
            acquire_timeout_seconds=key[1],
        )
        _GLOBAL_CONCURRENCY_LIMITERS[key] = limiter
    return limiter
