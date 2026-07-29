"""PostgreSQL account and session repository adapter."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.account_repositories import (
    AccessTokenSession,
    AccountRecord,
    SessionRecord,
)
from app.db.config import database_settings
from app.db.postgres.engine import shared_postgres_async_engine


class PostgresAccountRepository:
    """PostgreSQL-backed account repository for production auth mode."""

    def __init__(
        self,
        database_url: str | None = None,
        engine: AsyncEngine | None = None,
    ) -> None:
        self.engine = engine or shared_postgres_async_engine(
            database_url or database_settings().database_url
        )

    async def create_user(
        self,
        *,
        user_id: str,
        email: str,
        password_hash: str,
        now: datetime,
    ) -> None:
        async with self.engine.begin() as connection:
            await connection.execute(
                text(
                    """INSERT INTO users (user_id, email, password_hash, created_at, updated_at)
                    VALUES (:user_id, :email, :password_hash, :created_at, :updated_at)"""
                ),
                {
                    "user_id": user_id,
                    "email": email,
                    "password_hash": password_hash,
                    "created_at": now,
                    "updated_at": now,
                },
            )

    async def get_by_email(self, email: str) -> AccountRecord | None:
        async with self.engine.connect() as connection:
            row = (await connection.execute(
                    text(
                        """SELECT user_id, email, password_hash,
                        failed_login_count, last_failed_login_at
                        FROM users WHERE email = :email"""
                    ),
                {"email": email},
            )).mappings().first()
        if row is None:
            return None
        return AccountRecord(
            user_id=row["user_id"],
            email=row["email"],
            password_hash=row["password_hash"],
            failed_login_count=int(row["failed_login_count"] or 0),
            last_failed_login_at=(
                _datetime_to_string(row["last_failed_login_at"])
                if row["last_failed_login_at"]
                else None
            ),
        )

    async def create_session(
        self,
        *,
        session_id: str,
        user_id: str,
        refresh_token_hash: str,
        access_token_id: str,
        expires_at: datetime,
        now: datetime,
    ) -> None:
        async with self.engine.begin() as connection:
            await connection.execute(
                text(
                    """INSERT INTO user_sessions (
                        session_id, user_id, refresh_token_hash, access_token_id,
                        expires_at, revoked_at, created_at, updated_at
                    ) VALUES (
                        :session_id, :user_id, :refresh_token_hash, :access_token_id,
                        :expires_at, NULL, :created_at, :updated_at
                    )"""
                ),
                {
                    "session_id": session_id,
                    "user_id": user_id,
                    "refresh_token_hash": refresh_token_hash,
                    "access_token_id": access_token_id,
                    "expires_at": expires_at,
                    "created_at": now,
                    "updated_at": now,
                },
            )

    async def get_session_by_refresh_hash(self, refresh_hash: str) -> SessionRecord | None:
        async with self.engine.connect() as connection:
            row = (await connection.execute(
                text(
                    """SELECT s.session_id, s.user_id, s.expires_at, s.revoked_at, u.email
                    FROM user_sessions s
                    JOIN users u ON u.user_id = s.user_id
                    WHERE s.refresh_token_hash = :refresh_hash"""
                ),
                {"refresh_hash": refresh_hash},
            )).mappings().first()
        if row is None:
            return None
        return SessionRecord(
            session_id=row["session_id"],
            user_id=row["user_id"],
            email=row["email"],
            expires_at=_datetime_to_string(row["expires_at"]),
            revoked_at=_datetime_to_string(row["revoked_at"]) if row["revoked_at"] else None,
        )

    async def rotate_session(
        self,
        *,
        refresh_hash: str,
        new_session_id: str,
        new_refresh_token_hash: str,
        new_access_token_id: str,
        new_expires_at: datetime,
        now: datetime,
    ) -> SessionRecord | None:
        """Consume one locked refresh session and insert its replacement."""
        async with self.engine.begin() as connection:
            row = (await connection.execute(
                text(
                    """SELECT s.session_id, s.user_id, s.expires_at, s.revoked_at, u.email
                    FROM user_sessions s
                    JOIN users u ON u.user_id = s.user_id
                    WHERE s.refresh_token_hash = :refresh_hash
                    FOR UPDATE"""
                ),
                {"refresh_hash": refresh_hash},
            )).mappings().first()
            if row is None or row["revoked_at"] is not None:
                return None
            if row["expires_at"] <= now:
                await connection.execute(
                    text(
                        """UPDATE user_sessions
                        SET revoked_at = :now, updated_at = :now
                        WHERE session_id = :session_id"""
                    ),
                    {"now": now, "session_id": row["session_id"]},
                )
                return None
            updated = await connection.execute(
                text(
                    """UPDATE user_sessions
                    SET revoked_at = :now, updated_at = :now
                    WHERE session_id = :session_id AND revoked_at IS NULL"""
                ),
                {"now": now, "session_id": row["session_id"]},
            )
            if updated.rowcount != 1:
                return None
            await connection.execute(
                text(
                    """INSERT INTO user_sessions (
                        session_id, user_id, refresh_token_hash, access_token_id,
                        expires_at, revoked_at, created_at, updated_at
                    ) VALUES (
                        :session_id, :user_id, :refresh_token_hash, :access_token_id,
                        :expires_at, NULL, :now, :now
                    )"""
                ),
                {
                    "session_id": new_session_id,
                    "user_id": row["user_id"],
                    "refresh_token_hash": new_refresh_token_hash,
                    "access_token_id": new_access_token_id,
                    "expires_at": new_expires_at,
                    "now": now,
                },
            )
            return SessionRecord(
                session_id=row["session_id"],
                user_id=row["user_id"],
                email=row["email"],
                expires_at=_datetime_to_string(row["expires_at"]),
                revoked_at=None,
            )

    async def get_session_id_by_refresh_hash(self, refresh_hash: str) -> str | None:
        async with self.engine.connect() as connection:
            row = (await connection.execute(
                text(
                    "SELECT session_id FROM user_sessions WHERE refresh_token_hash = :refresh_hash"
                ),
                {"refresh_hash": refresh_hash},
            )).mappings().first()
        return row["session_id"] if row else None

    async def get_access_token_session(self, token_id: str) -> AccessTokenSession | None:
        async with self.engine.connect() as connection:
            row = (await connection.execute(
                text(
                    "SELECT expires_at, revoked_at FROM user_sessions WHERE access_token_id = :token_id"
                ),
                {"token_id": token_id},
            )).mappings().first()
        if row is None:
            return None
        return AccessTokenSession(
            expires_at=_datetime_to_string(row["expires_at"]),
            revoked_at=_datetime_to_string(row["revoked_at"]) if row["revoked_at"] else None,
        )

    async def revoke_session(self, session_id: str, now: datetime) -> None:
        async with self.engine.begin() as connection:
            await connection.execute(
                text(
                    """UPDATE user_sessions
                    SET revoked_at = :revoked_at, updated_at = :updated_at
                    WHERE session_id = :session_id"""
                ),
                {"revoked_at": now, "updated_at": now, "session_id": session_id},
            )

    async def revoke_user_sessions(self, user_id: str, now: datetime) -> int:
        async with self.engine.begin() as connection:
            result = await connection.execute(
                text(
                    """UPDATE user_sessions
                    SET revoked_at = :revoked_at, updated_at = :updated_at
                    WHERE user_id = :user_id AND revoked_at IS NULL"""
                ),
                {"revoked_at": now, "updated_at": now, "user_id": user_id},
            )
            return int(result.rowcount or 0)

    async def delete_user(self, user_id: str) -> bool:
        async with self.engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM user_sessions WHERE user_id = :user_id"),
                {"user_id": user_id},
            )
            result = await connection.execute(
                text("DELETE FROM users WHERE user_id = :user_id"),
                {"user_id": user_id},
            )
            return bool(result.rowcount)

    async def record_successful_login(self, user_id: str, now: datetime) -> None:
        async with self.engine.begin() as connection:
            await connection.execute(
                text(
                    """UPDATE users
                    SET last_login_at = :last_login_at,
                        failed_login_count = 0,
                        updated_at = :updated_at
                    WHERE user_id = :user_id"""
                ),
                {"last_login_at": now, "updated_at": now, "user_id": user_id},
            )

    async def record_failed_login(self, user_id: str, now: datetime) -> None:
        async with self.engine.begin() as connection:
            await connection.execute(
                text(
                    """UPDATE users
                    SET last_failed_login_at = :last_failed_login_at,
                        failed_login_count = failed_login_count + 1,
                        updated_at = :updated_at
                    WHERE user_id = :user_id"""
                ),
                {"last_failed_login_at": now, "updated_at": now, "user_id": user_id},
            )


def _datetime_to_string(value: object) -> str:
    """Return an ISO timestamp string from DB-driver values."""
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)
