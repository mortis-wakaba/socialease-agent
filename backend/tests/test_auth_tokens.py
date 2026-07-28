"""Unit tests for strict production authentication tokens."""

import pytest

from app.auth.tokens import (
    AuthTokenError,
    active_auth_signing_key,
    create_auth_token,
    validate_auth_configuration,
    verify_auth_token,
)


def test_access_token_round_trip() -> None:
    secret = "test-secret-with-at-least-32-bytes"

    token = create_auth_token(user_id="user_1", secret=secret)

    assert verify_auth_token(token, secret=secret).user_id == "user_1"


def test_legacy_four_part_token_is_rejected() -> None:
    with pytest.raises(AuthTokenError, match="Invalid token format"):
        verify_auth_token(
            "socialease.v1.payload.signature",
            secret="test-secret-with-at-least-32-bytes",
        )


def test_production_rejects_short_signing_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SOCIALEASE_AUTH_MODE", "production")
    monkeypatch.setenv("SOCIALEASE_AUTH_TOKEN_SECRET", "short")

    with pytest.raises(RuntimeError, match="at least 32 bytes"):
        validate_auth_configuration()


def test_unknown_auth_mode_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SOCIALEASE_AUTH_MODE", "typo")

    with pytest.raises(RuntimeError, match="Unsupported"):
        validate_auth_configuration()


def test_versioned_key_ring_supports_zero_downtime_rotation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old_secret = "old-signing-secret-with-at-least-32-bytes"
    new_secret = "new-signing-secret-with-at-least-32-bytes"
    monkeypatch.setenv(
        "SOCIALEASE_AUTH_TOKEN_KEYS",
        f'{{"old":"{old_secret}","new":"{new_secret}"}}',
    )
    monkeypatch.setenv("SOCIALEASE_AUTH_TOKEN_ACTIVE_KID", "new")

    key_id, secret = active_auth_signing_key()
    old_token = create_auth_token(
        user_id="old_user",
        secret=old_secret,
        key_id="old",
    )
    new_token = create_auth_token(
        user_id="new_user",
        secret=secret,
        key_id=key_id,
    )

    assert verify_auth_token(old_token).user_id == "old_user"
    assert verify_auth_token(new_token).user_id == "new_user"
