"""Heavier load and concurrency regression tests."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import os
from uuid import uuid4

import httpx
import pytest
from alembic import command
from alembic.config import Config

from app.main import app
from app.protocols.service import ProtocolService
from app.safety.actions import HarnessAction
from app.services.retention_service import retention_service


pytestmark = pytest.mark.load


@pytest.fixture
def anyio_backend() -> str:
    """Run async API tests on asyncio only."""
    return "asyncio"


@pytest.fixture
async def client() -> httpx.AsyncClient:
    """Create an async ASGI client for heavier load tests."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as async_client:
        yield async_client


def test_concurrent_protocol_approval_allows_one_terminal_transition() -> None:
    service = ProtocolService()
    user_id = f"load_protocol_user_{uuid4().hex}"
    protocol = service.create_consent_request(
        user_id=user_id,
        harness_action=HarnessAction.START_ROLEPLAY,
        reason="load test consent",
        required_protocol="explicit_consent",
        session_id=None,
        request_hash="load-approval-hash",
    )

    def approve_once():
        return service.respond(
            protocol_id=protocol.protocol_id,
            user_id=user_id,
            approved=True,
        )

    with ThreadPoolExecutor(max_workers=16) as executor:
        responses = list(executor.map(lambda _: approve_once(), range(16)))

    approved = [response for response in responses if response is not None and response.status == "approved"]
    fetched = service.store.get_for_user(protocol.protocol_id, user_id)

    assert approved
    assert fetched is not None
    assert fetched.status == "approved"
    assert service.respond(protocol_id=protocol.protocol_id, user_id=user_id, approved=False).status == "approved"


def test_concurrent_protocol_consume_under_higher_contention() -> None:
    service = ProtocolService()
    user_id = f"load_consume_user_{uuid4().hex}"
    protocol = service.create_consent_request(
        user_id=user_id,
        harness_action=HarnessAction.CREATE_EXPOSURE_PLAN,
        reason="load test consent",
        required_protocol="explicit_consent",
        session_id=None,
        request_hash="load-consume-hash",
    )
    approved = service.respond(
        protocol_id=protocol.protocol_id,
        user_id=user_id,
        approved=True,
    )
    assert approved is not None

    def consume_once() -> bool:
        consumed = service.consume_for_action(
            protocol_id=protocol.protocol_id,
            user_id=user_id,
            harness_action=HarnessAction.CREATE_EXPOSURE_PLAN,
            request_hash="load-consume-hash",
            session_id=None,
        )
        return consumed is not None

    with ThreadPoolExecutor(max_workers=32) as executor:
        results = list(executor.map(lambda _: consume_once(), range(32)))

    assert results.count(True) == 1
    assert results.count(False) == 31


@pytest.mark.anyio
async def test_concurrent_chat_runs_across_many_users(client: httpx.AsyncClient) -> None:
    user_ids = [f"load_chat_user_{uuid4().hex}" for _ in range(50)]
    messages = [
        "我想模拟课堂发言",
        "小组讨论前我有点紧张，想整理表达",
        "我想做一个社交练习计划，目标是问老师问题",
    ]

    async def run_chat(index: int, user_id: str) -> dict:
        response = await client.post(
            "/api/chat",
            headers={"X-Demo-User-Id": user_id},
            json={
                "user_id": "body_user_should_be_ignored",
                "message": messages[index % len(messages)],
                "context": {},
            },
        )
        assert response.status_code == 200
        return response.json()

    payloads = await asyncio.gather(
        *(run_chat(index, user_id) for index, user_id in enumerate(user_ids))
    )

    assert len(payloads) == len(user_ids)
    assert {payload["trace"]["user_id"] for payload in payloads} == set(user_ids)
    assert all(payload["risk_level"] in {"low", "medium"} for payload in payloads)
    assert all(payload["trace"]["request_id"] for payload in payloads)


@pytest.mark.anyio
async def test_memory_export_delete_with_active_sessions_and_cleanup(
    client: httpx.AsyncClient,
) -> None:
    user_id = f"load_memory_user_{uuid4().hex}"
    for difficulty in range(1, 6):
        response = await client.post(
            "/api/roleplay/start",
            json={
                "user_id": user_id,
                "scenario_description": "课堂上轮到我发言时练习清楚表达观点",
                "difficulty": difficulty,
            },
        )
        assert response.status_code == 200
    await client.put(
        f"/api/users/{user_id}/memory/preferences",
        json={
            "consent_to_save_preferences": True,
            "practice_preferences": {
                "preferred_roleplay_difficulty": 3,
                "preferred_feedback_style": "简洁直接",
                "preferred_practice_scenarios": ["classroom_speech"],
            },
        },
    )

    async def export_memory() -> int:
        response = await client.get(f"/api/users/{user_id}/memory/export")
        assert response.status_code == 200
        return len(response.json()["records"]["roleplay_sessions"])

    async def delete_memory() -> int:
        response = await client.delete(f"/api/users/{user_id}/memory")
        assert response.status_code == 200
        return response.json()["deleted_counts"]["roleplay_sessions"]

    export_task = asyncio.create_task(export_memory())
    cleanup_result = retention_service.run_once(
        now=datetime.now(timezone.utc),
        abandoned_plan_minutes=0,
    )
    deleted_count = await delete_memory()
    export_count = await export_task
    export_after_delete = await client.get(f"/api/users/{user_id}/memory/export")
    records = export_after_delete.json()["records"]

    assert export_count >= 0
    assert cleanup_result.expired_protocols >= 0
    assert deleted_count >= 0
    assert all(len(rows) == 0 for rows in records.values())


def test_fresh_postgres_database_can_upgrade_to_head_when_configured() -> None:
    database_url = os.getenv("SOCIALEASE_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("SOCIALEASE_TEST_DATABASE_URL is required for fresh Postgres migration load test.")

    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    command.current(config, check_heads=True)
