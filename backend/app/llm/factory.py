"""Environment-backed LLM client construction."""

from dataclasses import dataclass
import os

import httpx

from app.llm.base import BaseLLMClient
from app.llm.openai_compatible import OpenAICompatibleLLMClient
from app.llm.retry import CircuitBreaker, ProviderConcurrencyLimiter, RetryPolicy


_GLOBAL_CONCURRENCY_LIMITERS: dict[
    tuple[int, float, int, str],
    ProviderConcurrencyLimiter,
] = {}
_GLOBAL_HTTP_CLIENTS: dict[tuple[float, bool], httpx.AsyncClient] = {}


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
    max_queue_size: int = 0

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
            max_queue_size=int(os.getenv("LLM_MAX_QUEUE_SIZE", "0")),
        )

    @classmethod
    def local_from_env(cls) -> "LLMConfig":
        """Load the local lightweight model used for routing and compaction."""
        enabled = os.getenv("LOCAL_LLM_ENABLED", "false").casefold() == "true"
        base_url = os.getenv("LOCAL_LLM_BASE_URL")
        runtime = os.getenv("LOCAL_LLM_RUNTIME", "auto").strip().casefold()
        ollama_runtime = runtime == "ollama" or (
            runtime == "auto" and "11434" in (base_url or "")
        )
        default_concurrency = (
            os.getenv("LOCAL_LLM_OLLAMA_SLOTS", "1")
            if ollama_runtime
            else "4"
        )
        return cls(
            enabled=enabled,
            provider=os.getenv("LOCAL_LLM_PROVIDER", "openai_compatible"),
            base_url=base_url,
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
            max_concurrency=int(
                os.getenv("LOCAL_LLM_MAX_CONCURRENCY", default_concurrency)
            ),
            concurrency_wait_seconds=float(
                os.getenv("LOCAL_LLM_CONCURRENCY_WAIT_SECONDS", "5")
            ),
            max_queue_size=int(os.getenv("LOCAL_LLM_MAX_QUEUE_SIZE", "4")),
        )


def create_llm_client(
    config: LLMConfig | None = None,
    *,
    http_client: httpx.AsyncClient | None = None,
    concurrency_latency_stage: str = "llm_queue_wait.api",
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
        http_client=http_client or _global_http_client(resolved.timeout_seconds),
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
            max_queue_size=resolved.max_queue_size,
            latency_stage=concurrency_latency_stage,
        ),
    )


def create_local_llm_client(
    config: LLMConfig | None = None,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> BaseLLMClient | None:
    """Create the optional local model reserved for bounded lightweight tasks."""
    resolved = config or LLMConfig.local_from_env()
    return create_llm_client(
        resolved,
        http_client=(
            http_client
            if http_client is not None
            else _global_http_client(resolved.timeout_seconds, trust_env=False)
        ),
        concurrency_latency_stage="llm_queue_wait.local",
    )


def _global_concurrency_limiter(
    *,
    max_concurrency: int,
    acquire_timeout_seconds: float,
    max_queue_size: int,
    latency_stage: str,
) -> ProviderConcurrencyLimiter:
    """Return one process-wide limiter per configured capacity."""
    key = (
        max(0, max_concurrency),
        max(0.0, acquire_timeout_seconds),
        max(0, max_queue_size),
        latency_stage,
    )
    limiter = _GLOBAL_CONCURRENCY_LIMITERS.get(key)
    if limiter is None:
        limiter = ProviderConcurrencyLimiter(
            max_concurrency=key[0],
            acquire_timeout_seconds=key[1],
            max_queue_size=key[2],
            latency_stage=key[3],
        )
        _GLOBAL_CONCURRENCY_LIMITERS[key] = limiter
    return limiter


def local_llm_capacity_snapshot() -> dict[str, int | None]:
    """Expose the configured local-provider limiter for readiness diagnostics."""
    config = LLMConfig.local_from_env()
    limiter = _global_concurrency_limiter(
        max_concurrency=config.max_concurrency,
        acquire_timeout_seconds=config.concurrency_wait_seconds,
        max_queue_size=config.max_queue_size,
        latency_stage="llm_queue_wait.local",
    )
    snapshot = limiter.snapshot()
    return {
        "max_concurrency": snapshot.max_concurrency,
        "max_queue_size": snapshot.max_queue_size,
        "active": snapshot.active,
        "waiting": snapshot.waiting,
        "available": snapshot.available,
        "queue_available": snapshot.queue_available,
    }


def _global_http_client(
    timeout_seconds: float,
    *,
    trust_env: bool = True,
) -> httpx.AsyncClient:
    """Reuse provider connections across classifiers and agent turns."""
    timeout = max(0.1, timeout_seconds)
    key = (timeout, trust_env)
    client = _GLOBAL_HTTP_CLIENTS.get(key)
    if client is None or client.is_closed:
        client = httpx.AsyncClient(
            timeout=timeout,
            limits=httpx.Limits(max_connections=32, max_keepalive_connections=16),
            trust_env=trust_env,
        )
        _GLOBAL_HTTP_CLIENTS[key] = client
    return client


async def close_global_llm_clients() -> None:
    """Close process-wide provider pools during application shutdown."""
    clients = tuple(_GLOBAL_HTTP_CLIENTS.values())
    _GLOBAL_HTTP_CLIENTS.clear()
    for client in clients:
        if not client.is_closed:
            await client.aclose()
