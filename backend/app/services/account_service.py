"""Account registration, login, refresh, and logout services."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.auth.tokens import (
    auth_mode,
    auth_token_secret,
    create_auth_token,
    create_token_id,
)
from app.db.account_repositories import AccountRecord, AccountRepository
from app.db.factory import repository_factory
from app.models_auth import AccountDeleteResponse, AuthResponse, AuthTokenPair, AuthUser
from app.services.memory_privacy_service import memory_privacy_service


ACCESS_TOKEN_TTL_SECONDS = 60 * 60
REFRESH_TOKEN_TTL_DAYS = 14
PASSWORD_ITERATIONS = 210_000


class AccountError(ValueError):
    """Raised for account-service failures that should map to API errors."""


class DuplicateAccountError(AccountError):
    """Raised when registering an existing email."""


class InvalidCredentialsError(AccountError):
    """Raised when login credentials or refresh tokens are invalid."""


class AccountLockedError(AccountError):
    """Raised when an account is temporarily cooled down after failed logins."""


class SignupDisabledError(AccountError):
    """Raised when account registration is disabled for the deployment."""


class AccountService:
    """Account service for pilot authentication."""

    def __init__(self, repository: AccountRepository | None = None) -> None:
        self.repository = repository or repository_factory().account_repository()

    def register(
        self,
        email: str,
        password: str,
        *,
        invite_code: str | None = None,
    ) -> AuthResponse:
        """Create a user account and return an authenticated session."""
        normalized_email = _normalize_email(email)
        if not signup_allowed(normalized_email, invite_code=invite_code):
            raise SignupDisabledError("Public signup is disabled for this deployment.")
        now = _now()
        user_id = f"user_{uuid4().hex}"
        try:
            self.repository.create_user(
                user_id=user_id,
                email=normalized_email,
                password_hash=_hash_password(password),
                now=now,
            )
        except Exception as exc:
            if "UNIQUE" in str(exc).upper():
                raise DuplicateAccountError("Email is already registered.") from exc
            raise
        return self._create_session_response(user_id=user_id, email=normalized_email)

    def login(self, email: str, password: str) -> AuthResponse:
        """Validate credentials and return a new authenticated session."""
        normalized_email = _normalize_email(email)
        account = self._get_by_email(normalized_email)
        if account is not None and _is_temporarily_locked(account):
            from app.observability.runtime_events import record_auth_lockout

            record_auth_lockout()
            raise AccountLockedError("Too many failed login attempts. Please retry later.")
        if account is None or not _verify_password(password, account.password_hash):
            if account is not None:
                self.repository.record_failed_login(account.user_id, _now())
            from app.observability.runtime_events import record_auth_failed_login

            record_auth_failed_login()
            raise InvalidCredentialsError("Invalid email or password.")
        self.repository.record_successful_login(account.user_id, _now())
        return self._create_session_response(user_id=account.user_id, email=account.email)

    def refresh(self, refresh_token: str) -> AuthResponse:
        """Rotate a refresh token and issue a new access token."""
        refresh_hash = _hash_refresh_token(refresh_token)
        now = _now()
        row = self.repository.get_session_by_refresh_hash(refresh_hash)
        if row is None or row.revoked_at is not None:
            raise InvalidCredentialsError("Invalid refresh token.")
        if _parse_datetime(row.expires_at) <= now:
            self.repository.revoke_session(row.session_id, now)
            raise InvalidCredentialsError("Refresh token has expired.")
        self.repository.revoke_session(row.session_id, now)
        return self._create_session_response(user_id=row.user_id, email=row.email)

    def logout(self, refresh_token: str) -> bool:
        """Revoke a refresh-token session."""
        refresh_hash = _hash_refresh_token(refresh_token)
        now = _now()
        session_id = self.repository.get_session_id_by_refresh_hash(refresh_hash)
        if session_id is None:
            return False
        self.repository.revoke_session(session_id, now)
        return True

    def delete_account(self, user_id: str) -> AccountDeleteResponse:
        """Delete one account and its user-owned practice memory records."""
        now = _now()
        deleted_memory = memory_privacy_service.delete(user_id)
        revoked_sessions = self.repository.revoke_user_sessions(user_id, now)
        deleted = self.repository.delete_user(user_id)
        if not deleted:
            raise InvalidCredentialsError("Account not found.")
        return AccountDeleteResponse(
            deleted=True,
            revoked_sessions=revoked_sessions,
            deleted_memory_counts=deleted_memory.deleted_counts,
        )

    def is_access_token_active(self, token_id: str) -> bool:
        """Return whether a revocable access-token id is still active."""
        now = _now()
        row = self.repository.get_access_token_session(token_id)
        if row is None or row.revoked_at is not None:
            return False
        return _parse_datetime(row.expires_at) > now

    def _create_session_response(self, *, user_id: str, email: str) -> AuthResponse:
        """Persist a refresh session and return token pair."""
        secret = auth_token_secret()
        if secret is None:
            raise AccountError("Auth token secret is not configured.")
        now = _now()
        session_id = f"session_{uuid4().hex}"
        refresh_token = secrets.token_urlsafe(48)
        access_token_id = create_token_id()
        expires_at = now + timedelta(days=REFRESH_TOKEN_TTL_DAYS)
        self.repository.create_session(
            session_id=session_id,
            user_id=user_id,
            refresh_token_hash=_hash_refresh_token(refresh_token),
            access_token_id=access_token_id,
            expires_at=expires_at,
            now=now,
        )
        access_token = create_auth_token(
            user_id=user_id,
            secret=secret,
            ttl_seconds=ACCESS_TOKEN_TTL_SECONDS,
            token_id=access_token_id,
        )
        return AuthResponse(
            user=AuthUser(user_id=user_id, email=email),
            tokens=AuthTokenPair(
                access_token=access_token,
                refresh_token=refresh_token,
                expires_in=ACCESS_TOKEN_TTL_SECONDS,
            ),
        )

    def _get_by_email(self, email: str) -> AccountRecord | None:
        return self.repository.get_by_email(email)


def _hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PASSWORD_ITERATIONS,
    )
    return (
        f"pbkdf2_sha256${PASSWORD_ITERATIONS}$"
        f"{salt.hex()}${digest.hex()}"
    )


def _verify_password(password: str, password_hash: str) -> bool:
    try:
        algorithm, iterations, salt_hex, digest_hex = password_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            bytes.fromhex(salt_hex),
            int(iterations),
        )
    except Exception:
        return False
    return hmac.compare_digest(digest.hex(), digest_hex)


def _hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _normalize_email(email: str) -> str:
    return email.strip().casefold()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _is_temporarily_locked(account: AccountRecord) -> bool:
    """Return whether failed login cooldown should block this account."""
    threshold = _failed_login_lock_threshold()
    if threshold <= 0 or account.failed_login_count < threshold:
        return False
    if not account.last_failed_login_at:
        return False
    cooldown = _failed_login_cooldown_seconds()
    if cooldown <= 0:
        return False
    return _now() - _parse_datetime(account.last_failed_login_at) < timedelta(
        seconds=cooldown
    )


def _failed_login_lock_threshold() -> int:
    """Return failed-login threshold before temporary cooldown."""
    try:
        return max(0, int(os.getenv("SOCIALEASE_AUTH_FAILED_LOGIN_LOCK_THRESHOLD", "5")))
    except ValueError:
        return 5


def _failed_login_cooldown_seconds() -> int:
    """Return temporary cooldown duration after failed login threshold."""
    try:
        return max(0, int(os.getenv("SOCIALEASE_AUTH_FAILED_LOGIN_COOLDOWN_SECONDS", "300")))
    except ValueError:
        return 300


def signup_enabled() -> bool:
    """Return whether public account registration is enabled."""
    value = os.getenv("SOCIALEASE_ENABLE_SIGNUP")
    if value is None:
        return auth_mode() != "production"
    return value.strip().lower() in {"1", "true", "yes", "on"}


def signup_allowed(email: str, *, invite_code: str | None = None) -> bool:
    """Return whether one registration request is allowed by deployment policy."""
    if signup_enabled():
        return True
    allowed_emails = _env_csv("SOCIALEASE_SIGNUP_ALLOWED_EMAILS")
    if email in {_normalize_email(value) for value in allowed_emails}:
        return True
    invite_codes = set(_env_csv("SOCIALEASE_SIGNUP_INVITE_CODES"))
    return bool(invite_code and invite_code.strip() in invite_codes)


def _env_csv(name: str) -> list[str]:
    """Parse a comma-separated environment variable into non-empty values."""
    return [
        item.strip()
        for item in os.getenv(name, "").split(",")
        if item.strip()
    ]


account_service = AccountService()
