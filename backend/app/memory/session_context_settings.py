"""Environment-backed settings for Redis role-play session context."""

from dataclasses import dataclass
import os


@dataclass(frozen=True)
class RoleplaySessionContextSettings:
    """Resolved bounded context and Redis retention settings."""

    redis_url: str | None
    active_ttl_seconds: int
    paused_ttl_seconds: int
    max_input_tokens: int
    recent_min_messages: int
    recent_target_messages: int
    recent_max_messages: int
    compact_target_tokens: int
    compact_trigger_ratio: float
    redis_socket_timeout_seconds: float
    tokenizer_backend: str = "auto"
    tokenizer_model: str | None = None


def roleplay_session_context_settings() -> RoleplaySessionContextSettings:
    """Load and clamp role-play context settings from the environment."""
    redis_url = os.getenv("SOCIALEASE_REDIS_URL", "").strip() or None
    recent_min = _int_env("ROLEPLAY_RECENT_MIN_MESSAGES", 12, minimum=2, maximum=20)
    recent_max = _int_env("ROLEPLAY_RECENT_MAX_MESSAGES", 20, minimum=recent_min, maximum=40)
    recent_target = _int_env(
        "ROLEPLAY_RECENT_TARGET_MESSAGES",
        16,
        minimum=recent_min,
        maximum=recent_max,
    )
    return RoleplaySessionContextSettings(
        redis_url=redis_url,
        active_ttl_seconds=_int_env(
            "ROLEPLAY_SESSION_CONTEXT_TTL_SECONDS", 3600, minimum=60, maximum=86400
        ),
        paused_ttl_seconds=_int_env(
            "ROLEPLAY_PAUSED_CONTEXT_TTL_SECONDS", 86400, minimum=60, maximum=604800
        ),
        max_input_tokens=_int_env(
            "ROLEPLAY_CONTEXT_MAX_INPUT_TOKENS", 10000, minimum=2000, maximum=100000
        ),
        recent_min_messages=recent_min,
        recent_target_messages=recent_target,
        recent_max_messages=recent_max,
        compact_target_tokens=_int_env(
            "ROLEPLAY_COMPACT_TARGET_TOKENS", 1000, minimum=200, maximum=4000
        ),
        compact_trigger_ratio=_float_env(
            "ROLEPLAY_COMPACT_TRIGGER_RATIO", 0.75, minimum=0.5, maximum=0.95
        ),
        redis_socket_timeout_seconds=_float_env(
            "ROLEPLAY_REDIS_SOCKET_TIMEOUT_SECONDS", 0.5, minimum=0.1, maximum=5.0
        ),
        tokenizer_backend=os.getenv("ROLEPLAY_TOKENIZER_BACKEND", "auto"),
        tokenizer_model=(
            os.getenv("ROLEPLAY_TOKENIZER_MODEL", "").strip()
            or os.getenv("LLM_MODEL", "").strip()
            or None
        ),
    )


def _int_env(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(maximum, value))


def _float_env(
    name: str,
    default: float,
    *,
    minimum: float,
    maximum: float,
) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(maximum, value))
