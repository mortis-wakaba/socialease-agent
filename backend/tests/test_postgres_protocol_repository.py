"""Integration tests for the PostgreSQL protocol repository."""

import asyncio
from datetime import datetime, timedelta, timezone
import os
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config

from app.db.postgres.protocol_repository import PostgresProtocolRepository
from app.models_protocols import ProtocolStatus, ProtocolType


TEST_DATABASE_URL = os.getenv("SOCIALEASE_TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(
        not TEST_DATABASE_URL,
        reason=(
            "SOCIALEASE_TEST_DATABASE_URL is required for "
            "PostgreSQL integration tests."
        ),
    ),
    pytest.mark.anyio,
]


@pytest.fixture(scope="module", autouse=True)
def migrated_database() -> None:
    """Apply Alembic migrations to the configured test database."""
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL or "")
    command.upgrade(config, "head")


@pytest.fixture
def repository() -> PostgresProtocolRepository:
    """Return a PostgreSQL protocol repository for tests."""
    assert TEST_DATABASE_URL is not None
    return PostgresProtocolRepository(database_url=TEST_DATABASE_URL)


async def test_postgres_protocol_create_get_and_transition(
    repository: PostgresProtocolRepository,
) -> None:
    user_id = f"pg_protocol_user_{uuid4().hex}"
    protocol = await repository.create(
        user_id=user_id,
        protocol_type=ProtocolType.CONSENT_REQUEST,
        session_id=None,
        harness_action="start_roleplay",
        request_hash="pg-hash",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
        payload={"harness_action": "start_roleplay", "request_hash": "pg-hash"},
    )

    fetched = await repository.get_for_user(protocol.protocol_id, user_id)
    approved = await repository.transition_status(
        protocol_id=protocol.protocol_id,
        user_id=user_id,
        expected_status=ProtocolStatus.PENDING,
        next_status=ProtocolStatus.APPROVED,
    )
    stale_transition = await repository.transition_status(
        protocol_id=protocol.protocol_id,
        user_id=user_id,
        expected_status=ProtocolStatus.PENDING,
        next_status=ProtocolStatus.REJECTED,
    )

    assert fetched is not None
    assert fetched.protocol_id == protocol.protocol_id
    assert approved is not None
    assert approved.status == ProtocolStatus.APPROVED
    assert approved.approved_at is not None
    assert stale_transition is None


async def test_postgres_protocol_consume_is_atomic(
    repository: PostgresProtocolRepository,
) -> None:
    user_id = f"pg_consume_user_{uuid4().hex}"
    protocol = await repository.create(
        user_id=user_id,
        protocol_type=ProtocolType.CONSENT_REQUEST,
        session_id=None,
        harness_action="start_roleplay",
        request_hash="pg-consume-hash",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
        payload={"harness_action": "start_roleplay", "request_hash": "pg-consume-hash"},
    )
    approved = await repository.transition_status(
        protocol_id=protocol.protocol_id,
        user_id=user_id,
        expected_status=ProtocolStatus.PENDING,
        next_status=ProtocolStatus.APPROVED,
    )
    assert approved is not None

    async def consume_once() -> bool:
        consumed = await repository.transition_status(
            protocol_id=protocol.protocol_id,
            user_id=user_id,
            expected_status=ProtocolStatus.APPROVED,
            next_status=ProtocolStatus.CONSUMED,
        )
        return consumed is not None

    results = await asyncio.gather(*(consume_once() for _ in range(8)))

    assert results.count(True) == 1
    assert results.count(False) == 7


async def test_postgres_protocol_expire_pending_before(
    repository: PostgresProtocolRepository,
) -> None:
    user_id = f"pg_expire_user_{uuid4().hex}"
    protocol = await repository.create(
        user_id=user_id,
        protocol_type=ProtocolType.CONSENT_REQUEST,
        session_id=None,
        harness_action="create_exposure_plan",
        request_hash="pg-expire-hash",
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        payload={"harness_action": "create_exposure_plan", "request_hash": "pg-expire-hash"},
    )

    expired_count = await repository.expire_pending_before(datetime.now(timezone.utc))
    fetched = await repository.get_for_user(protocol.protocol_id, user_id)

    assert expired_count >= 1
    assert fetched is not None
    assert fetched.status == ProtocolStatus.EXPIRED
