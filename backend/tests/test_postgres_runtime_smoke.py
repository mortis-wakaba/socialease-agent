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

            first_chat = await client.post(
                "/api/chat",
                headers=headers,
                json={
                    "user_id": user_id,
                    "message": "我想模拟课堂发言，先从低强度练习开始。",
                    "context": {},
                },
            )
            assert first_chat.status_code == 200, first_chat.text
            first_payload = first_chat.json()
            assert first_payload["structured_data"]["action"] == "consent_required"
            protocol_id = first_payload["structured_data"]["protocol_id"]

            approved = await client.post(
                f"/api/protocols/{protocol_id}/respond",
                headers=headers,
                json={"user_id": user_id, "approved": True},
            )
            assert approved.status_code == 200, approved.text

            second_chat = await client.post(
                "/api/chat",
                headers=headers,
                json={
                    "user_id": user_id,
                    "message": "我想模拟课堂发言，先从低强度练习开始。",
                    "context": {"protocol_id": protocol_id},
                },
            )
            assert second_chat.status_code == 200, second_chat.text
            second_payload = second_chat.json()
            assert second_payload["structured_data"]["action"] == "roleplay_started"

            export = await client.get(
                f"/api/users/{user_id}/memory/export",
                headers=headers,
            )
            assert export.status_code == 200, export.text
            assert "runs" in export.json()["records"]

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
            headers = {"Authorization": f"Bearer {auth['tokens']['access_token']}"}
            worksheet = await client.post(
                "/api/worksheet/create",
                headers=headers,
                json={
                    "user_id": user_id,
                    "message": "小组讨论前我担心自己说错，想先整理一个小步骤。",
                },
            )
            assert worksheet.status_code == 200, worksheet.text
            record = worksheet.json()["worksheet"]
            assert record is not None
            print(json.dumps({
                "email": email,
                "password": password,
                "worksheet_id": record["worksheet_id"],
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
