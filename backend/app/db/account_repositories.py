"""Database-independent account and session repository contracts."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class AccountRecord:
    """Internal account row."""

    user_id: str
    email: str
    password_hash: str
    failed_login_count: int = 0
    last_failed_login_at: str | None = None


@dataclass(frozen=True)
class SessionRecord:
    """Internal session row joined with account identity."""

    session_id: str
    user_id: str
    email: str
    expires_at: str
    revoked_at: str | None


@dataclass(frozen=True)
class AccessTokenSession:
    """Internal access-token session state."""

    expires_at: str
    revoked_at: str | None


class AccountRepository(Protocol):
    """Persistence contract for account and session records."""

    async def create_user(
        self,
        *,
        user_id: str,
        email: str,
        password_hash: str,
        now: datetime,
    ) -> None: ...

    async def get_by_email(self, email: str) -> AccountRecord | None: ...

    async def create_session(
        self,
        *,
        session_id: str,
        user_id: str,
        refresh_token_hash: str,
        access_token_id: str,
        expires_at: datetime,
        now: datetime,
    ) -> None: ...

    async def get_session_by_refresh_hash(
        self,
        refresh_hash: str,
    ) -> SessionRecord | None: ...

    async def rotate_session(
        self,
        *,
        refresh_hash: str,
        new_session_id: str,
        new_refresh_token_hash: str,
        new_access_token_id: str,
        new_expires_at: datetime,
        now: datetime,
    ) -> SessionRecord | None: ...

    async def get_session_id_by_refresh_hash(
        self,
        refresh_hash: str,
    ) -> str | None: ...

    async def get_access_token_session(
        self,
        token_id: str,
    ) -> AccessTokenSession | None: ...

    async def revoke_session(self, session_id: str, now: datetime) -> None: ...

    async def revoke_user_sessions(self, user_id: str, now: datetime) -> int: ...

    async def delete_user(self, user_id: str) -> bool: ...

    async def record_successful_login(
        self,
        user_id: str,
        now: datetime,
    ) -> None: ...

    async def record_failed_login(
        self,
        user_id: str,
        now: datetime,
    ) -> None: ...
