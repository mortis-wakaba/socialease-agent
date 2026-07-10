"""Database provider resolution for repository factories."""

from enum import StrEnum
from urllib.parse import urlparse


class DatabaseProvider(StrEnum):
    """Supported database provider families."""

    SQLITE = "sqlite"
    POSTGRES = "postgres"


class UnsupportedDatabaseProviderError(ValueError):
    """Raised when a configured database URL uses an unsupported provider."""


def resolve_database_provider(database_url: str) -> DatabaseProvider:
    """Return the repository provider family for a database URL."""
    scheme = urlparse(database_url).scheme
    if scheme in {"", "sqlite", "file"}:
        return DatabaseProvider.SQLITE
    if scheme in {"postgres", "postgresql"} or scheme.startswith("postgresql+"):
        return DatabaseProvider.POSTGRES
    raise UnsupportedDatabaseProviderError(
        f"Unsupported database provider {scheme!r}. "
        "Supported providers are sqlite and postgresql."
    )
