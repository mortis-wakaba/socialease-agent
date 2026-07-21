"""Runtime metric event helpers for non-trace operational signals."""

from functools import lru_cache

from app.observability.metrics import MetricsRepository


RATE_LIMIT_HIT = "rate_limit_hit"
LLM_CONCURRENCY_SATURATION = "llm_concurrency_saturation"
SLOW_REQUEST = "slow_request"
MEMORY_EXPORT = "memory_export"
MEMORY_DELETE = "memory_delete"
MEMORY_PREFERENCES_SAVED = "memory_preferences_saved"
MEMORY_PREFERENCES_DISABLED = "memory_preferences_disabled"
AUTH_RATE_LIMIT_HIT = "auth_rate_limit_hit"
AUTH_FAILED_LOGIN = "auth_failed_login"
AUTH_LOCKOUT = "auth_lockout"
TRACE_PERSISTENCE_FAILURE = "trace_persistence_failure"
OBSERVABILITY_HOOK_FAILURE = "observability_hook_failure"


def record_rate_limit_hit() -> None:
    """Record one API rate-limit rejection."""
    _metrics_repository().record_runtime_event(RATE_LIMIT_HIT)


def record_llm_concurrency_saturation() -> None:
    """Record one LLM provider concurrency saturation event."""
    _metrics_repository().record_runtime_event(LLM_CONCURRENCY_SATURATION)


def record_slow_request() -> None:
    """Record one request slower than the configured threshold."""
    _metrics_repository().record_runtime_event(SLOW_REQUEST)


def record_memory_export() -> None:
    """Record one user-owned memory export."""
    _metrics_repository().record_runtime_event(MEMORY_EXPORT)


def record_memory_delete() -> None:
    """Record one user-owned memory deletion."""
    _metrics_repository().record_runtime_event(MEMORY_DELETE)


def record_memory_preferences_saved() -> None:
    """Record one explicit save of long-term practice preferences."""
    _metrics_repository().record_runtime_event(MEMORY_PREFERENCES_SAVED)


def record_memory_preferences_disabled() -> None:
    """Record one user action disabling long-term practice preferences."""
    _metrics_repository().record_runtime_event(MEMORY_PREFERENCES_DISABLED)


def record_auth_rate_limit_hit() -> None:
    """Record one auth endpoint rate-limit rejection."""
    _metrics_repository().record_runtime_event(AUTH_RATE_LIMIT_HIT)


def record_auth_failed_login() -> None:
    """Record one failed login attempt without sensitive details."""
    _metrics_repository().record_runtime_event(AUTH_FAILED_LOGIN)


def record_auth_lockout() -> None:
    """Record one temporary auth lockout without sensitive details."""
    _metrics_repository().record_runtime_event(AUTH_LOCKOUT)


def record_trace_persistence_failure() -> None:
    """Record one dropped product trace without user-derived content."""
    _metrics_repository().record_runtime_event(TRACE_PERSISTENCE_FAILURE)


def record_observability_hook_failure() -> None:
    """Record one isolated post-trace hook failure without payload content."""
    _metrics_repository().record_runtime_event(OBSERVABILITY_HOOK_FAILURE)


@lru_cache(maxsize=1)
def _metrics_repository() -> MetricsRepository:
    """Return the configured metrics repository lazily to avoid import cycles."""
    from app.db.factory import repository_factory

    return repository_factory().metrics_repository()
