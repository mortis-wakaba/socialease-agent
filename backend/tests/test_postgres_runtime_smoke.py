"""PostgreSQL runtime smoke test for the full FastAPI app path."""

from __future__ import annotations

import os
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
