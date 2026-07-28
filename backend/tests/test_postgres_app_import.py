"""Smoke tests for importing the app under PostgreSQL runtime settings."""

from __future__ import annotations

import os
import subprocess
import sys


def test_app_imports_with_postgres_runtime_configuration() -> None:
    """Postgres runtime should not initialize SQLite globals during import."""
    env = {
        **os.environ,
        "SOCIALEASE_DATABASE_URL": (
            "postgresql+psycopg://socialease:socialease@127.0.0.1:5432/socialease"
        ),
        "SOCIALEASE_AUTH_MODE": "production",
        "SOCIALEASE_AUTH_TOKEN_SECRET": "test-postgres-import-secret-32-bytes",
        "SOCIALEASE_REDIS_URL": "redis://127.0.0.1:6379/0",
        "SOCIALEASE_CALENDAR_MCP_URL": "https://calendar-mcp.invalid/mcp",
    }

    result = subprocess.run(
        [sys.executable, "-c", "import app.main; print('import ok')"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert "import ok" in result.stdout
