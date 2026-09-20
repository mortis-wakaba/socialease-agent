"""Tests for default LLM-first intent routing with deterministic fallback."""

import pytest

from app.models import (
    Intent,
    IntentAuthorizationBasis,
    IntentStepStatus,
    RiskLevel,
    SafetyResult,
)
from app.workflow.router import (
    LlmIntentRouter,
    compile_llm_intent_prediction,
    reconcile_intent_risk_policy,
)


def test_authoritative_high_risk_tightens_speculative_execution_plan() -> None:
    result = compile_llm_intent_prediction(
        {
            "i": "calendar_planning",
            "c": 0.95,
            "e": True,
            "p": True,
            "s": [],
        },
        risk_level=RiskLevel.LOW,
    )

    tightened = reconcile_intent_risk_policy(result, risk_level=RiskLevel.HIGH)

    assert tightened.execution_plan is not None
    step = tightened.execution_plan.steps[0]
    assert step.status is IntentStepStatus.AWAITING_CONSENT
    assert step.undo_supported is False


def test_authoritative_medium_risk_only_tightens_exposure_steps() -> None:
    exposure = compile_llm_intent_prediction(
        {"i": "exposure_planning", "c": 0.95, "e": True, "p": True, "s": []}
    )
    calendar = compile_llm_intent_prediction(
        {"i": "calendar_planning", "c": 0.95, "e": True, "p": True, "s": []}
    )

    exposure = reconcile_intent_risk_policy(
        exposure,
        risk_level=RiskLevel.MEDIUM,
    )
    calendar = reconcile_intent_risk_policy(
        calendar,
        risk_level=RiskLevel.MEDIUM,
    )

    assert exposure.execution_plan is not None
    assert calendar.execution_plan is not None
    assert (
        exposure.execution_plan.steps[0].status
        is IntentStepStatus.AWAITING_CONSENT
    )
    assert calendar.execution_plan.steps[0].status is IntentStepStatus.READY_WITH_UNDO


class FakeLLMClient:
    """Async fake for LLM router outputs."""

    def __init__(self, response: str, should_fail: bool = False) -> None:
        self.response = response
        self.should_fail = should_fail
        self.calls = 0

    async def generate_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.2,
    ) -> str:
        self.calls += 1
        if self.should_fail:
            raise RuntimeError("provider unavailable")
        return self.response


class FakeJSONModeLLMClient(FakeLLMClient):
    """Record that production routing prefers provider JSON mode."""

    json_mode_enabled = True

    def __init__(self, response: str) -> None:
        super().__init__(response)
        self.text_calls = 0
        self.json_calls = 0

    async def generate_text(self, **kwargs: object) -> str:
        self.text_calls += 1
        return await super().generate_text(**kwargs)  # type: ignore[arg-type]

    async def generate_json_text(self, **kwargs: object) -> str:
        self.json_calls += 1
        return await super().generate_text(**kwargs)  # type: ignore[arg-type]


class FakeStructuredJSONLLMClient(FakeJSONModeLLMClient):
    """Record the schema and token bound selected by production routing."""

    def __init__(self, response: str) -> None:
        super().__init__(response)
        self.structured_calls = 0
        self.json_schema: dict[str, object] | None = None
        self.schema_name: str | None = None
        self.max_tokens: int | None = None

    async def generate_structured_json_text(self, **kwargs: object) -> str:
        self.structured_calls += 1
        self.json_schema = kwargs["json_schema"]  # type: ignore[assignment]
        self.schema_name = kwargs["schema_name"]  # type: ignore[assignment]
        self.max_tokens = kwargs["max_tokens"]  # type: ignore[assignment]
        self.calls += 1
        return self.response


@pytest.fixture
def low_safety() -> SafetyResult:
    """Return a reusable low-risk safety fixture."""
    return SafetyResult(risk_level=RiskLevel.LOW, reason="safe")


@pytest.mark.anyio
async def test_llm_router_is_default_when_available(low_safety: SafetyResult) -> None:
    router = LlmIntentRouter(
        llm_client=FakeLLMClient(
            '{"intent":"exposure_planning","confidence":0.91,'
            '"reason":"semantic match","explicitly_requested":true,'
            '"parameters_complete":true,"secondary_intents":[]}'
        )
    )

    result = await router.route("我想先一点点练，不想一下子就上台", low_safety)

    assert result.intent == Intent.EXPOSURE_PLANNING
    assert result.llm_usage.used is True
    assert result.llm_usage.fallback_used is False


@pytest.mark.anyio
async def test_llm_router_uses_json_mode_for_local_structured_output(
    low_safety: SafetyResult,
) -> None:
    client = FakeJSONModeLLMClient(
        '{"intent":"progress_review","confidence":0.9,'
        '"reason":"history request","explicitly_requested":true,'
        '"parameters_complete":true,"secondary_intents":[]}'
    )
    router = LlmIntentRouter(llm_client=client)

    result = await router.route("帮我回顾最近的练习", low_safety)

    assert result.intent == Intent.PROGRESS_REVIEW
    assert client.json_calls == 1
    assert client.text_calls == 0


@pytest.mark.anyio
async def test_llm_router_prefers_strict_json_schema(
    low_safety: SafetyResult,
) -> None:
    client = FakeStructuredJSONLLMClient(
        '{"intent":"progress_review","confidence":0.9,'
        '"reason":"history request","explicitly_requested":true,'
        '"parameters_complete":true,"secondary_intents":[]}'
    )
    router = LlmIntentRouter(llm_client=client)

    result = await router.route("帮我回顾最近的练习", low_safety)

    assert result.intent == Intent.PROGRESS_REVIEW
    assert client.structured_calls == 1
    assert client.json_calls == 0
    assert client.text_calls == 0
    assert client.schema_name == "socialease_intent_prediction"
    assert client.max_tokens == 64
    assert client.json_schema is not None
    assert client.json_schema["type"] == "object"
    properties = client.json_schema["properties"]
    assert isinstance(properties, dict)
    assert set(properties) == {"i", "c", "e", "p"}
    assert "$defs" not in client.json_schema
    assert "title" not in client.json_schema


@pytest.mark.anyio
async def test_llm_router_allows_secondary_for_explicit_sequenced_actions(
    low_safety: SafetyResult,
) -> None:
    client = FakeStructuredJSONLLMClient(
        """{
            "intent":"exposure_planning",
            "confidence":0.94,
            "reason":"Bounded semantic match.",
            "explicitly_requested":true,
            "parameters_complete":false,
            "secondary_intents":[{
                "intent":"calendar_planning",
                "confidence":0.9,
                "relation":"after_primary",
                "reason":"Bounded secondary action.",
                "explicitly_requested":true,
                "parameters_complete":false
            }]
        }"""
    )
    router = LlmIntentRouter(llm_client=client)

    result = await router.route(
        "先帮我制定练习阶梯，然后把第一级安排到日历里。",
        low_safety,
    )

    assert result.intent == Intent.EXPOSURE_PLANNING
    assert [item.intent for item in result.secondary_intents] == [
        Intent.CALENDAR_PLANNING
    ]
    assert client.max_tokens == 128
    assert client.json_schema is not None
    properties = client.json_schema["properties"]
    assert isinstance(properties, dict)
    assert properties["s"]["maxItems"] == 2


@pytest.mark.anyio
async def test_llm_router_blocks_secondary_for_deferred_or_negated_action(
    low_safety: SafetyResult,
) -> None:
    client = FakeStructuredJSONLLMClient(
        '{"intent":"exposure_planning","confidence":0.94,'
        '"reason":"Bounded semantic match.","explicitly_requested":true,'
        '"parameters_complete":false,"secondary_intents":[]}'
    )
    router = LlmIntentRouter(llm_client=client)

    result = await router.route(
        "今天先做练习阶梯，同时日历提醒以后再说。",
        low_safety,
    )

    assert result.intent == Intent.EXPOSURE_PLANNING
    assert client.max_tokens == 64
    assert client.json_schema is not None
    properties = client.json_schema["properties"]
    assert isinstance(properties, dict)
    assert "s" not in properties


@pytest.mark.anyio
async def test_llm_router_falls_back_on_invalid_output(low_safety: SafetyResult) -> None:
    router = LlmIntentRouter(llm_client=FakeLLMClient("not-json"))

    result = await router.route("我想模拟向老师提问的对话", low_safety)

    assert result.intent == Intent.ROLEPLAY_PRACTICE
    assert result.llm_usage.used is False
    assert result.llm_usage.fallback_used is True
    assert result.llm_usage.error_category == "INVALID_JSON"


@pytest.mark.anyio
async def test_llm_router_requires_multi_intent_contract_field(
    low_safety: SafetyResult,
) -> None:
    router = LlmIntentRouter(
        llm_client=FakeLLMClient(
            '{"intent":"emotional_support","confidence":0.9,"reason":"legacy shape"}'
        )
    )

    result = await router.route("我只想聊聊。", low_safety)

    assert result.llm_usage.fallback_used is True
    assert result.llm_usage.error_category == "INVALID_SCHEMA"


@pytest.mark.anyio
async def test_llm_router_falls_back_on_provider_failure(low_safety: SafetyResult) -> None:
    router = LlmIntentRouter(llm_client=FakeLLMClient("", should_fail=True))

    result = await router.route("帮我做 thought record worksheet", low_safety)

    assert result.intent == Intent.CBT_WORKSHEET
    assert result.llm_usage.fallback_used is True
    assert result.llm_usage.error_category == "TRANSIENT_PROVIDER_ERROR"


@pytest.mark.anyio
async def test_crisis_skips_llm_router_call() -> None:
    client = FakeLLMClient(
        '{"intent":"roleplay_practice","confidence":0.9,'
        '"reason":"wrong","explicitly_requested":true,'
        '"parameters_complete":true,"secondary_intents":[]}'
    )
    router = LlmIntentRouter(llm_client=client)
    safety = SafetyResult(risk_level=RiskLevel.CRISIS, reason="crisis")

    result = await router.route("我想角色扮演", safety)

    assert result.intent == Intent.CRISIS
    assert client.calls == 0
    assert result.llm_usage.used is False


@pytest.mark.anyio
async def test_llm_router_low_confidence_action_requests_clarification(
    low_safety: SafetyResult,
) -> None:
    router = LlmIntentRouter(
        llm_client=FakeLLMClient(
            '{"intent":"roleplay_practice","confidence":0.41,'
            '"reason":"unclear","explicitly_requested":false,'
            '"parameters_complete":false,"secondary_intents":[]}'
        )
    )

    result = await router.route("你能帮我一下吗", low_safety)

    assert result.intent == Intent.CLARIFICATION_NEEDED
    assert result.llm_usage.used is True


@pytest.mark.anyio
async def test_llm_router_compiles_multiple_intents_into_sequential_plan(
    low_safety: SafetyResult,
) -> None:
    router = LlmIntentRouter(
        llm_client=FakeLLMClient(
            """{
                "intent":"exposure_planning",
                "confidence":0.94,
                "reason":"the user asked to build the ladder first",
                "explicitly_requested":true,
                "parameters_complete":true,
                "secondary_intents":[
                    {
                        "intent":"calendar_planning",
                        "confidence":0.91,
                        "relation":"depends_on_primary",
                        "reason":"the reminder depends on the selected practice step",
                        "explicitly_requested":true,
                        "parameters_complete":true
                    }
                ]
            }"""
        )
    )

    result = await router.route(
        "先帮我制定练习阶梯，然后把第一级安排到日历里。",
        low_safety,
    )

    assert result.intent == Intent.EXPOSURE_PLANNING
    assert [item.intent for item in result.secondary_intents] == [
        Intent.CALENDAR_PLANNING
    ]
    assert result.execution_plan is not None
    assert result.execution_plan.mode == "sequential"
    assert result.execution_plan.current_intent == Intent.EXPOSURE_PLANNING
    assert result.execution_plan.pending_intents == [Intent.CALENDAR_PLANNING]
    assert result.execution_plan.steps[0].status == IntentStepStatus.READY
    assert (
        result.execution_plan.steps[1].status
        == IntentStepStatus.READY_WITH_UNDO
    )
    assert result.execution_plan.steps[1].undo_supported is True


@pytest.mark.anyio
async def test_llm_router_requires_consent_for_inferred_incomplete_step(
    low_safety: SafetyResult,
) -> None:
    router = LlmIntentRouter(
        llm_client=FakeLLMClient(
            """{
                "intent":"emotional_support",
                "confidence":0.9,
                "reason":"support first",
                "explicitly_requested":true,
                "parameters_complete":true,
                "secondary_intents":[{
                    "intent":"calendar_planning",
                    "confidence":0.8,
                    "relation":"after_primary",
                    "reason":"a possible follow-up",
                    "explicitly_requested":false,
                    "parameters_complete":false
                }]
            }"""
        )
    )

    result = await router.route("我有点紧张，之后也许可以提醒我。", low_safety)

    assert result.execution_plan is not None
    assert (
        result.execution_plan.steps[1].status
        == IntentStepStatus.AWAITING_CONSENT
    )
    assert (
        result.execution_plan.steps[1].authorization_basis
        == IntentAuthorizationBasis.INFERRED
    )


@pytest.mark.anyio
async def test_llm_router_conflicting_intents_request_clarification(
    low_safety: SafetyResult,
) -> None:
    router = LlmIntentRouter(
        llm_client=FakeLLMClient(
            """{
                "intent":"roleplay_practice",
                "confidence":0.82,
                "reason":"one possible action",
                "explicitly_requested":false,
                "parameters_complete":false,
                "secondary_intents":[
                    {
                        "intent":"cbt_worksheet",
                        "confidence":0.79,
                        "relation":"conflicting",
                        "reason":"the user did not choose which interactive task to start",
                        "explicitly_requested":false,
                        "parameters_complete":false
                    }
                ]
            }"""
        )
    )

    result = await router.route(
        "角色扮演或者做想法记录都行，你替我决定。",
        low_safety,
    )

    assert result.intent == Intent.CLARIFICATION_NEEDED
    assert result.secondary_intents == []
    assert result.execution_plan is not None
    assert result.execution_plan.pending_intents == []


@pytest.mark.anyio
async def test_llm_router_filters_unsafe_duplicate_and_low_confidence_secondaries(
    low_safety: SafetyResult,
) -> None:
    router = LlmIntentRouter(
        llm_client=FakeLLMClient(
            """{
                "intent":"progress_review",
                "confidence":0.9,
                "reason":"review comes first",
                "explicitly_requested":true,
                "parameters_complete":true,
                "secondary_intents":[
                    {
                        "intent":"progress_review",
                        "confidence":0.9,
                        "relation":"after_primary",
                        "reason":"duplicate",
                        "explicitly_requested":true,
                        "parameters_complete":true
                    },
                    {
                        "intent":"calendar_planning",
                        "confidence":0.4,
                        "relation":"depends_on_primary",
                        "reason":"too uncertain to queue",
                        "explicitly_requested":false,
                        "parameters_complete":false
                    }
                ]
            }"""
        )
    )

    result = await router.route("复盘后可能再设置提醒。", low_safety)

    assert result.intent == Intent.PROGRESS_REVIEW
    assert result.secondary_intents == []
    assert result.execution_plan is not None
    assert result.execution_plan.pending_intents == []


@pytest.mark.anyio
async def test_llm_router_rejects_crisis_as_secondary_intent(
    low_safety: SafetyResult,
) -> None:
    router = LlmIntentRouter(
        llm_client=FakeLLMClient(
            """{
                "intent":"emotional_support",
                "confidence":0.9,
                "reason":"ordinary request",
                "explicitly_requested":true,
                "parameters_complete":true,
                "secondary_intents":[
                    {
                        "intent":"crisis",
                        "confidence":0.9,
                        "relation":"independent",
                        "reason":"unsafe router authority",
                        "explicitly_requested":true,
                        "parameters_complete":true
                    }
                ]
            }"""
        )
    )

    result = await router.route("我想聊聊，也想练习。", low_safety)

    assert result.llm_usage.fallback_used is True
    assert result.llm_usage.error_category == "INVALID_SCHEMA"
