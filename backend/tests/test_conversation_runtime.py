"""Tests for process-wide conversation runtime wiring."""

import pytest

from app.services import conversation_runtime
from app.workflow.router import LlmIntentRouter


class StubLocalLLM:
    """Minimal local client identity used to verify dependency sharing."""

    async def generate_text(self, **kwargs: object) -> str:
        del kwargs
        return "{}"


def test_runtime_shares_local_model_between_router_and_compactor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    local_client = StubLocalLLM()
    captured: dict[str, object] = {}

    class StubHarness:
        def __init__(self, **kwargs: object) -> None:
            captured["harness"] = kwargs

    class StubConversationService:
        def __init__(self, **kwargs: object) -> None:
            captured["service"] = kwargs

    conversation_runtime.conversation_service.cache_clear()
    monkeypatch.setattr(
        conversation_runtime,
        "create_local_llm_client",
        lambda: local_client,
    )
    monkeypatch.setattr(conversation_runtime, "AgentHarness", StubHarness)
    monkeypatch.setattr(
        conversation_runtime,
        "ConversationService",
        StubConversationService,
    )

    service = conversation_runtime.conversation_service()

    service_kwargs = captured["service"]
    harness_kwargs = captured["harness"]
    assert isinstance(service, StubConversationService)
    assert isinstance(service_kwargs, dict)
    assert isinstance(harness_kwargs, dict)
    assert service_kwargs["lightweight_llm_client"] is local_client
    assert service_kwargs["intent_router"] is harness_kwargs["intent_router"]
    assert isinstance(service_kwargs["intent_router"], LlmIntentRouter)
    assert service_kwargs["intent_router"].llm_client is local_client

    conversation_runtime.conversation_service.cache_clear()
