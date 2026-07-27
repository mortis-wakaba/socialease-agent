"""Tests for database repository provider selection."""

from pathlib import Path

import pytest

from app.db.factory import RepositoryFactory, repository_factory
from app.db.account_repositories import PostgresAccountRepository
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
from app.protocols.store import ProtocolStore


def test_default_repository_factory_uses_sqlite(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("SOCIALEASE_DATABASE_URL", raising=False)
    monkeypatch.setenv("SOCIALEASE_DB_PATH", str(tmp_path / "socialease.db"))

    factory = repository_factory()

    assert factory.provider == DatabaseProvider.SQLITE
    assert isinstance(factory.protocol_repository(), ProtocolStore)


def test_explicit_sqlite_url_selects_sqlite(tmp_path: Path) -> None:
    factory = RepositoryFactory(database_url=f"sqlite:///{tmp_path / 'demo.db'}")

    assert factory.provider == DatabaseProvider.SQLITE
    assert isinstance(factory.protocol_repository(), ProtocolStore)


def test_postgres_url_selects_postgres_protocol_repository() -> None:
    factory = RepositoryFactory(
        database_url="postgresql+psycopg://socialease:socialease@127.0.0.1:5432/socialease"
    )
    repository = factory.protocol_repository()

    assert factory.provider == DatabaseProvider.POSTGRES
    assert isinstance(repository, PostgresProtocolRepository)
    repository.engine.dispose()


def test_postgres_url_selects_postgres_trace_repository() -> None:
    factory = RepositoryFactory(
        database_url="postgresql+psycopg://socialease:socialease@127.0.0.1:5432/socialease"
    )
    repository = factory.trace_repository()

    assert factory.provider == DatabaseProvider.POSTGRES
    assert isinstance(repository, PostgresTraceRepository)
    repository.engine.dispose()


def test_postgres_url_selects_conversation_repository() -> None:
    factory = RepositoryFactory(
        database_url="postgresql+psycopg://socialease:socialease@127.0.0.1:5432/socialease"
    )
    repository = factory.conversation_repository()

    assert isinstance(repository, PostgresConversationRepository)
    repository.engine.dispose()


def test_postgres_url_selects_postgres_worksheet_repository() -> None:
    factory = RepositoryFactory(
        database_url="postgresql+psycopg://socialease:socialease@127.0.0.1:5432/socialease"
    )
    repository = factory.worksheet_repository()

    assert factory.provider == DatabaseProvider.POSTGRES
    assert isinstance(repository, PostgresWorksheetRepository)
    repository.engine.dispose()


def test_postgres_url_selects_postgres_roleplay_repository() -> None:
    factory = RepositoryFactory(
        database_url="postgresql+psycopg://socialease:socialease@127.0.0.1:5432/socialease"
    )
    repository = factory.roleplay_repository()

    assert factory.provider == DatabaseProvider.POSTGRES
    assert isinstance(repository, PostgresRoleplaySessionRepository)
    repository.engine.dispose()


def test_postgres_url_selects_postgres_exposure_repository() -> None:
    factory = RepositoryFactory(
        database_url="postgresql+psycopg://socialease:socialease@127.0.0.1:5432/socialease"
    )
    repository = factory.exposure_repository()

    assert factory.provider == DatabaseProvider.POSTGRES
    assert isinstance(repository, PostgresExposureRepository)
    repository.engine.dispose()


def test_postgres_url_selects_postgres_user_profile_repository() -> None:
    factory = RepositoryFactory(
        database_url="postgresql+psycopg://socialease:socialease@127.0.0.1:5432/socialease"
    )
    repository = factory.user_profile_repository()

    assert factory.provider == DatabaseProvider.POSTGRES
    assert isinstance(repository, PostgresUserProfileRepository)
    repository.engine.dispose()


def test_postgres_url_selects_postgres_memory_settings_repository() -> None:
    factory = RepositoryFactory(
        database_url="postgresql+psycopg://socialease:socialease@127.0.0.1:5432/socialease"
    )
    repository = factory.user_memory_settings_repository()

    assert factory.provider == DatabaseProvider.POSTGRES
    assert isinstance(repository, PostgresUserMemorySettingsRepository)
    repository.engine.dispose()


def test_postgres_memory_repositories_share_one_engine_pool() -> None:
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
    repositories[0].engine.dispose()


def test_postgres_url_selects_postgres_session_review_repository() -> None:
    factory = RepositoryFactory(
        database_url="postgresql+psycopg://socialease:socialease@127.0.0.1:5432/socialease"
    )
    repository = factory.session_review_repository()

    assert factory.provider == DatabaseProvider.POSTGRES
    assert isinstance(repository, PostgresSessionReviewRepository)
    repository.engine.dispose()


def test_postgres_url_selects_postgres_metrics_repository() -> None:
    factory = RepositoryFactory(
        database_url="postgresql+psycopg://socialease:socialease@127.0.0.1:5432/socialease"
    )
    repository = factory.metrics_repository()

    assert factory.provider == DatabaseProvider.POSTGRES
    assert isinstance(repository, PostgresMetricsRepository)
    repository.engine.dispose()


def test_postgres_url_selects_postgres_intervention_plan_repository() -> None:
    factory = RepositoryFactory(
        database_url="postgresql+psycopg://socialease:socialease@127.0.0.1:5432/socialease"
    )
    repository = factory.intervention_plan_repository()

    assert factory.provider == DatabaseProvider.POSTGRES
    assert isinstance(repository, PostgresInterventionPlanRepository)
    repository.engine.dispose()


def test_postgres_url_selects_postgres_account_repository() -> None:
    factory = RepositoryFactory(
        database_url="postgresql+psycopg://socialease:socialease@127.0.0.1:5432/socialease"
    )
    repository = factory.account_repository()

    assert factory.provider == DatabaseProvider.POSTGRES
    assert isinstance(repository, PostgresAccountRepository)
    repository.engine.dispose()


def test_sqlite_runtime_database_capability_check_passes(tmp_path: Path) -> None:
    report = validate_runtime_database_support(f"sqlite:///{tmp_path / 'runtime.db'}")

    assert report.provider == DatabaseProvider.SQLITE
    assert report.full_runtime_supported is True
    assert report.missing_runtime_repositories == ()
    assert "trace" in report.supported_repositories


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
        "session_review",
        "protocol",
        "intervention_plan",
        "metrics",
        "account",
    )
    assert report.missing_runtime_repositories == ()
    assert validated.full_runtime_supported is True


def test_unsupported_provider_fails_loudly() -> None:
    with pytest.raises(UnsupportedDatabaseProviderError, match="Unsupported database provider"):
        RepositoryFactory(database_url="mysql://socialease:secret@localhost/socialease")


def test_protocol_service_uses_repository_factory_by_default(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("SOCIALEASE_DATABASE_URL", raising=False)
    monkeypatch.setenv("SOCIALEASE_DB_PATH", str(tmp_path / "service.db"))

    service = ProtocolService()

    assert isinstance(service.store, ProtocolStore)


def test_protocol_service_uses_postgres_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "SOCIALEASE_DATABASE_URL",
        "postgresql+psycopg://socialease:socialease@127.0.0.1:5432/socialease",
    )

    service = ProtocolService()

    assert isinstance(service.store, PostgresProtocolRepository)
    service.store.engine.dispose()


@pytest.mark.parametrize(
    ("database_url", "provider"),
    [
        ("sqlite:////tmp/socialease.db", DatabaseProvider.SQLITE),
        ("postgres://user:pass@localhost/socialease", DatabaseProvider.POSTGRES),
        ("postgresql://user:pass@localhost/socialease", DatabaseProvider.POSTGRES),
        ("postgresql+psycopg://user:pass@localhost/socialease", DatabaseProvider.POSTGRES),
    ],
)
def test_resolve_database_provider(database_url: str, provider: DatabaseProvider) -> None:
    assert resolve_database_provider(database_url) == provider
