"""Retry and circuit-breaker utilities for LLM provider adapters."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import Enum
from time import monotonic
from typing import TypeVar

import httpx

from app.observability.turn_latency import measure_turn_stage


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
class ProviderCapacitySnapshot:
    """Non-sensitive in-process provider capacity counters."""

    max_concurrency: int
    max_queue_size: int
    active: int
    waiting: int

    @property
    def available(self) -> int | None:
        """Return immediately available execution slots, or None when unlimited."""
        if self.max_concurrency <= 0:
            return None
        return max(0, self.max_concurrency - self.active)

    @property
    def queue_available(self) -> int | None:
        """Return remaining bounded queue positions, or None when unbounded."""
        if self.max_queue_size <= 0:
            return None
        return max(0, self.max_queue_size - self.waiting)


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
        max_queue_size: int = 0,
        latency_stage: str = "llm_queue_wait",
    ) -> None:
        self.max_concurrency = max(0, max_concurrency)
        self.acquire_timeout_seconds = max(0.0, acquire_timeout_seconds)
        self.max_queue_size = max(0, max_queue_size)
        self.latency_stage = latency_stage
        self._semaphore = (
            asyncio.Semaphore(self.max_concurrency)
            if self.max_concurrency > 0
            else None
        )
        self._admission_semaphore = (
            asyncio.Semaphore(self.max_concurrency + self.max_queue_size)
            if self.max_concurrency > 0 and self.max_queue_size > 0
            else None
        )
        self._active = 0
        self._waiting = 0

    def snapshot(self) -> ProviderCapacitySnapshot:
        """Return current counters without exposing request or prompt data."""
        return ProviderCapacitySnapshot(
            max_concurrency=self.max_concurrency,
            max_queue_size=self.max_queue_size,
            active=self._active,
            waiting=self._waiting,
        )

    async def run(self, operation: Callable[[], Awaitable[T]]) -> T:
        """Run an operation after acquiring provider capacity."""
        async with self.slot():
            return await operation()

    @asynccontextmanager
    async def slot(self):
        """Hold one provider slot across an incremental streaming response."""
        if self._semaphore is None:
            yield
            return
        admitted = False
        if self._admission_semaphore is not None:
            if self._admission_semaphore.locked():
                await self._record_saturation()
                raise ProviderConcurrencyLimitError(
                    "LLM provider queue capacity reached."
                )
            await self._admission_semaphore.acquire()
            admitted = True
        acquired = False
        waiting = False
        try:
            try:
                if self._semaphore.locked():
                    self._waiting += 1
                    waiting = True
                with measure_turn_stage(self.latency_stage):
                    await asyncio.wait_for(
                        self._semaphore.acquire(),
                        timeout=self.acquire_timeout_seconds,
                    )
                acquired = True
                self._active += 1
                if waiting:
                    self._waiting -= 1
                    waiting = False
            except TimeoutError as exc:
                await self._record_saturation()
                raise ProviderConcurrencyLimitError(
                    "LLM provider queue wait timed out."
                ) from exc
            yield
        finally:
            if waiting:
                self._waiting -= 1
            if acquired:
                self._active -= 1
                self._semaphore.release()
            if admitted and self._admission_semaphore is not None:
                self._admission_semaphore.release()

    @staticmethod
    async def _record_saturation() -> None:
        """Record one rejected or expired provider queue admission."""
        from app.observability.runtime_events import record_llm_concurrency_saturation

        await record_llm_concurrency_saturation()


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
