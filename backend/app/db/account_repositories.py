"""Account repository adapters for pilot authentication."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app.db.config import database_settings
from app.db.engine import connect
from app.db.session import initialize_database


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

    def create_user(
        self,
        *,
        user_id: str,
        email: str,
        password_hash: str,
        now: datetime,
    ) -> None: ...
    def get_by_email(self, email: str) -> AccountRecord | None: ...
    def create_session(
        self,
        *,
        session_id: str,
        user_id: str,
        refresh_token_hash: str,
        access_token_id: str,
        expires_at: datetime,
        now: datetime,
    ) -> None: ...
    def get_session_by_refresh_hash(self, refresh_hash: str) -> SessionRecord | None: ...
    def get_session_id_by_refresh_hash(self, refresh_hash: str) -> str | None: ...
    def get_access_token_session(self, token_id: str) -> AccessTokenSession | None: ...
    def revoke_session(self, session_id: str, now: datetime) -> None: ...
    def revoke_user_sessions(self, user_id: str, now: datetime) -> int: ...
    def delete_user(self, user_id: str) -> bool: ...
    def record_successful_login(self, user_id: str, now: datetime) -> None: ...
    def record_failed_login(self, user_id: str, now: datetime) -> None: ...


class SQLiteAccountRepository:
    """SQLite-backed account repository for local demo and tests."""

    def __init__(self) -> None:
        initialize_database()

    def create_user(
        self,
        *,
        user_id: str,
        email: str,
        password_hash: str,
        now: datetime,
    ) -> None:
        with connect() as connection:
            connection.execute(
                """INSERT INTO users (user_id, email, password_hash, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)""",
                (user_id, email, password_hash, now.isoformat(), now.isoformat()),
            )

    def get_by_email(self, email: str) -> AccountRecord | None:
        with connect() as connection:
            row = connection.execute(
                """SELECT user_id, email, password_hash,
                failed_login_count, last_failed_login_at
                FROM users WHERE email = ?""",
                (email,),
            ).fetchone()
        if row is None:
            return None
        return AccountRecord(
            user_id=row["user_id"],
            email=row["email"],
            password_hash=row["password_hash"],
            failed_login_count=int(row["failed_login_count"] or 0),
            last_failed_login_at=row["last_failed_login_at"],
        )

    def create_session(
        self,
        *,
        session_id: str,
        user_id: str,
        refresh_token_hash: str,
        access_token_id: str,
        expires_at: datetime,
        now: datetime,
    ) -> None:
        with connect() as connection:
            connection.execute(
                """INSERT INTO user_sessions (
                    session_id, user_id, refresh_token_hash, access_token_id,
                    expires_at, revoked_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, NULL, ?, ?)""",
                (
                    session_id,
                    user_id,
                    refresh_token_hash,
                    access_token_id,
                    expires_at.isoformat(),
                    now.isoformat(),
                    now.isoformat(),
                ),
            )

    def get_session_by_refresh_hash(self, refresh_hash: str) -> SessionRecord | None:
        with connect() as connection:
            row = connection.execute(
                """SELECT s.session_id, s.user_id, s.expires_at, s.revoked_at, u.email
                FROM user_sessions s
                JOIN users u ON u.user_id = s.user_id
                WHERE s.refresh_token_hash = ?""",
                (refresh_hash,),
            ).fetchone()
        if row is None:
            return None
        return SessionRecord(
            session_id=row["session_id"],
            user_id=row["user_id"],
            email=row["email"],
            expires_at=row["expires_at"],
            revoked_at=row["revoked_at"],
        )

    def get_session_id_by_refresh_hash(self, refresh_hash: str) -> str | None:
        with connect() as connection:
            row = connection.execute(
                "SELECT session_id FROM user_sessions WHERE refresh_token_hash = ?",
                (refresh_hash,),
            ).fetchone()
        return row["session_id"] if row else None

    def get_access_token_session(self, token_id: str) -> AccessTokenSession | None:
        with connect() as connection:
            row = connection.execute(
                "SELECT expires_at, revoked_at FROM user_sessions WHERE access_token_id = ?",
                (token_id,),
            ).fetchone()
        if row is None:
            return None
        return AccessTokenSession(expires_at=row["expires_at"], revoked_at=row["revoked_at"])

    def revoke_session(self, session_id: str, now: datetime) -> None:
        with connect() as connection:
            connection.execute(
                "UPDATE user_sessions SET revoked_at = ?, updated_at = ? WHERE session_id = ?",
                (now.isoformat(), now.isoformat(), session_id),
            )

    def revoke_user_sessions(self, user_id: str, now: datetime) -> int:
        with connect() as connection:
            cursor = connection.execute(
                """UPDATE user_sessions
                SET revoked_at = ?, updated_at = ?
                WHERE user_id = ? AND revoked_at IS NULL""",
                (now.isoformat(), now.isoformat(), user_id),
            )
            return cursor.rowcount

    def delete_user(self, user_id: str) -> bool:
        with connect() as connection:
            connection.execute(
                "DELETE FROM user_sessions WHERE user_id = ?",
                (user_id,),
            )
            cursor = connection.execute(
                "DELETE FROM users WHERE user_id = ?",
                (user_id,),
            )
            return cursor.rowcount > 0

    def record_successful_login(self, user_id: str, now: datetime) -> None:
        value = now.isoformat()
        with connect() as connection:
            connection.execute(
                """UPDATE users
                SET last_login_at = ?, failed_login_count = 0, updated_at = ?
                WHERE user_id = ?""",
                (value, value, user_id),
            )

    def record_failed_login(self, user_id: str, now: datetime) -> None:
        value = now.isoformat()
        with connect() as connection:
            connection.execute(
                """UPDATE users
                SET last_failed_login_at = ?,
                    failed_login_count = failed_login_count + 1,
                    updated_at = ?
                WHERE user_id = ?""",
                (value, value, user_id),
            )


class PostgresAccountRepository:
    """PostgreSQL-backed account repository for production auth mode."""

    def __init__(self, database_url: str | None = None, engine: Engine | None = None) -> None:
        self.engine = engine or create_engine(
            database_url or database_settings().database_url,
            pool_pre_ping=True,
        )

    def create_user(
        self,
        *,
        user_id: str,
        email: str,
        password_hash: str,
        now: datetime,
    ) -> None:
        with self.engine.begin() as connection:
            connection.execute(
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

    def get_by_email(self, email: str) -> AccountRecord | None:
        with self.engine.connect() as connection:
            row = connection.execute(
                    text(
                        """SELECT user_id, email, password_hash,
                        failed_login_count, last_failed_login_at
                        FROM users WHERE email = :email"""
                    ),
                {"email": email},
            ).mappings().first()
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

    def create_session(
        self,
        *,
        session_id: str,
        user_id: str,
        refresh_token_hash: str,
        access_token_id: str,
        expires_at: datetime,
        now: datetime,
    ) -> None:
        with self.engine.begin() as connection:
            connection.execute(
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

    def get_session_by_refresh_hash(self, refresh_hash: str) -> SessionRecord | None:
        with self.engine.connect() as connection:
            row = connection.execute(
                text(
                    """SELECT s.session_id, s.user_id, s.expires_at, s.revoked_at, u.email
                    FROM user_sessions s
                    JOIN users u ON u.user_id = s.user_id
                    WHERE s.refresh_token_hash = :refresh_hash"""
                ),
                {"refresh_hash": refresh_hash},
            ).mappings().first()
        if row is None:
            return None
        return SessionRecord(
            session_id=row["session_id"],
            user_id=row["user_id"],
            email=row["email"],
            expires_at=_datetime_to_string(row["expires_at"]),
            revoked_at=_datetime_to_string(row["revoked_at"]) if row["revoked_at"] else None,
        )

    def get_session_id_by_refresh_hash(self, refresh_hash: str) -> str | None:
        with self.engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT session_id FROM user_sessions WHERE refresh_token_hash = :refresh_hash"
                ),
                {"refresh_hash": refresh_hash},
            ).mappings().first()
        return row["session_id"] if row else None

    def get_access_token_session(self, token_id: str) -> AccessTokenSession | None:
        with self.engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT expires_at, revoked_at FROM user_sessions WHERE access_token_id = :token_id"
                ),
                {"token_id": token_id},
            ).mappings().first()
        if row is None:
            return None
        return AccessTokenSession(
            expires_at=_datetime_to_string(row["expires_at"]),
            revoked_at=_datetime_to_string(row["revoked_at"]) if row["revoked_at"] else None,
        )

    def revoke_session(self, session_id: str, now: datetime) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    """UPDATE user_sessions
                    SET revoked_at = :revoked_at, updated_at = :updated_at
                    WHERE session_id = :session_id"""
                ),
                {"revoked_at": now, "updated_at": now, "session_id": session_id},
            )

    def revoke_user_sessions(self, user_id: str, now: datetime) -> int:
        with self.engine.begin() as connection:
            result = connection.execute(
                text(
                    """UPDATE user_sessions
                    SET revoked_at = :revoked_at, updated_at = :updated_at
                    WHERE user_id = :user_id AND revoked_at IS NULL"""
                ),
                {"revoked_at": now, "updated_at": now, "user_id": user_id},
            )
            return int(result.rowcount or 0)

    def delete_user(self, user_id: str) -> bool:
        with self.engine.begin() as connection:
            connection.execute(
                text("DELETE FROM user_sessions WHERE user_id = :user_id"),
                {"user_id": user_id},
            )
            result = connection.execute(
                text("DELETE FROM users WHERE user_id = :user_id"),
                {"user_id": user_id},
            )
            return bool(result.rowcount)

    def record_successful_login(self, user_id: str, now: datetime) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    """UPDATE users
                    SET last_login_at = :last_login_at,
                        failed_login_count = 0,
                        updated_at = :updated_at
                    WHERE user_id = :user_id"""
                ),
                {"last_login_at": now, "updated_at": now, "user_id": user_id},
            )

    def record_failed_login(self, user_id: str, now: datetime) -> None:
        with self.engine.begin() as connection:
            connection.execute(
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
