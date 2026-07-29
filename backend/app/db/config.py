"""PostgreSQL-only database configuration."""

from urllib.parse import urlparse
import os


DEFAULT_DATABASE_URL = (
    "postgresql+psycopg://socialease:socialease@127.0.0.1:5432/socialease"
)


class DatabaseSettings:
    """Resolved database settings for the current process."""

    def __init__(self) -> None:
        self.database_url = (
            os.getenv("SOCIALEASE_DATABASE_URL", "").strip()
            or DEFAULT_DATABASE_URL
        )

    @property
    def provider(self) -> str:
        """Return the configured database provider name."""
        return urlparse(self.database_url).scheme or "unsupported"


def database_settings() -> DatabaseSettings:
    """Return database settings from environment variables."""
    return DatabaseSettings()
