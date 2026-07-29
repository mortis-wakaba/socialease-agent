"""Explicit PostgreSQL-only runtime capability report."""

from dataclasses import dataclass

from app.db.config import database_settings
from app.db.providers import DatabaseProvider, resolve_database_provider


SUPPORTED_POSTGRES_REPOSITORIES = (
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
FULL_RUNTIME_REPOSITORIES = SUPPORTED_POSTGRES_REPOSITORIES
POSTGRES_CAPABILITIES = (
    "async_transactions",
    "row_level_locking",
    "optimistic_versioning",
    "multi_instance_runtime",
    "transactional_outbox",
    "owner_scoped_deletion",
)
POSTGRES_UNAVAILABLE_CAPABILITIES = (
    "vector_similarity_search",
)


@dataclass(frozen=True)
class DatabaseCapabilityReport:
    """Resolved database support matrix for the current runtime."""

    provider: DatabaseProvider
    database_url: str
    supported_repositories: tuple[str, ...]
    missing_runtime_repositories: tuple[str, ...]
    capabilities: tuple[str, ...]
    unavailable_capabilities: tuple[str, ...]
    full_runtime_supported: bool
    notes: str


class DatabaseCapabilityError(RuntimeError):
    """Raised when the configured database cannot run the full application."""


def database_capability_report(database_url: str | None = None) -> DatabaseCapabilityReport:
    """Return the support matrix for one database URL."""
    resolved_url = database_url or database_settings().database_url
    provider = resolve_database_provider(resolved_url)
    if provider == DatabaseProvider.POSTGRES:
        missing = tuple(
            repository
            for repository in FULL_RUNTIME_REPOSITORIES
            if repository not in SUPPORTED_POSTGRES_REPOSITORIES
        )
        return DatabaseCapabilityReport(
            provider=provider,
            database_url=resolved_url,
            supported_repositories=SUPPORTED_POSTGRES_REPOSITORIES,
            missing_runtime_repositories=missing,
            capabilities=POSTGRES_CAPABILITIES,
            unavailable_capabilities=POSTGRES_UNAVAILABLE_CAPABILITIES,
            full_runtime_supported=not missing,
            notes=(
                "PostgreSQL is the only runtime persistence provider. "
                "Vector similarity search is not enabled because the current "
                "large-scale memory eval does not pass its safety gate. "
                "No SQLite or demo persistence fallback is available."
            ),
        )
    raise AssertionError(f"Unhandled database provider: {provider!r}")


def validate_runtime_database_support(database_url: str | None = None) -> DatabaseCapabilityReport:
    """Fail early when the configured provider cannot run the full API runtime."""
    report = database_capability_report(database_url)
    if report.full_runtime_supported:
        return report
    supported = ", ".join(report.supported_repositories)
    missing = ", ".join(report.missing_runtime_repositories)
    raise DatabaseCapabilityError(
        f"{report.provider.value} full runtime is not enabled yet. "
        f"Supported adapters: {supported}. "
        f"Missing full-runtime repositories: {missing}. "
        "Use a provider with full runtime support or add the missing repository adapters."
    )
