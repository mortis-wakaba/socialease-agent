"""Request-scoped context helpers for observability."""

from contextvars import ContextVar, Token


REQUEST_ID_HEADER = "X-Request-Id"

_request_id: ContextVar[str | None] = ContextVar("socialease_request_id", default=None)


def set_request_id(request_id: str | None) -> Token[str | None]:
    """Store the current request id for this async context."""
    return _request_id.set(request_id)


def reset_request_id(token: Token[str | None]) -> None:
    """Restore the previous request id context."""
    _request_id.reset(token)


def get_request_id() -> str | None:
    """Return the current request id, if one exists."""
    return _request_id.get()
