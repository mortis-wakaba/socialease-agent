"""API tests for real-user pilot account authentication."""

import asyncio
import json
import logging
from uuid import uuid4

import httpx
import pytest

from app.auth.rate_limit import reset_auth_rate_limiters_for_tests
from app.auth.tokens import create_auth_token
from app.main import app
from app.observability.request_logging import PROCESS_TIME_HEADER
from app.services.account_service import InvalidCredentialsError, account_service
from tests.postgres_test_support import execute_sql, fetch_one


TEST_AUTH_SECRET = "test-real-account-secret"
ALLOWED_ORIGIN = "http://127.0.0.1:3000"


@pytest.fixture
def anyio_backend() -> str:
    """Run async API tests on asyncio only."""
    return "asyncio"


@pytest.fixture(autouse=True)
def production_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    """Use production auth mode for account API tests."""
    monkeypatch.setenv("SOCIALEASE_AUTH_MODE", "production")
    monkeypatch.setenv("SOCIALEASE_AUTH_TOKEN_SECRET", TEST_AUTH_SECRET)
    monkeypatch.setenv("SOCIALEASE_ENABLE_SIGNUP", "true")
    monkeypatch.setenv("SOCIALEASE_AUTH_RATE_LIMIT_PER_MINUTE", "1000")
    reset_auth_rate_limiters_for_tests()


@pytest.fixture
async def client() -> httpx.AsyncClient:
    """Create an async ASGI client for auth API tests."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as async_client:
        yield async_client


def unique_email(prefix: str) -> str:
    """Return a unique test email address."""
    return f"{prefix}_{uuid4().hex}@example.com"


@pytest.mark.anyio
async def test_register_returns_tokens_and_authenticated_profile(
    client: httpx.AsyncClient,
) -> None:
    email = unique_email("register")

    response = await client.post(
        "/api/auth/register",
        json={"email": email, "password": "correct-horse-password"},
    )

    assert response.status_code == 201
    payload = response.json()
    access_token = payload["tokens"]["access_token"]
    assert payload["user"]["email"] == email
    assert payload["tokens"]["token_type"] == "bearer"
    assert len(access_token.split(".")) == 3

    profile_response = await client.get(
        f"/api/users/{payload['user']['user_id']}/profile",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert profile_response.status_code == 200
    assert profile_response.json()["user_id"] == payload["user"]["user_id"]


@pytest.mark.anyio
async def test_login_can_authenticate_subsequent_request_with_httponly_cookie(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SOCIALEASE_AUTH_COOKIE_ENABLED", "true")
    monkeypatch.setenv("SOCIALEASE_AUTH_COOKIE_SECURE", "false")
    email = unique_email("cookie")
    await client.post(
        "/api/auth/register",
        headers={"Origin": ALLOWED_ORIGIN},
        json={"email": email, "password": "correct-horse-password"},
    )

    login = await client.post(
        "/api/auth/login",
        headers={"Origin": ALLOWED_ORIGIN},
        json={"email": email, "password": "correct-horse-password"},
    )
    user_id = login.json()["user"]["user_id"]
    profile = await client.get(f"/api/users/{user_id}/profile")

    set_cookie = login.headers.get("set-cookie", "")
    assert login.status_code == 200
    assert "socialease_access_token=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert profile.status_code == 200
    assert profile.json()["user_id"] == user_id


@pytest.mark.anyio
async def test_duplicate_register_is_rejected(client: httpx.AsyncClient) -> None:
    email = unique_email("duplicate")
    body = {"email": email, "password": "correct-horse-password"}

    first = await client.post("/api/auth/register", json=body)
    second = await client.post("/api/auth/register", json=body)

    assert first.status_code == 201
    assert second.status_code == 409


@pytest.mark.anyio
async def test_register_returns_403_when_backend_signup_disabled(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SOCIALEASE_ENABLE_SIGNUP", "false")

    response = await client.post(
        "/api/auth/register",
        json={"email": unique_email("closed"), "password": "correct-horse-password"},
    )

    assert response.status_code == 403


@pytest.mark.anyio
async def test_register_defaults_to_closed_in_production(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SOCIALEASE_ENABLE_SIGNUP", raising=False)

    response = await client.post(
        "/api/auth/register",
        json={"email": unique_email("closed_default"), "password": "correct-horse-password"},
    )

    assert response.status_code == 403


@pytest.mark.anyio
async def test_auth_config_matches_backend_signup_policy(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SOCIALEASE_ENABLE_SIGNUP", "false")

    response = await client.get("/api/auth/config")

    assert response.status_code == 200
    payload = response.json()
    assert payload["auth_mode"] == "production"
    assert payload["signup_enabled"] is False


@pytest.mark.anyio
async def test_auth_me_returns_non_sensitive_role_state(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SOCIALEASE_ENABLE_DEVELOPER_ENDPOINTS", "true")
    token = create_auth_token(
        user_id="normal_user",
        secret=TEST_AUTH_SECRET,
        roles=("user",),
    )

    response = await client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["authenticated"] is True
    assert payload["user_id"] == "normal_user"
    assert payload["roles"] == ["user"]
    assert payload["auth_mode"] == "production"
    assert payload["developer_endpoints_enabled"] is True
    assert payload["developer_access"] is False


@pytest.mark.anyio
async def test_auth_me_marks_developer_access_for_developer_role(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SOCIALEASE_ENABLE_DEVELOPER_ENDPOINTS", "true")
    token = create_auth_token(
        user_id="developer_user",
        secret=TEST_AUTH_SECRET,
        roles=("user", "developer"),
    )

    response = await client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["authenticated"] is True
    assert payload["roles"] == ["user", "developer"]
    assert payload["developer_access"] is True


@pytest.mark.anyio
async def test_register_allows_email_allowlist_when_signup_disabled(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    email = unique_email("allowlist")
    monkeypatch.setenv("SOCIALEASE_ENABLE_SIGNUP", "false")
    monkeypatch.setenv("SOCIALEASE_SIGNUP_ALLOWED_EMAILS", email)

    response = await client.post(
        "/api/auth/register",
        json={"email": email.upper(), "password": "correct-horse-password"},
    )

    assert response.status_code == 201
    assert response.json()["user"]["email"] == email


@pytest.mark.anyio
async def test_register_allows_invite_code_when_signup_disabled(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SOCIALEASE_ENABLE_SIGNUP", "false")
    monkeypatch.setenv("SOCIALEASE_SIGNUP_INVITE_CODES", "pilot-code-1,pilot-code-2")

    response = await client.post(
        "/api/auth/register",
        json={
            "email": unique_email("invite"),
            "password": "correct-horse-password",
            "invite_code": "pilot-code-2",
        },
    )

    assert response.status_code == 201


@pytest.mark.anyio
async def test_login_rejects_wrong_password(client: httpx.AsyncClient) -> None:
    email = unique_email("login")
    await client.post(
        "/api/auth/register",
        json={"email": email, "password": "correct-horse-password"},
    )

    response = await client.post(
        "/api/auth/login",
        json={"email": email, "password": "wrong-password"},
    )

    assert response.status_code == 401
    row = await fetch_one(
        """SELECT failed_login_count, last_failed_login_at
        FROM users WHERE email = :email""",
        {"email": email},
    )
    assert row is not None
    assert row["failed_login_count"] == 1
    assert row["last_failed_login_at"] is not None


@pytest.mark.anyio
async def test_auth_rate_limit_blocks_repeated_login_attempts(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SOCIALEASE_AUTH_RATE_LIMIT_PER_MINUTE", "1")
    email = unique_email("auth_limit")

    first = await client.post(
        "/api/auth/login",
        json={"email": email, "password": "wrong-password"},
    )
    second = await client.post(
        "/api/auth/login",
        json={"email": email, "password": "wrong-password"},
    )

    assert first.status_code == 401
    assert second.status_code == 429
    assert second.headers["retry-after"]


@pytest.mark.anyio
async def test_auth_rate_limit_normalizes_email_variant(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SOCIALEASE_AUTH_RATE_LIMIT_PER_MINUTE", "1")
    email = unique_email("auth_limit_norm")

    first = await client.post(
        "/api/auth/login",
        json={"email": email.upper(), "password": "wrong-password"},
    )
    second = await client.post(
        "/api/auth/login",
        json={"email": email, "password": "wrong-password"},
    )

    assert first.status_code == 401
    assert second.status_code == 429


@pytest.mark.anyio
async def test_failed_login_threshold_temporarily_locks_account(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SOCIALEASE_AUTH_FAILED_LOGIN_LOCK_THRESHOLD", "2")
    monkeypatch.setenv("SOCIALEASE_AUTH_FAILED_LOGIN_COOLDOWN_SECONDS", "300")
    email = unique_email("lockout")
    await client.post(
        "/api/auth/register",
        json={"email": email, "password": "correct-horse-password"},
    )

    for _ in range(2):
        response = await client.post(
            "/api/auth/login",
            json={"email": email, "password": "wrong-password"},
        )
        assert response.status_code == 401
    locked = await client.post(
        "/api/auth/login",
        json={"email": email, "password": "correct-horse-password"},
    )

    assert locked.status_code == 429
    assert "Too many failed login attempts" in locked.json()["detail"]


@pytest.mark.anyio
async def test_successful_login_updates_audit_fields(client: httpx.AsyncClient) -> None:
    email = unique_email("audit")
    await client.post(
        "/api/auth/register",
        json={"email": email, "password": "correct-horse-password"},
    )
    await client.post(
        "/api/auth/login",
        json={"email": email, "password": "wrong-password"},
    )

    response = await client.post(
        "/api/auth/login",
        json={"email": email, "password": "correct-horse-password"},
    )

    assert response.status_code == 200
    row = await fetch_one(
        """SELECT failed_login_count, last_login_at, last_failed_login_at
        FROM users WHERE email = :email""",
        {"email": email},
    )
    assert row is not None
    assert row["failed_login_count"] == 0
    assert row["last_login_at"] is not None
    assert row["last_failed_login_at"] is not None


@pytest.mark.anyio
async def test_refresh_rotates_token_and_old_refresh_fails(
    client: httpx.AsyncClient,
) -> None:
    email = unique_email("refresh")
    register = await client.post(
        "/api/auth/register",
        json={"email": email, "password": "correct-horse-password"},
    )
    old_refresh = register.json()["tokens"]["refresh_token"]

    refresh = await client.post(
        "/api/auth/refresh",
        json={"refresh_token": old_refresh},
    )
    replay = await client.post(
        "/api/auth/refresh",
        json={"refresh_token": old_refresh},
    )

    assert refresh.status_code == 200
    assert refresh.json()["tokens"]["refresh_token"] != old_refresh
    assert replay.status_code == 401


@pytest.mark.anyio
async def test_concurrent_refresh_claims_old_token_only_once(
    client: httpx.AsyncClient,
) -> None:
    """Two workers cannot both rotate the same refresh token."""
    register = await client.post(
        "/api/auth/register",
        json={
            "email": unique_email("concurrent_refresh"),
            "password": "correct-horse-password",
        },
    )
    old_refresh = register.json()["tokens"]["refresh_token"]
    async def rotate() -> bool:
        try:
            await account_service.refresh(old_refresh)
        except InvalidCredentialsError:
            return False
        return True

    results = await asyncio.gather(rotate(), rotate())

    assert sorted(results) == [False, True]


@pytest.mark.anyio
async def test_refresh_can_use_httponly_cookie_without_body(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SOCIALEASE_AUTH_COOKIE_ENABLED", "true")
    monkeypatch.setenv("SOCIALEASE_AUTH_COOKIE_SECURE", "false")
    email = unique_email("cookie_refresh")
    await client.post(
        "/api/auth/register",
        headers={"Origin": ALLOWED_ORIGIN},
        json={"email": email, "password": "correct-horse-password"},
    )

    refresh = await client.post("/api/auth/refresh")

    assert refresh.status_code == 403

    refresh = await client.post(
        "/api/auth/refresh",
        headers={"Origin": ALLOWED_ORIGIN},
    )

    assert refresh.status_code == 200
    assert refresh.json()["user"]["email"] == email
    assert "socialease_refresh_token=" in refresh.headers.get("set-cookie", "")


@pytest.mark.anyio
async def test_logout_revokes_current_access_token(client: httpx.AsyncClient) -> None:
    email = unique_email("logout")
    register = await client.post(
        "/api/auth/register",
        json={"email": email, "password": "correct-horse-password"},
    )
    payload = register.json()
    access_token = payload["tokens"]["access_token"]
    refresh_token = payload["tokens"]["refresh_token"]
    user_id = payload["user"]["user_id"]

    logout = await client.post(
        "/api/auth/logout",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"refresh_token": refresh_token},
    )
    profile_after_logout = await client.get(
        f"/api/users/{user_id}/profile",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert logout.status_code == 200
    assert logout.json()["revoked"] is True
    assert profile_after_logout.status_code == 401


@pytest.mark.anyio
async def test_logout_can_use_httponly_cookie_without_body(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SOCIALEASE_AUTH_COOKIE_ENABLED", "true")
    monkeypatch.setenv("SOCIALEASE_AUTH_COOKIE_SECURE", "false")
    email = unique_email("cookie_logout")
    register = await client.post(
        "/api/auth/register",
        headers={"Origin": ALLOWED_ORIGIN},
        json={"email": email, "password": "correct-horse-password"},
    )
    user_id = register.json()["user"]["user_id"]

    logout = await client.post(
        "/api/auth/logout",
        headers={"Origin": ALLOWED_ORIGIN},
    )
    profile_after_logout = await client.get(f"/api/users/{user_id}/profile")

    assert logout.status_code == 200
    assert logout.json()["revoked"] is True
    assert profile_after_logout.status_code == 401


@pytest.mark.anyio
async def test_cookie_logout_revokes_refresh_session_when_access_cookie_is_invalid(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SOCIALEASE_AUTH_COOKIE_ENABLED", "true")
    monkeypatch.setenv("SOCIALEASE_AUTH_COOKIE_SECURE", "false")
    email = unique_email("cookie_logout_expired_access")
    register = await client.post(
        "/api/auth/register",
        headers={"Origin": ALLOWED_ORIGIN},
        json={"email": email, "password": "correct-horse-password"},
    )
    user_id = register.json()["user"]["user_id"]
    expired_access = create_auth_token(
        user_id=user_id,
        secret=TEST_AUTH_SECRET,
        ttl_seconds=-1,
    )
    client.cookies.set("socialease_access_token", expired_access)

    logout = await client.post(
        "/api/auth/logout",
        headers={"Origin": ALLOWED_ORIGIN},
    )
    refresh_after_logout = await client.post(
        "/api/auth/refresh",
        headers={"Origin": ALLOWED_ORIGIN},
    )

    assert logout.status_code == 200
    assert logout.json()["revoked"] is True
    assert refresh_after_logout.status_code == 401


@pytest.mark.anyio
async def test_cookie_auth_login_and_register_require_allowed_origin(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SOCIALEASE_AUTH_COOKIE_ENABLED", "true")
    monkeypatch.setenv("SOCIALEASE_AUTH_COOKIE_SECURE", "false")
    email = unique_email("cookie_origin")
    blocked_register = await client.post(
        "/api/auth/register",
        headers={"Origin": "https://evil.example"},
        json={"email": email, "password": "correct-horse-password"},
    )
    allowed_register = await client.post(
        "/api/auth/register",
        headers={"Origin": ALLOWED_ORIGIN},
        json={"email": email, "password": "correct-horse-password"},
    )
    blocked_login = await client.post(
        "/api/auth/login",
        headers={"Origin": "https://evil.example"},
        json={"email": email, "password": "correct-horse-password"},
    )

    assert blocked_register.status_code == 403
    assert allowed_register.status_code == 201
    assert blocked_login.status_code == 403


@pytest.mark.anyio
async def test_delete_account_revokes_session_and_removes_login(
    client: httpx.AsyncClient,
) -> None:
    email = unique_email("delete_account")
    register = await client.post(
        "/api/auth/register",
        json={"email": email, "password": "correct-horse-password"},
    )
    payload = register.json()
    access_token = payload["tokens"]["access_token"]
    user_id = payload["user"]["user_id"]
    headers = {"Authorization": f"Bearer {access_token}"}
    await execute_sql(
        """INSERT INTO conversations
            (conversation_id, user_id, title, status, active_module_depth,
             version, history_notice_version, created_at, updated_at)
            VALUES (:conversation_id, :user_id, :title, :status,
                    :active_module_depth, :version, :history_notice_version,
                    :created_at, :updated_at)""",
        {
            "conversation_id": f"account-delete-{uuid4().hex}",
            "user_id": user_id,
            "title": "Account delete conversation",
            "status": "active",
            "active_module_depth": 0,
            "version": 1,
            "history_notice_version": "test-notice",
            "created_at": "2026-07-27T00:00:00+00:00",
            "updated_at": "2026-07-27T00:00:00+00:00",
        },
    )

    delete_response = await client.delete("/api/auth/account", headers=headers)
    profile_after_delete = await client.get(
        f"/api/users/{user_id}/profile",
        headers=headers,
    )
    login_after_delete = await client.post(
        "/api/auth/login",
        json={"email": email, "password": "correct-horse-password"},
    )

    assert delete_response.status_code == 200
    payload = delete_response.json()
    assert payload["deleted"] is True
    assert payload["revoked_sessions"] >= 1
    assert payload["deleted_memory_counts"]["runs"] == 0
    assert payload["deleted_memory_counts"]["conversations"] == 1
    assert profile_after_delete.status_code == 401
    assert login_after_delete.status_code == 401


@pytest.mark.anyio
async def test_cookie_chat_requires_csrf_or_allowed_origin(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("SOCIALEASE_AUTH_COOKIE_ENABLED", "true")
    monkeypatch.setenv("SOCIALEASE_AUTH_COOKIE_SECURE", "false")
    email = unique_email("cookie_chat")
    register = await client.post(
        "/api/auth/register",
        headers={"Origin": ALLOWED_ORIGIN},
        json={"email": email, "password": "correct-horse-password"},
    )
    user_id = register.json()["user"]["user_id"]
    body = {
        "user_id": user_id,
        "message": "我想练习课堂发言，先写一个开场。",
        "context": {},
    }

    caplog.clear()
    with caplog.at_level(logging.INFO, logger="socialease.request"):
        blocked = await client.post("/api/chat", json=body)
    allowed = await client.post("/api/chat", json=body, headers={"Origin": ALLOWED_ORIGIN})

    assert blocked.status_code == 403
    assert blocked.headers["x-request-id"]
    assert blocked.headers[PROCESS_TIME_HEADER]
    assert blocked.json()["request_id"] == blocked.headers["x-request-id"]
    assert blocked.json()["error_category"] == "CSRF_VALIDATION_FAILED"
    request_logs = [
        json.loads(record.message)
        for record in caplog.records
        if record.name == "socialease.request"
    ]
    assert request_logs
    assert request_logs[-1]["path"] == "/api/chat"
    assert request_logs[-1]["status_code"] == 403
    assert request_logs[-1]["request_id"] == blocked.headers["x-request-id"]
    assert allowed.status_code == 404


@pytest.mark.anyio
async def test_cookie_delete_account_requires_csrf_or_allowed_origin(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SOCIALEASE_AUTH_COOKIE_ENABLED", "true")
    monkeypatch.setenv("SOCIALEASE_AUTH_COOKIE_SECURE", "false")
    email = unique_email("cookie_delete_account")
    await client.post(
        "/api/auth/register",
        headers={"Origin": ALLOWED_ORIGIN},
        json={"email": email, "password": "correct-horse-password"},
    )

    blocked = await client.delete("/api/auth/account")
    allowed = await client.delete(
        "/api/auth/account",
        headers={"Origin": ALLOWED_ORIGIN},
    )

    assert blocked.status_code == 403
    assert allowed.status_code == 200


@pytest.mark.anyio
async def test_production_mode_requires_auth_for_profile(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/api/users/anonymous/profile")

    assert response.status_code == 401


@pytest.mark.anyio
async def test_expired_access_token_is_rejected(client: httpx.AsyncClient) -> None:
    token = create_auth_token(
        user_id="expired_user",
        secret=TEST_AUTH_SECRET,
        ttl_seconds=-1,
    )

    response = await client.get(
        "/api/users/expired_user/profile",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 401
