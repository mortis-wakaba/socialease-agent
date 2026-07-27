"""Tests for production Redis requirements and readiness reporting."""

from __future__ import annotations

import httpx
import pytest

import app.main as main_module
from app.main import app
from app.memory.runtime_requirements import (
    TaskStateConfigurationError,
    task_state_runtime_report,
    validate_task_state_runtime,
)
from app.services.roleplay_service import roleplay_service
from app.services.support_resource_service import support_resource_service
from app.services.worksheet_service import worksheet_service


@pytest.fixture
def anyio_backend() -> str:
    """Run async readiness tests on asyncio."""
    return "asyncio"


def test_demo_runtime_allows_explicitly_disabled_task_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SOCIALEASE_AUTH_MODE", "demo")
    monkeypatch.delenv("SOCIALEASE_REQUIRE_REDIS", raising=False)
    monkeypatch.delenv("SOCIALEASE_REDIS_URL", raising=False)

    report = validate_task_state_runtime()

    assert report.required is False
    assert report.configured is False
    assert report.configuration_ok is True


def test_production_runtime_rejects_missing_redis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SOCIALEASE_AUTH_MODE", "production")
    monkeypatch.delenv("SOCIALEASE_REQUIRE_REDIS", raising=False)
    monkeypatch.delenv("SOCIALEASE_REDIS_URL", raising=False)

    with pytest.raises(TaskStateConfigurationError):
        validate_task_state_runtime()


def test_explicit_requirement_override_supports_database_only_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SOCIALEASE_AUTH_MODE", "production")
    monkeypatch.setenv("SOCIALEASE_REQUIRE_REDIS", "false")
    monkeypatch.delenv("SOCIALEASE_REDIS_URL", raising=False)

    report = task_state_runtime_report()

    assert report.required is False
    assert report.configuration_ok is True


@pytest.mark.anyio
async def test_readiness_fails_when_any_required_task_state_probe_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SOCIALEASE_AUTH_MODE", "production")
    monkeypatch.delenv("SOCIALEASE_REQUIRE_REDIS", raising=False)
    monkeypatch.setenv("SOCIALEASE_REDIS_URL", "redis://configured-but-probed/0")

    async def healthy() -> bool:
        return True

    async def unhealthy() -> bool:
        return False

    monkeypatch.setattr(roleplay_service, "context_health", healthy)
    monkeypatch.setattr(worksheet_service, "context_health", unhealthy)
    monkeypatch.setattr(support_resource_service, "context_health", healthy)
    monkeypatch.setattr(
        main_module,
        "_conversation_context_health",
        healthy,
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/ready")

    assert response.status_code == 503
    task_state = response.json()["checks"]["task_state"]
    assert task_state["required"] is True
    assert task_state["configured"] is True
    assert task_state["components"] == {
        "roleplay": True,
        "worksheet": False,
        "support_search": True,
        "conversation_context": True,
    }


@pytest.mark.anyio
async def test_readiness_accepts_all_required_task_state_probes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SOCIALEASE_AUTH_MODE", "production")
    monkeypatch.delenv("SOCIALEASE_REQUIRE_REDIS", raising=False)
    monkeypatch.setenv("SOCIALEASE_REDIS_URL", "redis://configured-and-probed/0")

    async def healthy() -> bool:
        return True

    monkeypatch.setattr(roleplay_service, "context_health", healthy)
    monkeypatch.setattr(worksheet_service, "context_health", healthy)
    monkeypatch.setattr(support_resource_service, "context_health", healthy)
    monkeypatch.setattr(
        main_module,
        "_conversation_context_health",
        healthy,
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/ready")

    assert response.status_code == 200
    assert response.json()["checks"]["task_state"]["ok"] is True
