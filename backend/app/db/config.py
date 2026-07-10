"""Database configuration for local demo and production targets."""

from pathlib import Path
from urllib.parse import urlparse
import os


DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "socialease.db"


class DatabaseSettings:
    """Resolved database settings for the current process."""

    def __init__(self) -> None:
        legacy_sqlite_path = os.getenv("SOCIALEASE_DB_PATH")
        default_url = f"sqlite:///{legacy_sqlite_path}" if legacy_sqlite_path else f"sqlite:///{DEFAULT_DB_PATH}"
        self.database_url = os.getenv("SOCIALEASE_DATABASE_URL", default_url)
        self.sqlite_timeout_seconds = float(os.getenv("SOCIALEASE_SQLITE_TIMEOUT_SECONDS", "10"))

    @property
    def provider(self) -> str:
        """Return the configured database provider name."""
        return urlparse(self.database_url).scheme or "sqlite"

    @property
    def sqlite_path(self) -> Path:
        """Return the SQLite path for local demo persistence."""
        if self.database_url.startswith("sqlite:///"):
            return Path(self.database_url.removeprefix("sqlite:///"))
        return DEFAULT_DB_PATH


def database_settings() -> DatabaseSettings:
    """Return database settings from environment variables."""
    return DatabaseSettings()
