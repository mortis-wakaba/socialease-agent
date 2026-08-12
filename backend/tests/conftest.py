"""Shared backend test fixtures for isolated unit and integration tests."""

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from app.conversation.repository import ConversationRepository
from app.db.migration_check import run_database_upgrade
from app.db.postgres.conversation_repository import PostgresConversationRepository
from app.db.postgres.long_term_memory_repository import (
    PostgresLongTermMemoryRepository,
)
from app.memory.long_term_repository import LongTermMemoryRepository
from app.memory.intervention_plan_store import InMemoryInterventionPlanRepository
from app.memory.settings_store import InMemoryUserMemorySettingsRepository
from app.models_memory import UserPracticeSummary
from app.privacy.persistence_gate import PersistenceGate
from app.protocols.service import ProtocolService
from app.protocols.store import InMemoryProtocolRepository
import app.services.intervention_plan_service as intervention_plan_service_module
from app.services.intervention_plan_service import InterventionPlanService
from app.workflow import engine as workflow_engine
from app.workflow.engine import AgentHarness


class _EmptyUserProfileRepository:
    """Return an empty profile for database-independent Harness tests."""

    async def get_summary(self, user_id: str) -> UserPracticeSummary:
        del user_id
        return UserPracticeSummary()


@pytest.fixture
def anyio_backend() -> str:
    """Run the backend async test suite on its supported asyncio backend."""
    return "asyncio"


@pytest.fixture(scope="session")
def test_database_url() -> str:
    """Return the explicitly configured PostgreSQL integration-test URL."""
    database_url = os.getenv("SOCIALEASE_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip(
            "SOCIALEASE_TEST_DATABASE_URL is required for PostgreSQL integration tests."
        )
    return database_url


@pytest.fixture(scope="session")
def migrated_database(test_database_url: str) -> str:
    """Upgrade the shared integration-test database once per pytest session."""
    backend_root = Path(__file__).resolve().parents[1]
    run_database_upgrade(backend_root, test_database_url)
    return test_database_url


@pytest.fixture
def isolated_protocol_service(
    monkeypatch: pytest.MonkeyPatch,
) -> ProtocolService:
    """Install an in-memory consent protocol service for Harness tests."""
    service = ProtocolService(store=InMemoryProtocolRepository())
    monkeypatch.setattr(workflow_engine, "protocol_service", service)
    return service


@pytest.fixture
def isolated_harness_factory(
    monkeypatch: pytest.MonkeyPatch,
    isolated_protocol_service: ProtocolService,
) -> Callable[..., AgentHarness]:
    """Build a Harness whose default persistence never reaches PostgreSQL."""
    del isolated_protocol_service
    settings = InMemoryUserMemorySettingsRepository()
    intervention_plans = InterventionPlanService(
        store=InMemoryInterventionPlanRepository()
    )
    monkeypatch.setattr(
        workflow_engine,
        "persistence_gate",
        PersistenceGate(repository=settings),
    )
    monkeypatch.setattr(
        workflow_engine,
        "intervention_plan_service",
        intervention_plans,
    )
    monkeypatch.setattr(
        intervention_plan_service_module,
        "intervention_plan_service",
        intervention_plans,
    )

    def build(**kwargs: Any) -> AgentHarness:
        kwargs.setdefault("user_profile_repository", _EmptyUserProfileRepository())
        kwargs.setdefault("memory_settings_repository", settings)
        return AgentHarness(**kwargs)

    return build


@pytest.fixture
def conversation_repository_contract(
    monkeypatch: pytest.MonkeyPatch,
    migrated_database: str,
) -> ConversationRepository:
    """Bind the conversation contract suite to the configured adapter."""
    monkeypatch.setenv("SOCIALEASE_AUTH_MODE", "demo")
    return PostgresConversationRepository(database_url=migrated_database)


@pytest.fixture
def long_term_memory_repository_contract(
    migrated_database: str,
) -> LongTermMemoryRepository:
    """Bind the long-term-memory contract suite to the configured adapter."""
    return PostgresLongTermMemoryRepository(database_url=migrated_database)
