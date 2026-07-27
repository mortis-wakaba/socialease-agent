"""PostgreSQL runtime smoke test for the full FastAPI app path."""

from __future__ import annotations

import os
import json
import subprocess
import sys
import textwrap

import pytest
from alembic import command
from alembic.config import Config


TEST_DATABASE_URL = os.getenv("SOCIALEASE_TEST_DATABASE_URL")
TEST_CONVERSATION_CONTENT_KEY = (
    "c3Nzc3Nzc3Nzc3Nzc3Nzc3Nzc3Nzc3Nzc3Nzc3Nzc3M="
)

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="SOCIALEASE_TEST_DATABASE_URL is required for PostgreSQL runtime smoke.",
)


def test_postgres_runtime_smoke_covers_auth_chat_consent_memory_delete() -> None:
    """Run a real app import and API smoke flow against PostgreSQL."""
    assert TEST_DATABASE_URL is not None
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL)
    command.upgrade(config, "head")

    env = {
        **os.environ,
        "SOCIALEASE_DATABASE_URL": TEST_DATABASE_URL,
        "SOCIALEASE_AUTH_MODE": "production",
        "SOCIALEASE_AUTH_TOKEN_SECRET": "test-postgres-runtime-smoke-secret",
        "SOCIALEASE_CONVERSATION_CONTENT_KEY": TEST_CONVERSATION_CONTENT_KEY,
        "SOCIALEASE_CONVERSATION_CONTENT_KEY_VERSION": "postgres-smoke-v1",
        "SOCIALEASE_ENABLE_SIGNUP": "true",
        "SOCIALEASE_AUTH_COOKIE_ENABLED": "false",
        "SOCIALEASE_AUTH_RATE_LIMIT_PER_MINUTE": "1000",
        "SOCIALEASE_ENFORCE_DIRECT_ACTION_CONSENT": "true",
        # This smoke isolates database persistence; Redis has its own integration target.
        "SOCIALEASE_REQUIRE_REDIS": "false",
        "SOCIALEASE_REDIS_URL": "",
        "LLM_ENABLED": "false",
    }
    result = subprocess.run(
        [sys.executable, "-c", _SMOKE_CODE],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert "postgres runtime smoke ok" in result.stdout


def test_postgres_runtime_state_survives_fresh_application_process() -> None:
    """Persist through one app process and read through a separately imported app."""
    assert TEST_DATABASE_URL is not None
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL)
    command.upgrade(config, "head")
    env = {
        **os.environ,
        "SOCIALEASE_DATABASE_URL": TEST_DATABASE_URL,
        "SOCIALEASE_AUTH_MODE": "production",
        "SOCIALEASE_AUTH_TOKEN_SECRET": "test-postgres-restart-secret",
        "SOCIALEASE_CONVERSATION_CONTENT_KEY": TEST_CONVERSATION_CONTENT_KEY,
        "SOCIALEASE_CONVERSATION_CONTENT_KEY_VERSION": "postgres-smoke-v1",
        "SOCIALEASE_ENABLE_SIGNUP": "true",
        "SOCIALEASE_AUTH_COOKIE_ENABLED": "false",
        "SOCIALEASE_AUTH_RATE_LIMIT_PER_MINUTE": "1000",
        "SOCIALEASE_REQUIRE_REDIS": "false",
        "SOCIALEASE_REDIS_URL": "",
        "LLM_ENABLED": "false",
    }
    writer = subprocess.run(
        [sys.executable, "-c", _RESTART_WRITER_CODE],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert writer.returncode == 0, writer.stderr + writer.stdout
    state = json.loads(writer.stdout.splitlines()[-1])

    reader = subprocess.run(
        [sys.executable, "-c", _RESTART_READER_CODE],
        check=False,
        capture_output=True,
        text=True,
        env={
            **env,
            "SOCIALEASE_RESTART_EMAIL": state["email"],
            "SOCIALEASE_RESTART_PASSWORD": state["password"],
            "SOCIALEASE_RESTART_WORKSHEET_ID": state["worksheet_id"],
        },
    )

    assert reader.returncode == 0, reader.stderr + reader.stdout
    assert "postgres restart persistence ok" in reader.stdout


_SMOKE_CODE = textwrap.dedent(
    r'''
    import anyio
    import httpx
    from uuid import uuid4

    from app.main import app
    async def main():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            email = f"pg_runtime_{uuid4().hex}@example.com"
            password = "correct-horse-password"
            register = await client.post(
                "/api/auth/register",
                json={"email": email, "password": password},
            )
            assert register.status_code == 201, register.text
            payload = register.json()
            user_id = payload["user"]["user_id"]
            token = payload["tokens"]["access_token"]
            headers = {"Authorization": f"Bearer {token}"}

            login = await client.post(
                "/api/auth/login",
                json={"email": email, "password": password},
            )
            assert login.status_code == 200, login.text

            created = await client.post(
                "/api/conversations",
                headers=headers,
                json={
                    "user_id": user_id,
                    "title": "PostgreSQL unified conversation smoke",
                    "history_notice_version": "2026-07-01",
                    "history_notice_acknowledged": True,
                },
            )
            assert created.status_code == 200, created.text
            conversation_id = created.json()["conversation_id"]

            proposed = await client.post(
                f"/api/conversations/{conversation_id}/messages",
                headers=headers,
                json={
                    "user_id": user_id,
                    "message": "我想模拟课堂发言，先从低强度练习开始。",
                    "idempotency_key": f"postgres-smoke-{uuid4().hex}",
                },
            )
            assert proposed.status_code == 200, proposed.text
            proposal = proposed.json()["pending_module_proposal"]
            assert proposal["proposed_module"] == "roleplay"

            accepted = await client.post(
                (
                    f"/api/conversations/{conversation_id}/module-proposals/"
                    f"{proposal['proposal_id']}/accept"
                ),
                headers=headers,
                json={
                    "user_id": user_id,
                    "request_hash": proposal["request_hash"],
                },
            )
            assert accepted.status_code == 200, accepted.text
            stack = accepted.json()["active_module_stack"]
            assert len(stack) == 1
            assert stack[0]["module_type"] == "roleplay"

            detail = await client.get(
                f"/api/conversations/{conversation_id}",
                headers=headers,
                params={"user_id": user_id},
            )
            assert detail.status_code == 200, detail.text
            assert len(detail.json()["events"]["items"]) >= 4

            deleted = await client.delete("/api/auth/account", headers=headers)
            assert deleted.status_code == 200, deleted.text
            assert deleted.json()["deleted"] is True

            profile_after_delete = await client.get(
                f"/api/users/{user_id}/profile",
                headers=headers,
            )
            assert profile_after_delete.status_code == 401, profile_after_delete.text

        print("postgres runtime smoke ok")


    anyio.run(main)
    '''
)


_RESTART_WRITER_CODE = textwrap.dedent(
    r'''
    import anyio
    import httpx
    import json
    from uuid import uuid4

    from app.main import app
    from app.models_worksheet import WorksheetCreateRequest
    from app.services.worksheet_service import worksheet_service


    async def main():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            email = f"pg_restart_{uuid4().hex}@example.com"
            password = "restart-persistence-password"
            register = await client.post(
                "/api/auth/register",
                json={"email": email, "password": password},
            )
            assert register.status_code == 201, register.text
            auth = register.json()
            user_id = auth["user"]["user_id"]
            worksheet = await worksheet_service.create_worksheet(
                WorksheetCreateRequest(
                    user_id=user_id,
                    message="小组讨论前我担心自己说错，想先整理一个小步骤。",
                )
            )
            record = worksheet.worksheet
            assert record is not None
            print(json.dumps({
                "email": email,
                "password": password,
                "worksheet_id": record.worksheet_id,
            }))


    anyio.run(main)
    '''
)


_RESTART_READER_CODE = textwrap.dedent(
    r'''
    import anyio
    import httpx
    import os

    from app.main import app


    async def main():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            login = await client.post(
                "/api/auth/login",
                json={
                    "email": os.environ["SOCIALEASE_RESTART_EMAIL"],
                    "password": os.environ["SOCIALEASE_RESTART_PASSWORD"],
                },
            )
            assert login.status_code == 200, login.text
            headers = {"Authorization": f"Bearer {login.json()['tokens']['access_token']}"}
            worksheet = await client.get(
                f"/api/worksheet/{os.environ['SOCIALEASE_RESTART_WORKSHEET_ID']}",
                headers=headers,
            )
            assert worksheet.status_code == 200, worksheet.text
            assert worksheet.json()["worksheet_id"] == os.environ["SOCIALEASE_RESTART_WORKSHEET_ID"]
            print("postgres restart persistence ok")


    anyio.run(main)
    '''
)
