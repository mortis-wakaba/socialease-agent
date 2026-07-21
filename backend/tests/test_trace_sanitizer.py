"""Tests for trace privacy sanitization and observability failure isolation."""

from datetime import datetime, timezone

import pytest

from app.db.repositories import InMemoryTraceRepository
from app.models import ChatRequest, Intent, IntentResult, RiskLevel, SafetyResult, TraceRecord
from app.tracing.logger import TraceLogger
from app.tracing.sanitizer import TraceSanitizer
from app.workflow.engine import AgentHarness


class FailingTraceRepository:
    """Trace repository double that simulates an unavailable diagnostics backend."""

    def save(self, record: TraceRecord) -> TraceRecord:
        del record
        raise RuntimeError("api_key=sk-fake-test-value")

    def get(self, run_id: str) -> TraceRecord | None:
        del run_id
        return None

    def list_recent(self, limit: int = 100) -> list[TraceRecord]:
        del limit
        return []


class FailingAfterTraceHook:
    """Hook double that fails after the product trace was persisted."""

    def after_trace(self, trace: TraceRecord) -> None:
        del trace
        raise RuntimeError("contact trace-hook@example.com with password=unsafe")


def _trace_record() -> TraceRecord:
    return TraceRecord(
        run_id="trace-sanitizer-run",
        user_id="owner",
        input="[raw chat input minimized by privacy policy]",
        safety_result=SafetyResult(
            risk_level=RiskLevel.LOW,
            reason="classifier mentioned trace-safety@example.com",
        ),
        intent_result=IntentResult(
            intent=Intent.EMOTIONAL_SUPPORT,
            confidence=0.8,
            reason="router echoed 13912345678",
        ),
        selected_skill="general_support_skill",
        selected_agent="support_agent",
        permission_reason="authorization=Bearer secret-token-value",
        agent_loop_used=True,
        agent_loop_steps=[
            {
                "step": 1,
                "query": "地址：北京市海淀区中关村大街27号",
                "citation_count": 1,
            }
        ],
        output="[assistant output summarized by privacy policy: supportive_response]",
        errors=["TOOL_OR_SKILL_FAILURE:provider returned private payload"],
        latency_ms=12.0,
        created_at=datetime.now(timezone.utc),
    )


def test_trace_sanitizer_redacts_all_supported_free_text_metadata() -> None:
    sanitized = TraceSanitizer().sanitize(_trace_record())
    payload = sanitized.model_dump_json()

    assert "trace-safety@example.com" not in payload
    assert "13912345678" not in payload
    assert "secret-token-value" not in payload
    assert "中关村大街27号" not in payload
    assert "provider returned private payload" not in payload
    assert sanitized.errors == [
        "TOOL_OR_SKILL_FAILURE:[redacted:error_detail]"
    ]
    metadata_policies = [
        field
        for field in sanitized.privacy_summary.fields
        if field.persistence_kind == "trace_metadata"
    ]
    assert {field.field for field in metadata_policies} >= {
        "safety_result.reason",
        "intent_result.reason",
        "permission_reason",
        "errors[0]",
        "agent_loop_steps[0].query",
    }


def test_trace_logger_sanitizes_before_repository_write() -> None:
    repository = InMemoryTraceRepository()
    logger = TraceLogger(repository=repository)

    saved = logger.save(_trace_record())
    fetched = repository.get(saved.run_id)

    assert fetched is not None
    assert "trace-safety@example.com" not in fetched.model_dump_json()
    assert fetched.safety_result.reason.endswith("[redacted:email]")


@pytest.mark.anyio
async def test_trace_repository_failure_does_not_block_crisis_response() -> None:
    harness = AgentHarness(
        trace_logger=TraceLogger(repository=FailingTraceRepository()),
    )

    response = await harness.run(
        ChatRequest(
            user_id="crisis-owner",
            message="我不想活了，可能会伤害自己",
            context={},
        )
    )

    assert response.risk_level == RiskLevel.CRISIS
    assert response.structured_data["trace_persisted"] is False
    assert response.trace.error_categories == ["TRACE_PERSISTENCE_FAILURE"]
    assert response.trace.errors == ["TRACE_PERSISTENCE_FAILURE:RuntimeError"]
    assert "sk-should-never" not in response.model_dump_json()


@pytest.mark.anyio
async def test_after_trace_hook_failure_is_isolated_from_business_response() -> None:
    repository = InMemoryTraceRepository()
    harness = AgentHarness(
        trace_logger=TraceLogger(repository=repository),
        hooks=(FailingAfterTraceHook(),),
    )

    response = await harness.run(
        ChatRequest(user_id="hook-owner", message="我今天有点紧张", context={})
    )

    assert response.response
    assert response.structured_data["trace_persisted"] is True
    assert response.structured_data["observability_hook_failed"] is True
    assert response.trace.errors[-1] == "OBSERVABILITY_HOOK_FAILURE:RuntimeError"
    assert response.trace.execution_version.trace_schema_version == "trace-v2"
    assert response.trace.execution_version.prompt_versions == {}
    assert "trace-hook@example.com" not in response.model_dump_json()
    assert repository.get(response.run_id) is not None
