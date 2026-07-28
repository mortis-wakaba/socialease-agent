"""Retry and circuit-breaker utilities for LLM provider adapters."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from time import monotonic
from typing import TypeVar

import httpx


T = TypeVar("T")


class ProviderErrorCategory(str, Enum):
    """Stable categories for provider-level failures."""

    TRANSIENT_PROVIDER_ERROR = "TRANSIENT_PROVIDER_ERROR"
    PERMANENT_PROVIDER_ERROR = "PERMANENT_PROVIDER_ERROR"
    CIRCUIT_OPEN = "CIRCUIT_OPEN"


class ProviderError(RuntimeError):
    """Base provider error carrying a stable category."""

    def __init__(self, message: str, category: ProviderErrorCategory) -> None:
        super().__init__(message)
        self.category = category


class TransientProviderError(ProviderError):
    """Retryable provider issue such as timeout, 429, or 5xx."""

    def __init__(self, message: str) -> None:
        super().__init__(message, ProviderErrorCategory.TRANSIENT_PROVIDER_ERROR)


class PermanentProviderError(ProviderError):
    """Non-retryable provider issue such as malformed response or 4xx."""

    def __init__(self, message: str) -> None:
        super().__init__(message, ProviderErrorCategory.PERMANENT_PROVIDER_ERROR)


class ProviderCircuitOpenError(ProviderError):
    """Raised when the provider circuit is open after repeated failures."""

    def __init__(self, message: str) -> None:
        super().__init__(message, ProviderErrorCategory.CIRCUIT_OPEN)


class ProviderConcurrencyLimitError(ProviderError):
    """Raised when the local LLM concurrency guard cannot acquire capacity."""

    def __init__(self, message: str) -> None:
        super().__init__(message, ProviderErrorCategory.TRANSIENT_PROVIDER_ERROR)


@dataclass(frozen=True)
class RetryPolicy:
    """Configurable retry policy for transient provider failures."""

    max_attempts: int = 3
    initial_backoff_seconds: float = 0.25
    backoff_multiplier: float = 2.0

    def delay_for_attempt(self, attempt: int) -> float:
        """Return delay before the next attempt."""
        if attempt <= 1:
            return 0.0
        return self.initial_backoff_seconds * (
            self.backoff_multiplier ** max(0, attempt - 2)
        )


class CircuitBreaker:
    """Small in-process circuit breaker for provider stability."""

    def __init__(
        self,
        *,
        failure_threshold: int = 3,
        recovery_seconds: float = 30.0,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.recovery_seconds = recovery_seconds
        self._failure_count = 0
        self._opened_at: float | None = None

    def before_call(self) -> None:
        """Raise if the circuit is currently open."""
        if self._opened_at is None:
            return
        if monotonic() - self._opened_at >= self.recovery_seconds:
            self._opened_at = None
            self._failure_count = 0
            return
        raise ProviderCircuitOpenError("LLM provider circuit is open.")

    def record_success(self) -> None:
        """Reset breaker state after a successful call."""
        self._failure_count = 0
        self._opened_at = None

    def record_failure(self) -> None:
        """Track a failed provider call and open circuit when threshold is reached."""
        self._failure_count += 1
        if self._failure_count >= self.failure_threshold:
            self._opened_at = monotonic()


class ProviderConcurrencyLimiter:
    """Async semaphore wrapper for local LLM provider concurrency limits."""

    def __init__(
        self,
        *,
        max_concurrency: int,
        acquire_timeout_seconds: float = 0.25,
    ) -> None:
        self.max_concurrency = max(0, max_concurrency)
        self.acquire_timeout_seconds = max(0.0, acquire_timeout_seconds)
        self._semaphore = (
            asyncio.Semaphore(self.max_concurrency)
            if self.max_concurrency > 0
            else None
        )

    async def run(self, operation: Callable[[], Awaitable[T]]) -> T:
        """Run an operation after acquiring provider capacity."""
        if self._semaphore is None:
            return await operation()
        try:
            await asyncio.wait_for(
                self._semaphore.acquire(),
                timeout=self.acquire_timeout_seconds,
            )
        except TimeoutError as exc:
            from app.observability.runtime_events import (
                record_llm_concurrency_saturation,
            )

            await record_llm_concurrency_saturation()
            raise ProviderConcurrencyLimitError(
                "LLM provider concurrency limit reached."
            ) from exc
        try:
            return await operation()
        finally:
            self._semaphore.release()


async def retry_async(
    operation: Callable[[], Awaitable[T]],
    *,
    retry_policy: RetryPolicy,
    circuit_breaker: CircuitBreaker,
) -> T:
    """Run one async provider operation with retry and circuit-breaker protection."""
    circuit_breaker.before_call()
    last_error: TransientProviderError | None = None
    attempts = max(1, retry_policy.max_attempts)

    for attempt in range(1, attempts + 1):
        delay = retry_policy.delay_for_attempt(attempt)
        if delay > 0:
            await asyncio.sleep(delay)
        try:
            result = await operation()
        except TransientProviderError as exc:
            last_error = exc
            if attempt >= attempts:
                circuit_breaker.record_failure()
                raise
            continue
        except ProviderError:
            circuit_breaker.record_failure()
            raise
        except Exception as exc:
            circuit_breaker.record_failure()
            raise PermanentProviderError(str(exc)) from exc
        circuit_breaker.record_success()
        return result

    circuit_breaker.record_failure()
    if last_error is not None:
        raise last_error
    raise TransientProviderError("LLM provider failed after retries.")


def classify_httpx_error(error: Exception) -> ProviderError:
    """Convert httpx exceptions into stable provider error categories."""
    if isinstance(error, httpx.TimeoutException | httpx.TransportError):
        return TransientProviderError(str(error) or "LLM provider transport error.")

    if isinstance(error, httpx.HTTPStatusError):
        status_code = error.response.status_code
        if status_code == 429 or status_code >= 500:
            return TransientProviderError(f"LLM provider transient HTTP {status_code}.")
        return PermanentProviderError(f"LLM provider non-retryable HTTP {status_code}.")

    return PermanentProviderError(str(error) or error.__class__.__name__)
