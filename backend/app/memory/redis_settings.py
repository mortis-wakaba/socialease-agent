"""Shared Redis connection settings for rebuildable task projections."""

from dataclasses import dataclass
import os


@dataclass(frozen=True)
class RedisTaskStateSettings:
    """Minimal connection settings shared by all task-state caches."""

    redis_url: str | None
    socket_timeout_seconds: float


def redis_task_state_settings() -> RedisTaskStateSettings:
    """Load the shared Redis URL and bounded socket timeout."""
    raw_timeout = os.getenv("SOCIALEASE_REDIS_SOCKET_TIMEOUT_SECONDS", "0.5")
    try:
        timeout = float(raw_timeout)
    except ValueError:
        timeout = 0.5
    return RedisTaskStateSettings(
        redis_url=os.getenv("SOCIALEASE_REDIS_URL", "").strip() or None,
        socket_timeout_seconds=min(max(timeout, 0.1), 5.0),
    )
