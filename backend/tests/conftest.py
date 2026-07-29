"""Repository adapter bindings for database-independent contract tests."""

import os

import pytest

from app.conversation.repository import ConversationRepository
from app.db.postgres.conversation_repository import PostgresConversationRepository
from app.db.postgres.long_term_memory_repository import (
    PostgresLongTermMemoryRepository,
)
from app.memory.long_term_repository import LongTermMemoryRepository


@pytest.fixture
def conversation_repository_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> ConversationRepository:
    """Bind the conversation contract suite to the configured adapter."""
    monkeypatch.setenv("SOCIALEASE_AUTH_MODE", "demo")
    return PostgresConversationRepository(database_url=_test_database_url())


@pytest.fixture
def long_term_memory_repository_contract() -> LongTermMemoryRepository:
    """Bind the long-term-memory contract suite to the configured adapter."""
    return PostgresLongTermMemoryRepository(database_url=_test_database_url())


def _test_database_url() -> str:
    """Return the explicit contract-test database URL."""
    database_url = os.getenv("SOCIALEASE_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("SOCIALEASE_TEST_DATABASE_URL is required for repository contracts.")
    return database_url
