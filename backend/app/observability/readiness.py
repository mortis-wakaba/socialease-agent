"""Readiness checks for deployment and pilot operations."""

from __future__ import annotations

import sqlite3
import os
from pathlib import Path

import psycopg

from app.auth.cookies import auth_cookies_enabled
from app.auth.tokens import auth_mode
from app.db.capabilities import database_capability_report
from app.db.config import database_settings
from app.db.migration_check import validate_revision_chain
from app.db.providers import DatabaseProvider, resolve_database_provider
from app.workflow.default_hooks import metrics_hook


def readiness_snapshot() -> tuple[int, dict[str, object]]:
    """Return HTTP status and a non-secret readiness snapshot."""
    checks: dict[str, dict[str, object]] = {
        "database": _database_check(),
        "capabilities": _capability_check(),
        "migrations": _migration_graph_check(),
        "metrics": _metrics_check(),
        "auth": _auth_config_check(),
    }
    ready = all(check["ok"] is True for check in checks.values())
    return (
        200 if ready else 503,
        {
            "status": "ready" if ready else "not_ready",
            "checks": checks,
        },
    )


def _database_check() -> dict[str, object]:
    """Check that the configured database can answer a trivial query."""
    settings = database_settings()
    provider = resolve_database_provider(settings.database_url)
    try:
        if provider == DatabaseProvider.SQLITE:
            settings.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(settings.sqlite_path, timeout=3) as connection:
                connection.execute("SELECT 1").fetchone()
            return {
                "ok": True,
                "provider": provider.value,
                "database": _safe_database_label(settings.database_url),
            }
        if provider == DatabaseProvider.POSTGRES:
            with psycopg.connect(settings.database_url, connect_timeout=3) as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 1")
                    cursor.fetchone()
            return {
                "ok": True,
                "provider": provider.value,
                "database": _safe_database_label(settings.database_url),
            }
    except Exception as exc:  # pragma: no cover - exact driver errors vary
        return {
            "ok": False,
            "provider": provider.value,
            "database": _safe_database_label(settings.database_url),
            "error": exc.__class__.__name__,
        }
    return {
        "ok": False,
        "provider": provider.value,
        "database": _safe_database_label(settings.database_url),
        "error": "unsupported_provider",
    }


def _capability_check() -> dict[str, object]:
    """Check that the configured provider supports all runtime repositories."""
    try:
        report = database_capability_report()
        return {
            "ok": report.full_runtime_supported,
            "provider": report.provider.value,
            "supported_repositories": list(report.supported_repositories),
            "missing_runtime_repositories": list(report.missing_runtime_repositories),
            "notes": report.notes,
        }
    except Exception as exc:  # pragma: no cover - defensive for deployment diagnostics
        return {
            "ok": False,
            "error": exc.__class__.__name__,
        }


def _migration_graph_check() -> dict[str, object]:
    """Check Alembic revision names and graph without touching the live database."""
    backend_root = Path(__file__).resolve().parents[2]
    try:
        validate_revision_chain(backend_root)
    except Exception as exc:
        return {
            "ok": False,
            "error": exc.__class__.__name__,
        }
    return {"ok": True}


def _metrics_check() -> dict[str, object]:
    """Check that the aggregate metrics backend can return a snapshot."""
    try:
        snapshot = metrics_hook.snapshot()
    except Exception as exc:  # pragma: no cover - defensive for deployment diagnostics
        return {
            "ok": False,
            "error": exc.__class__.__name__,
        }
    return {
        "ok": True,
        "summary": {
            "total_runs": snapshot.total_runs,
            "crisis_runs": snapshot.crisis_runs,
            "fallback_runs": snapshot.fallback_runs,
            "rate_limit_hits": snapshot.rate_limit_hits,
            "llm_concurrency_saturation_count": snapshot.llm_concurrency_saturation,
            "slow_request_count": snapshot.slow_request_count,
        },
    }


def _auth_config_check() -> dict[str, object]:
    """Check non-secret authentication hardening settings."""
    mode = auth_mode()
    auth_limit = _int_env("SOCIALEASE_AUTH_RATE_LIMIT_PER_MINUTE")
    effective_limit = auth_limit if auth_limit is not None else (5 if mode == "production" else 0)
    signup_value = os.getenv("SOCIALEASE_ENABLE_SIGNUP")
    public_signup = (
        signup_value.strip().lower() in {"1", "true", "yes", "on"}
        if signup_value is not None
        else mode != "production"
    )
    warnings: list[str] = []
    if mode == "production" and effective_limit <= 0:
        warnings.append("auth_rate_limit_disabled")
    if mode == "production" and public_signup:
        warnings.append("public_signup_enabled")
    if mode == "production" and not auth_cookies_enabled():
        warnings.append("cookie_auth_disabled")
    return {
        "ok": True,
        "mode": mode,
        "public_signup_enabled": public_signup,
        "cookie_auth_enabled": auth_cookies_enabled(),
        "auth_rate_limit_per_minute": effective_limit,
        "warnings": warnings,
    }


def _safe_database_label(database_url: str) -> str:
    """Return a non-secret database label for readiness output."""
    provider = resolve_database_provider(database_url)
    if provider == DatabaseProvider.SQLITE:
        return "sqlite"
    if provider == DatabaseProvider.POSTGRES:
        return "postgres"
    return "unknown"


def _int_env(name: str) -> int | None:
    """Return an integer env var or None when unset/invalid."""
    value = os.getenv(name)
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None
