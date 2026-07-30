"""Tests for database repository provider selection."""

import pytest

from app.db.factory import RepositoryFactory, repository_factory
from app.db.postgres.account_repository import PostgresAccountRepository
from app.db.capabilities import (
    database_capability_report,
    validate_runtime_database_support,
)
from app.db.postgres.protocol_repository import PostgresProtocolRepository
from app.db.postgres.intervention_plan_repository import PostgresInterventionPlanRepository
from app.db.postgres.exposure_repository import PostgresExposureRepository
from app.db.postgres.conversation_repository import PostgresConversationRepository
from app.db.postgres.metrics_repository import PostgresMetricsRepository
from app.db.postgres.memory_settings_repository import PostgresUserMemorySettingsRepository
from app.db.postgres.trace_repository import PostgresTraceRepository
from app.db.postgres.user_profile_repository import PostgresUserProfileRepository
from app.db.postgres.worksheet_repository import PostgresWorksheetRepository
from app.db.postgres.roleplay_repository import PostgresRoleplaySessionRepository
from app.db.postgres.session_review_repository import PostgresSessionReviewRepository
from app.db.providers import (
    DatabaseProvider,
    UnsupportedDatabaseProviderError,
    resolve_database_provider,
)
from app.protocols.service import ProtocolService
def test_default_repository_factory_uses_postgres(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SOCIALEASE_DATABASE_URL", raising=False)

    factory = repository_factory()

    assert factory.provider == DatabaseProvider.POSTGRES


def test_explicit_sqlite_url_is_rejected() -> None:
    with pytest.raises(
        UnsupportedDatabaseProviderError,
        match="PostgreSQL is the only supported provider",
    ):
        RepositoryFactory(database_url="sqlite:///demo.db")


@pytest.mark.anyio
async def test_postgres_url_selects_postgres_protocol_repository() -> None:
    factory = RepositoryFactory(
        database_url="postgresql+psycopg://socialease:socialease@127.0.0.1:5432/socialease"
    )
    repository = factory.protocol_repository()

    assert factory.provider == DatabaseProvider.POSTGRES
    assert isinstance(repository, PostgresProtocolRepository)
    await repository.engine.dispose()


@pytest.mark.anyio
async def test_postgres_url_selects_postgres_trace_repository() -> None:
    factory = RepositoryFactory(
        database_url="postgresql+psycopg://socialease:socialease@127.0.0.1:5432/socialease"
    )
    repository = factory.trace_repository()

    assert factory.provider == DatabaseProvider.POSTGRES
    assert isinstance(repository, PostgresTraceRepository)
    await repository.engine.dispose()


@pytest.mark.anyio
async def test_postgres_url_selects_conversation_repository() -> None:
    factory = RepositoryFactory(
        database_url="postgresql+psycopg://socialease:socialease@127.0.0.1:5432/socialease"
    )
    repository = factory.conversation_repository()

    assert isinstance(repository, PostgresConversationRepository)
    await repository.engine.dispose()


@pytest.mark.anyio
async def test_postgres_url_selects_postgres_worksheet_repository() -> None:
    factory = RepositoryFactory(
        database_url="postgresql+psycopg://socialease:socialease@127.0.0.1:5432/socialease"
    )
    repository = factory.worksheet_repository()

    assert factory.provider == DatabaseProvider.POSTGRES
    assert isinstance(repository, PostgresWorksheetRepository)
    await repository.engine.dispose()


@pytest.mark.anyio
async def test_postgres_url_selects_postgres_roleplay_repository() -> None:
    factory = RepositoryFactory(
        database_url="postgresql+psycopg://socialease:socialease@127.0.0.1:5432/socialease"
    )
    repository = factory.roleplay_repository()

    assert factory.provider == DatabaseProvider.POSTGRES
    assert isinstance(repository, PostgresRoleplaySessionRepository)
    await repository.engine.dispose()


@pytest.mark.anyio
async def test_postgres_url_selects_postgres_exposure_repository() -> None:
    factory = RepositoryFactory(
        database_url="postgresql+psycopg://socialease:socialease@127.0.0.1:5432/socialease"
    )
    repository = factory.exposure_repository()

    assert factory.provider == DatabaseProvider.POSTGRES
    assert isinstance(repository, PostgresExposureRepository)
    await repository.engine.dispose()


@pytest.mark.anyio
async def test_postgres_url_selects_postgres_user_profile_repository() -> None:
    factory = RepositoryFactory(
        database_url="postgresql+psycopg://socialease:socialease@127.0.0.1:5432/socialease"
    )
    repository = factory.user_profile_repository()

    assert factory.provider == DatabaseProvider.POSTGRES
    assert isinstance(repository, PostgresUserProfileRepository)
    await repository.engine.dispose()


@pytest.mark.anyio
async def test_postgres_url_selects_postgres_memory_settings_repository() -> None:
    factory = RepositoryFactory(
        database_url="postgresql+psycopg://socialease:socialease@127.0.0.1:5432/socialease"
    )
    repository = factory.user_memory_settings_repository()

    assert factory.provider == DatabaseProvider.POSTGRES
    assert isinstance(repository, PostgresUserMemorySettingsRepository)
    await repository.engine.dispose()


@pytest.mark.anyio
async def test_postgres_memory_repositories_share_one_engine_pool() -> None:
    database_url = (
        "postgresql+psycopg://socialease:socialease@127.0.0.1:5432/"
        "socialease_shared_memory_pool"
    )
    factory = RepositoryFactory(database_url=database_url)
    repositories = [
        factory.long_term_memory_repository(),
        factory.memory_proposal_repository(),
        factory.user_memory_settings_repository(),
        factory.user_profile_repository(),
    ]

    assert len({id(repository.engine) for repository in repositories}) == 1
    await repositories[0].engine.dispose()


@pytest.mark.anyio
async def test_postgres_url_selects_postgres_session_review_repository() -> None:
    factory = RepositoryFactory(
        database_url="postgresql+psycopg://socialease:socialease@127.0.0.1:5432/socialease"
    )
    repository = factory.session_review_repository()

    assert factory.provider == DatabaseProvider.POSTGRES
    assert isinstance(repository, PostgresSessionReviewRepository)
    await repository.engine.dispose()


@pytest.mark.anyio
async def test_postgres_url_selects_postgres_metrics_repository() -> None:
    factory = RepositoryFactory(
        database_url="postgresql+psycopg://socialease:socialease@127.0.0.1:5432/socialease"
    )
    repository = factory.metrics_repository()

    assert factory.provider == DatabaseProvider.POSTGRES
    assert isinstance(repository, PostgresMetricsRepository)
    await repository.engine.dispose()


@pytest.mark.anyio
async def test_postgres_url_selects_postgres_intervention_plan_repository() -> None:
    factory = RepositoryFactory(
        database_url="postgresql+psycopg://socialease:socialease@127.0.0.1:5432/socialease"
    )
    repository = factory.intervention_plan_repository()

    assert factory.provider == DatabaseProvider.POSTGRES
    assert isinstance(repository, PostgresInterventionPlanRepository)
    await repository.engine.dispose()


@pytest.mark.anyio
async def test_postgres_url_selects_postgres_account_repository() -> None:
    factory = RepositoryFactory(
        database_url="postgresql+psycopg://socialease:socialease@127.0.0.1:5432/socialease"
    )
    repository = factory.account_repository()

    assert factory.provider == DatabaseProvider.POSTGRES
    assert isinstance(repository, PostgresAccountRepository)
    await repository.engine.dispose()


def test_sqlite_runtime_is_rejected_in_every_auth_mode() -> None:
    with pytest.raises(UnsupportedDatabaseProviderError):
        validate_runtime_database_support("sqlite:///runtime.db")


def test_postgres_runtime_database_capability_check_passes_when_all_repositories_exist() -> None:
    postgres_url = "postgresql+psycopg://socialease:socialease@127.0.0.1:5432/socialease"
    report = database_capability_report(postgres_url)
    validated = validate_runtime_database_support(postgres_url)

    assert report.provider == DatabaseProvider.POSTGRES
    assert report.full_runtime_supported is True
    assert report.supported_repositories == (
        "conversation",
        "trace",
        "roleplay",
        "worksheet",
        "exposure",
        "user_profile",
        "memory_settings",
        "long_term_memory",
        "memory_proposal",
        "session_review",
        "protocol",
        "intervention_plan",
        "metrics",
        "account",
        "calendar_outbox",
        "conversation_deletion",
        "account_deletion",
    )
    assert report.missing_runtime_repositories == ()
    assert "row_level_locking" in report.capabilities
    assert "multi_instance_runtime" in report.capabilities
    assert "full_text_search" in report.capabilities
    assert report.unavailable_capabilities == ("vector_similarity_search",)
    assert validated.full_runtime_supported is True


def test_unsupported_provider_fails_loudly() -> None:
    with pytest.raises(UnsupportedDatabaseProviderError, match="Unsupported database provider"):
        RepositoryFactory(database_url="mysql://socialease:secret@localhost/socialease")


@pytest.mark.anyio
async def test_protocol_service_uses_repository_factory_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SOCIALEASE_DATABASE_URL", raising=False)

    service = ProtocolService()

    assert isinstance(service.store, PostgresProtocolRepository)
    await service.store.engine.dispose()


@pytest.mark.anyio
async def test_protocol_service_uses_postgres_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "SOCIALEASE_DATABASE_URL",
        "postgresql+psycopg://socialease:socialease@127.0.0.1:5432/socialease",
    )

    service = ProtocolService()

    assert isinstance(service.store, PostgresProtocolRepository)
    await service.store.engine.dispose()


@pytest.mark.parametrize(
    ("database_url", "provider"),
    [
        ("postgres://user:pass@localhost/socialease", DatabaseProvider.POSTGRES),
        ("postgresql://user:pass@localhost/socialease", DatabaseProvider.POSTGRES),
        ("postgresql+psycopg://user:pass@localhost/socialease", DatabaseProvider.POSTGRES),
    ],
)
def test_resolve_database_provider(database_url: str, provider: DatabaseProvider) -> None:
    assert resolve_database_provider(database_url) == provider
