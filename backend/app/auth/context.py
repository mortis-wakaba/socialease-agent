"""Authenticated user context for product boundary checks."""

from dataclasses import dataclass


@dataclass(frozen=True)
class AuthContext:
    """Resolved identity for the current request.

    Demo mode allows requests without an auth header so existing local demos
    still work. Production mode requires a verified bearer-token identity.
    """

    user_id: str | None
    tenant_id: str | None = None
    roles: tuple[str, ...] = ("user",)
    is_demo_user: bool = True

    @property
    def has_authenticated_user(self) -> bool:
        """Return whether the request carried an explicit identity."""
        return self.user_id is not None
