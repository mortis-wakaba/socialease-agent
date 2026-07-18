"""Environment-backed retention settings for worksheet and search sessions."""

import os


def worksheet_draft_ttl_seconds() -> int:
    """Return the bounded sliding TTL for raw worksheet clarifications."""
    return _bounded_int("WORKSHEET_DRAFT_TTL_SECONDS", 3600, 300, 86400)


def support_search_ttl_seconds() -> int:
    """Return the bounded sliding TTL for support search context."""
    return _bounded_int("SUPPORT_SEARCH_TTL_SECONDS", 1800, 300, 86400)


def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(maximum, value))
