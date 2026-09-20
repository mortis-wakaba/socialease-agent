"""Readiness checks for deployment and pilot operations."""

from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import text

from app.auth.cookies import auth_cookies_enabled
from app.auth.tokens import auth_mode
from app.db.capabilities import database_capability_report
from app.db.config import database_settings
from app.db.migration_check import validate_revision_chain
from app.db.providers import DatabaseProvider, resolve_database_provider
from app.db.postgres.engine import shared_postgres_async_engine
from app.rate_limit import rate_limit_backend
from app.llm.local_runtime import local_llm_runtime
from app.llm.factory import LLMConfig
from app.workflow.default_hooks import metrics_hook

async def readiness_snapshot() -> tuple[int, dict[str, object]]:
    """Return HTTP status and a non-secret readiness snapshot."""
    checks: dict[str, dict[str, object]] = {
        "database": await _database_check(),
        "capabilities": _capability_check(),
        "migrations": _migration_graph_check(),
        "metrics": await _metrics_check(),
        "auth": _auth_config_check(),
        "rate_limit": _rate_limit_config_check(),
        "calendar": _calendar_config_check(),
        "outbox": await _outbox_check(),
        "local_llm": await local_llm_runtime.health(),
        "llm_configuration": _llm_configuration_check(),
    }
    ready = all(check["ok"] is True for check in checks.values())
    return (
        200 if ready else 503,
        {
            "status": "ready" if ready else "not_ready",
            "checks": checks,
        },
    )


async def _database_check() -> dict[str, object]:
    """Check that the configured database can answer a trivial query."""
    settings = database_settings()
    provider = resolve_database_provider(settings.database_url)
    try:
        if provider == DatabaseProvider.POSTGRES:
            engine = shared_postgres_async_engine(settings.database_url)
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
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
            "capabilities": list(report.capabilities),
            "unavailable_capabilities": list(report.unavailable_capabilities),
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


async def _metrics_check() -> dict[str, object]:
    """Check that the aggregate metrics backend can return a snapshot."""
    try:
        snapshot = await metrics_hook.snapshot()
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
            "module_outbox_dead_letter_count": snapshot.runtime_event_counts.get(
                "module_outbox_dead_letter", 0
            ),
            "calendar_outbox_dead_letter_count": snapshot.runtime_event_counts.get(
                "calendar_outbox_dead_letter", 0
            ),
        },
    }


async def _outbox_check() -> dict[str, object]:
    """Expose payload-free queue health for worker operations."""
    try:
        from app.calendar.outbox import CalendarActionOutbox

        calendar = await CalendarActionOutbox().health()
        module = await _module_outbox_health()
    except Exception as exc:
        return {"ok": False, "error": exc.__class__.__name__}
    dead_letter = calendar.dead_letter + module["dead_letter"]
    return {
        "ok": True,
        "healthy": dead_letter == 0,
        "calendar": {
            "pending": calendar.pending,
            "processing": calendar.processing,
            "dead_letter": calendar.dead_letter,
            "oldest_pending_seconds": calendar.oldest_pending_seconds,
        },
        "module_start": module,
        "warnings": ["dead_letter_jobs_present"] if dead_letter else [],
    }


async def _module_outbox_health() -> dict[str, int]:
    """Return non-sensitive module-start outbox queue counts."""
    settings = database_settings()
    engine = shared_postgres_async_engine(settings.database_url)
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text(
                    """SELECT
                    COUNT(*) FILTER (WHERE status = 'pending') AS pending,
                    COUNT(*) FILTER (WHERE status = 'processing') AS processing,
                    COUNT(*) FILTER (WHERE status = 'dead_letter') AS dead_letter
                    FROM conversation_module_start_outbox"""
                )
            )
        ).mappings().one()
    return {
        "pending": int(row["pending"]),
        "processing": int(row["processing"]),
        "dead_letter": int(row["dead_letter"]),
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


def _calendar_config_check() -> dict[str, object]:
    """Reject the in-memory calendar provider in production deployments."""
    configured = bool(os.getenv("SOCIALEASE_CALENDAR_MCP_URL", "").strip())
    production = auth_mode() == "production"
    return {
        "ok": configured or not production,
        "transport": "remote_mcp" if configured else "demo_in_process",
    }


def _rate_limit_config_check() -> dict[str, object]:
    """Describe whether request budgets are safe for the deployment topology."""
    backend = rate_limit_backend()
    limit = _int_env("SOCIALEASE_RATE_LIMIT_PER_MINUTE") or 0
    redis_configured = bool(os.getenv("SOCIALEASE_REDIS_URL", "").strip())
    supported = backend in {"local", "gateway", "redis"}
    configured = backend != "redis" or redis_configured
    warnings: list[str] = []
    if backend == "local":
        warnings.append("single_process_only")
    if limit <= 0 and backend != "gateway":
        warnings.append("application_rate_limit_disabled")
    return {
        "ok": supported and configured,
        "backend": backend,
        "limit_per_minute": limit,
        "multi_instance_safe": backend in {"gateway", "redis"},
        "warnings": warnings,
    }


def _llm_configuration_check() -> dict[str, object]:
    """Report effective optional-model modes without probing paid providers."""
    hosted = LLMConfig.from_env()
    local = LLMConfig.local_from_env()
    semantic_enabled = _env_bool("OUTPUT_GUARDRAIL_LLM_ENABLED", False)
    hosted_complete = _llm_config_complete(hosted)
    local_complete = _llm_config_complete(local)
    if hosted.enabled and not hosted_complete:
        generation_status = "unavailable"
    elif hosted.enabled:
        generation_status = "enabled_unprobed"
    else:
        generation_status = "disabled"
    if not semantic_enabled:
        guardrail_status = "disabled"
    elif not hosted.enabled or not hosted_complete:
        guardrail_status = "degraded_unavailable"
    else:
        guardrail_status = "enabled_unprobed"
    if local.enabled and not local_complete:
        local_status = "unavailable"
    elif local.enabled:
        local_status = "enabled"
    else:
        local_status = "disabled"
    ok = (
        (not hosted.enabled or hosted_complete)
        and (not local.enabled or local_complete)
        and guardrail_status != "degraded_unavailable"
    )
    return {
        "ok": ok,
        "generation": generation_status,
        "output_guardrail_semantic": guardrail_status,
        "local_routing_and_summary": local_status,
        "provider_availability_probe": "not_performed",
    }


def _llm_config_complete(config: LLMConfig) -> bool:
    return bool(config.base_url and config.api_key and config.model)


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().casefold() in {"1", "true", "yes", "on"}


def _safe_database_label(database_url: str) -> str:
    """Return a non-secret database label for readiness output."""
    provider = resolve_database_provider(database_url)
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
