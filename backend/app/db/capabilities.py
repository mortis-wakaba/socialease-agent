"""Runtime database capability checks for production-shaped persistence paths."""

from dataclasses import dataclass

from app.auth.tokens import auth_mode
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
    "session_review",
    "protocol",
    "intervention_plan",
    "metrics",
    "account",
)
SQLITE_REPOSITORIES = (
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
FULL_RUNTIME_REPOSITORIES = SQLITE_REPOSITORIES


@dataclass(frozen=True)
class DatabaseCapabilityReport:
    """Resolved database support matrix for the current runtime."""

    provider: DatabaseProvider
    database_url: str
    supported_repositories: tuple[str, ...]
    missing_runtime_repositories: tuple[str, ...]
    full_runtime_supported: bool
    notes: str


class DatabaseCapabilityError(RuntimeError):
    """Raised when the configured database cannot run the full application."""


def database_capability_report(database_url: str | None = None) -> DatabaseCapabilityReport:
    """Return the support matrix for one database URL."""
    resolved_url = database_url or database_settings().database_url
    provider = resolve_database_provider(resolved_url)
    if provider == DatabaseProvider.SQLITE:
        return DatabaseCapabilityReport(
            provider=provider,
            database_url=resolved_url,
            supported_repositories=SQLITE_REPOSITORIES,
            missing_runtime_repositories=(),
            full_runtime_supported=True,
            notes="SQLite is the default full demo runtime.",
        )
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
            full_runtime_supported=True,
            notes=(
                "PostgreSQL currently supports conversation, trace, roleplay, "
                "worksheet, exposure, "
                "user_profile, memory_settings, session_review, protocol, "
                "intervention_plan, metrics and account repositories."
            ),
        )
    raise AssertionError(f"Unhandled database provider: {provider!r}")


def validate_runtime_database_support(database_url: str | None = None) -> DatabaseCapabilityReport:
    """Fail early when the configured provider cannot run the full API runtime."""
    report = database_capability_report(database_url)
    if report.provider == DatabaseProvider.SQLITE and auth_mode() == "production":
        raise DatabaseCapabilityError(
            "Production requires PostgreSQL; SQLite is limited to local demo and tests."
        )
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
