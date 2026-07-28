"""Integration tests for the PostgreSQL role-play repository."""

from datetime import datetime, timedelta, timezone
import os
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from app.db.postgres.roleplay_repository import PostgresRoleplaySessionRepository
from app.models_knowledge import Citation
from app.models_roleplay import (
    RoleplayGuidance,
    RoleplayMessage,
    RoleplayMessageFeatures,
    RoleplayMessageRole,
    RoleplaySession,
)


TEST_DATABASE_URL = os.getenv("SOCIALEASE_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="SOCIALEASE_TEST_DATABASE_URL is required for PostgreSQL integration tests.",
)


@pytest.fixture(scope="module", autouse=True)
def migrated_database() -> None:
    """Apply Alembic migrations to the configured test database."""
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL or "")
    command.upgrade(config, "head")


@pytest.fixture
def repository() -> PostgresRoleplaySessionRepository:
    """Return a PostgreSQL role-play repository for tests."""
    assert TEST_DATABASE_URL is not None
    return PostgresRoleplaySessionRepository(database_url=TEST_DATABASE_URL)


@pytest.mark.anyio
async def test_postgres_roleplay_save_get_and_owner_scope(
    repository: PostgresRoleplaySessionRepository,
) -> None:
    session = _roleplay_session(user_id=f"pg_roleplay_user_{uuid4().hex}")

    saved = await repository.save(session)
    fetched = await repository.get_for_user(session.session_id, session.user_id)
    wrong_user = await repository.get_for_user(session.session_id, "other_user")

    assert saved.session_id == session.session_id
    assert fetched is not None
    assert fetched.session_id == session.session_id
    assert fetched.user_id == session.user_id
    assert fetched.scenario == "refuse_request"
    assert fetched.retrieved_guidance.citations
    assert fetched.messages[0].role == RoleplayMessageRole.AGENT
    assert wrong_user is None

    async with repository.engine.connect() as connection:
        row = (await connection.execute(
            text(
                """SELECT scenario, difficulty
                FROM roleplay_sessions WHERE session_id = :session_id"""
            ),
            {"session_id": session.session_id},
        )).mappings().first()

    assert row is not None
    assert row["scenario"] == "refuse_request"
    assert row["difficulty"] == 3


@pytest.mark.anyio
async def test_postgres_roleplay_save_upserts_messages_and_features(
    repository: PostgresRoleplaySessionRepository,
) -> None:
    session = _roleplay_session(user_id=f"pg_roleplay_upsert_user_{uuid4().hex}")
    now = datetime.now(timezone.utc)
    updated = session.model_copy(
        update={
            "messages": [
                *session.messages,
                RoleplayMessage(
                    role=RoleplayMessageRole.USER,
                    content="[raw roleplay message minimized by privacy policy]",
                    features=RoleplayMessageFeatures(
                        char_count=42,
                        sentence_count=2,
                        question_count=1,
                        has_reason=True,
                        has_request=True,
                        has_boundary_statement=True,
                        has_empathy_marker=True,
                        sensitive_detected=["email"],
                    ),
                    created_at=now,
                ),
            ],
            "updated_at": now,
        }
    )

    await repository.save(session)
    await repository.save(updated)
    fetched = await repository.get_for_user(session.session_id, session.user_id)

    assert fetched is not None
    assert len(fetched.messages) == 2
    assert fetched.updated_at >= session.updated_at
    user_message = fetched.messages[-1]
    assert user_message.content == "[raw roleplay message minimized by privacy policy]"
    assert user_message.features is not None
    assert user_message.features.char_count == 42
    assert user_message.features.has_boundary_statement is True
    assert user_message.features.sensitive_detected == ["email"]


def _roleplay_session(*, user_id: str) -> RoleplaySession:
    """Build a product-safe demo role-play session."""
    now = datetime.now(timezone.utc)
    return RoleplaySession(
        session_id=f"session_{uuid4().hex}",
        user_id=user_id,
        scenario="refuse_request",
        difficulty=3,
        messages=[
            RoleplayMessage(
                role=RoleplayMessageRole.AGENT,
                content="我们来做一个拒绝请求的 demo 练习。",
                created_at=now - timedelta(seconds=1),
            )
        ],
        retrieved_guidance=RoleplayGuidance(
            query="refuse request practice",
            answer="Use clear, respectful boundaries in this demo practice.",
            citations=[
                Citation(
                    title="Refuse Request Demo",
                    source_name="Synthetic demo knowledge base",
                    source_type="project_authored",
                    snippet="Demo guidance for respectful refusal practice.",
                )
            ],
            unknown=False,
            confidence=0.8,
        ),
        created_at=now - timedelta(seconds=1),
        updated_at=now - timedelta(seconds=1),
    )
