"""Deterministic tests for the global Harness output policy layer."""

import json

import pytest

from app.db.repositories import InMemoryTraceRepository
from app.guardrails.output import (
    GroundingMetadata,
    OutputBoundaryTier,
    OutputGuardrail,
    OutputGuardrailAction,
    SemanticCheckErrorType,
    SemanticSchemaErrorCode,
)
from app.llm.prompts import build_output_guardrail_system_prompt
from app.models import ChatRequest, Intent, RiskLevel
from app.skills.base import SkillContext, SkillDescriptor, SkillResult
from app.skills.registry import SkillRegistry
from app.tracing.logger import TraceLogger
from app.workflow.engine import AgentHarness
from app.workflow.router import RuleBasedIntentRouter
from app.safety.classifier import RuleBasedSafetyClassifier


class FakeLLMClient:
    """Return one semantic assessment or simulate provider failure."""

    def __init__(self, response: str = "", *, should_fail: bool = False) -> None:
        self.response = response
        self.should_fail = should_fail
        self.call_count = 0

    async def generate_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.2,
    ) -> str:
        del system_prompt, user_prompt, temperature
        self.call_count += 1
        if self.should_fail:
            raise RuntimeError("provider unavailable")
        return self.response


class SequenceLLMClient:
    """Return a bounded sequence for classify, repair, and recheck calls."""

    def __init__(self, responses: list[str | Exception]) -> None:
        self.responses = responses
        self.call_count = 0

    async def generate_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.2,
    ) -> str:
        del system_prompt, user_prompt, temperature
        item = self.responses[self.call_count]
        self.call_count += 1
        if isinstance(item, Exception):
            raise item
        return item


class UnsafeSupportSkill:
    """Test-only skill returning output that violates a global boundary."""

    descriptor = SkillDescriptor(
        name="general_support_skill",
        description="Unsafe test skill",
        supported_intents=(Intent.EMOTIONAL_SUPPORT,),
        entrypoint="tests.UnsafeSupportSkill",
        safety_notes="test only",
    )

    async def run(self, context: SkillContext) -> SkillResult:
        del context
        return SkillResult(
            response="你已经患有社交焦虑症，按这个方案一定会治好。",
            structured_data={"agent": "unsafe_test_agent", "action": "unsafe_test"},
            selected_agent="unsafe_test_agent",
        )


class InventedFactSupportSkill:
    """Test-only skill returning one otherwise repairable unsupported fact."""

    descriptor = UnsafeSupportSkill.descriptor

    async def run(self, context: SkillContext) -> SkillResult:
        del context
        return SkillResult(
            response="我上周被同学打伤了，所以最近压力很大。",
            structured_data={"agent": "test_agent", "action": "test_support"},
            selected_agent="test_agent",
        )


@pytest.mark.anyio
async def test_deterministic_output_guardrail_replaces_diagnosis_across_skills() -> None:
    guardrail = OutputGuardrail()

    result = await guardrail.evaluate(
        user_message="帮我找一个学校支持资源。",
        response="根据描述，你已经患有社交焦虑症。",
        intent=Intent.CAMPUS_RESOURCE_QUERY,
        risk_level=RiskLevel.LOW,
        selected_skill="support_resource_rag_skill",
        selected_agent="resource_guidance_agent",
    )

    assert result.action == OutputGuardrailAction.REPLACE
    assert [category.value for category in result.categories] == ["diagnosis"]
    assert result.violation_tier == OutputBoundaryTier.HARD_SAFETY
    assert "患有" not in result.response


@pytest.mark.anyio
async def test_resource_output_without_citations_cannot_claim_a_specific_office() -> None:
    result = await OutputGuardrail().evaluate(
        user_message="我们学校哪里可以求助？",
        response="你可以去学校北门三楼的心理援助办公室。",
        intent=Intent.CAMPUS_RESOURCE_QUERY,
        risk_level=RiskLevel.LOW,
        selected_skill="support_resource_rag_skill",
        selected_agent="resource_guidance_agent",
        grounding_metadata=GroundingMetadata(
            retrieval_unknown=True,
            citation_count=0,
        ),
    )

    assert result.action == OutputGuardrailAction.REPLACE
    assert [category.value for category in result.categories] == [
        "fabricated_contact"
    ]
    assert result.sources == ["deterministic"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("response", "metadata"),
    [
        (
            "当前知识库没有可靠的学校联系方式，因此我不会编造电话。",
            GroundingMetadata(retrieval_unknown=True, citation_count=0),
        ),
        (
            "可以查看经过审核的公开自助资料，具体内容以引用来源为准。",
            GroundingMetadata(
                retrieval_unknown=False,
                citation_count=1,
                citation_titles=["Reviewed Public Self-help Resource"],
            ),
        ),
    ],
)
async def test_resource_grounding_policy_allows_unknown_or_cited_answers(
    response: str,
    metadata: GroundingMetadata,
) -> None:
    result = await OutputGuardrail().evaluate(
        user_message="有没有可靠资源？",
        response=response,
        intent=Intent.CAMPUS_RESOURCE_QUERY,
        risk_level=RiskLevel.LOW,
        selected_skill="support_resource_rag_skill",
        selected_agent="resource_guidance_agent",
        grounding_metadata=metadata,
    )

    assert result.action == OutputGuardrailAction.ALLOW


@pytest.mark.anyio
async def test_semantic_output_guardrail_catches_paraphrased_dependency() -> None:
    assessment = {
        "violations": [
            {
                "category": "dependency_encouragement",
                "evidence": "现实中的人不会理解你",
                "reason": "Encourages withdrawal from real support.",
            }
        ]
    }
    guardrail = OutputGuardrail(
        FakeLLMClient(json.dumps(assessment, ensure_ascii=False))
    )

    result = await guardrail.evaluate(
        user_message="最近不知道和谁说。",
        response="现实中的人不会理解你，留在这里继续说就好。",
        intent=Intent.EMOTIONAL_SUPPORT,
        risk_level=RiskLevel.LOW,
        selected_skill="general_support_skill",
        selected_agent="support_generation_agent",
    )

    assert result.action == OutputGuardrailAction.REPLACE
    assert result.sources == ["semantic"]
    assert result.semantic_checked is True


@pytest.mark.anyio
async def test_semantic_candidate_requires_exact_response_evidence() -> None:
    assessment = {
        "violations": [
            {
                "category": "diagnosis",
                "evidence": "你患有某种疾病",
                "reason": "Model-proposed violation not present in output.",
            }
        ]
    }
    guardrail = OutputGuardrail(
        FakeLLMClient(json.dumps(assessment, ensure_ascii=False))
    )

    result = await guardrail.evaluate(
        user_message="我有点紧张。",
        response="可以先写下一句开场，也可以随时暂停。",
        intent=Intent.EMOTIONAL_SUPPORT,
        risk_level=RiskLevel.LOW,
        selected_skill="general_support_skill",
        selected_agent="support_generation_agent",
    )

    assert result.action == OutputGuardrailAction.ALLOW
    assert result.categories == []
    assert result.semantic_check_failed is True
    assert result.semantic_error_type == SemanticCheckErrorType.INVALID_EVIDENCE


def _invented_fact_assessment(evidence: str) -> str:
    return json.dumps(
        {
            "violations": [
                {
                    "category": "invented_user_fact",
                    "evidence": evidence,
                    "reason": "The user did not report this event.",
                }
            ]
        },
        ensure_ascii=False,
    )


@pytest.mark.anyio
async def test_repair_succeeds_only_after_clean_second_guardrail() -> None:
    client = SequenceLLMClient(
        [
            _invented_fact_assessment("我上周被同学打伤了"),
            json.dumps(
                {"repaired_response": "老师您好，我最近压力比较大，想找您聊一聊。"},
                ensure_ascii=False,
            ),
            '{"violations":[]}',
        ]
    )
    guardrail = OutputGuardrail(client)

    result = await guardrail.evaluate(
        user_message="帮我写一句和辅导员说明最近压力的话。",
        response="我上周被同学打伤了，所以最近压力很大。",
        intent=Intent.EMOTIONAL_SUPPORT,
        risk_level=RiskLevel.LOW,
        selected_skill="general_support_skill",
        selected_agent="support_generation_agent",
    )

    assert result.action == OutputGuardrailAction.REPAIR
    assert result.violation_tier == OutputBoundaryTier.SOFT_FACTUAL
    assert result.repair_attempted is True
    assert result.repair_succeeded is True
    assert result.repair_recheck_action == OutputGuardrailAction.ALLOW
    assert "打伤" not in result.response
    assert client.call_count == 3


@pytest.mark.anyio
async def test_repair_recheck_violation_fails_closed_without_second_repair() -> None:
    repaired = "从这些表现看，这就是一种心理疾病。"
    client = SequenceLLMClient(
        [
            _invented_fact_assessment("我上周被同学打伤了"),
            json.dumps({"repaired_response": repaired}, ensure_ascii=False),
            json.dumps(
                {
                    "violations": [
                        {
                            "category": "diagnosis",
                            "evidence": "这就是一种心理疾病",
                            "reason": "The repair introduced a diagnosis.",
                        }
                    ]
                },
                ensure_ascii=False,
            ),
        ]
    )
    guardrail = OutputGuardrail(client)

    result = await guardrail.evaluate(
        user_message="帮我写一句话。",
        response="我上周被同学打伤了。",
        intent=Intent.EMOTIONAL_SUPPORT,
        risk_level=RiskLevel.LOW,
        selected_skill="general_support_skill",
        selected_agent="support_generation_agent",
    )

    assert result.action == OutputGuardrailAction.REPLACE
    assert result.repair_attempted is True
    assert result.repair_succeeded is False
    assert result.repair_recheck_action == OutputGuardrailAction.REPLACE
    assert client.call_count == 3


@pytest.mark.anyio
@pytest.mark.parametrize("repair_result", ["not-json", RuntimeError("repair failed")])
async def test_invalid_or_failed_repair_is_replaced(
    repair_result: str | Exception,
) -> None:
    client = SequenceLLMClient(
        [
            _invented_fact_assessment("我上周被同学打伤了"),
            repair_result,
        ]
    )
    guardrail = OutputGuardrail(client)

    result = await guardrail.evaluate(
        user_message="帮我写一句话。",
        response="我上周被同学打伤了。",
        intent=Intent.EMOTIONAL_SUPPORT,
        risk_level=RiskLevel.LOW,
        selected_skill="general_support_skill",
        selected_agent="support_generation_agent",
    )

    assert result.action == OutputGuardrailAction.REPLACE
    assert result.repair_attempted is True
    assert result.repair_succeeded is False
    assert client.call_count == 2


@pytest.mark.anyio
async def test_semantic_provider_failure_degrades_to_deterministic_allow() -> None:
    guardrail = OutputGuardrail(FakeLLMClient(should_fail=True))

    result = await guardrail.evaluate(
        user_message="我想练一句开场。",
        response="我补充一个比较小的点。",
        intent=Intent.EMOTIONAL_SUPPORT,
        risk_level=RiskLevel.LOW,
        selected_skill="general_support_skill",
        selected_agent="support_generation_agent",
    )

    assert result.action == OutputGuardrailAction.ALLOW
    assert result.semantic_checked is True
    assert result.semantic_check_failed is True
    assert result.semantic_error_type == SemanticCheckErrorType.PROVIDER_ERROR


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("raw", "expected_error", "expected_schema_error", "expected_error_field"),
    [
        ("not-json", SemanticCheckErrorType.INVALID_JSON, None, None),
        (
            '{"violations":"not-a-list"}',
            SemanticCheckErrorType.SCHEMA_VALIDATION,
            SemanticSchemaErrorCode.INVALID_TYPE,
            "violations",
        ),
        (
            '{"violations":[{"category":"diagnosis","evidence":"x"}]}',
            SemanticCheckErrorType.SCHEMA_VALIDATION,
            SemanticSchemaErrorCode.MISSING_FIELD,
            "reason",
        ),
        (
            '{"violations":[],"note":"x"}',
            SemanticCheckErrorType.SCHEMA_VALIDATION,
            SemanticSchemaErrorCode.EXTRA_FIELD,
            "unexpected_field",
        ),
        (
            '{"violations":[{"category":"other","evidence":"x","reason":"x"}]}',
            SemanticCheckErrorType.SCHEMA_VALIDATION,
            SemanticSchemaErrorCode.INVALID_CATEGORY,
            "category",
        ),
        (
            '{"violations":[{"category":"diagnosis","evidence":"x","reason":""}]}',
            SemanticCheckErrorType.SCHEMA_VALIDATION,
            SemanticSchemaErrorCode.CONSTRAINT_VIOLATION,
            "reason",
        ),
        ("[]", SemanticCheckErrorType.INVALID_PAYLOAD, None, None),
    ],
)
async def test_semantic_response_failures_have_safe_error_types(
    raw: str,
    expected_error: SemanticCheckErrorType,
    expected_schema_error: SemanticSchemaErrorCode | None,
    expected_error_field: str | None,
) -> None:
    client = FakeLLMClient(raw)
    result = await OutputGuardrail(client).evaluate(
        user_message="我想练一句开场。",
        response="我补充一个比较小的点。",
        intent=Intent.EMOTIONAL_SUPPORT,
        risk_level=RiskLevel.LOW,
        selected_skill="general_support_skill",
        selected_agent="support_generation_agent",
    )

    assert result.action == OutputGuardrailAction.ALLOW
    assert result.semantic_check_failed is True
    assert result.semantic_error_type == expected_error
    assert result.semantic_schema_error_code == expected_schema_error
    assert result.semantic_schema_error_field == expected_error_field
    assert result.semantic_retry_attempted is True
    assert client.call_count == 2


@pytest.mark.anyio
async def test_semantic_schema_failure_retries_once_and_can_recover() -> None:
    client = SequenceLLMClient(
        [
            '{"violations":"not-a-list"}',
            '{"violations":[]}',
        ]
    )

    result = await OutputGuardrail(client).evaluate(
        user_message="我想练一句开场。",
        response="我补充一个比较小的点。",
        intent=Intent.EMOTIONAL_SUPPORT,
        risk_level=RiskLevel.LOW,
        selected_skill="general_support_skill",
        selected_agent="support_generation_agent",
    )

    assert result.action == OutputGuardrailAction.ALLOW
    assert result.semantic_check_failed is False
    assert result.semantic_retry_attempted is True
    assert client.call_count == 2


@pytest.mark.anyio
async def test_auxiliary_semantic_reason_is_truncated_without_retry() -> None:
    long_reason = "r" * 500
    assessment = json.dumps(
        {
            "violations": [
                {
                    "category": "diagnosis",
                    "evidence": "这就是一种心理疾病",
                    "reason": long_reason,
                }
            ]
        },
        ensure_ascii=False,
    )
    client = FakeLLMClient(assessment)

    result = await OutputGuardrail(client).evaluate(
        user_message="我最近社交时紧张。",
        response="这就是一种心理疾病。",
        intent=Intent.EMOTIONAL_SUPPORT,
        risk_level=RiskLevel.LOW,
        selected_skill="general_support_skill",
        selected_agent="support_generation_agent",
    )

    assert result.action == OutputGuardrailAction.REPLACE
    assert result.semantic_retry_attempted is False
    assert len(result.semantic_diagnostics[0].reason) == 240
    assert client.call_count == 1


def test_invented_fact_prompt_keeps_drafted_first_person_claims_grounded() -> None:
    prompt = build_output_guardrail_system_prompt()

    assert "request to draft wording does not supply missing facts" in prompt
    assert "declarative\n  first-person claim remains an assertion" in prompt


def test_output_guardrail_prompt_applies_negation_scope_globally() -> None:
    prompt = build_output_guardrail_system_prompt()
    definitions_index = prompt.index("Use these definitions:")

    assert prompt.index("scope and\ntarget of negation") < definitions_index
    assert "mere mention of a category topic" in prompt
    assert "semantic direction satisfies the category definition" in prompt


def test_output_guardrail_prompt_separates_social_prediction_from_danger() -> None:
    prompt = build_output_guardrail_system_prompt()

    assert "concrete external safety signal" in prompt
    assert "social evaluation, rejection, embarrassment" in prompt
    assert "compare every declarative consequential personal claim" in prompt


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("risk_level", "expected_action"),
    [
        (RiskLevel.LOW, OutputGuardrailAction.ALLOW),
        (RiskLevel.MEDIUM, OutputGuardrailAction.AUGMENT),
        (RiskLevel.HIGH, OutputGuardrailAction.REPLACE),
        (RiskLevel.CRISIS, OutputGuardrailAction.REPLACE),
    ],
)
async def test_semantic_failure_policy_is_risk_tiered(
    risk_level: RiskLevel,
    expected_action: OutputGuardrailAction,
) -> None:
    guardrail = OutputGuardrail(FakeLLMClient(should_fail=True))

    result = await guardrail.evaluate(
        user_message="测试输入",
        response="一段通过确定性检查的普通回复。",
        intent=Intent.EMOTIONAL_SUPPORT,
        risk_level=risk_level,
        selected_skill="general_support_skill",
        selected_agent="support_generation_agent",
    )

    assert result.action == expected_action
    assert result.semantic_check_failed is True
    if risk_level == RiskLevel.MEDIUM:
        assert "可以先暂停" in result.response
    if risk_level in {RiskLevel.HIGH, RiskLevel.CRISIS}:
        assert result.response != "一段通过确定性检查的普通回复。"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("intent", "risk_level", "selected_skill", "selected_agent", "response"),
    [
        (
            Intent.ROLEPLAY_PRACTICE,
            RiskLevel.LOW,
            "roleplay_skill",
            "roleplay_agent",
            "我们先练一句开场；你可以随时说暂停。",
        ),
        (
            Intent.CRISIS,
            RiskLevel.CRISIS,
            "crisis_escalation_skill",
            "crisis_escalation",
            "请先联系可信任的人；如果存在紧急危险，请联系当地紧急服务。",
        ),
    ],
)
async def test_global_guardrail_allows_safe_roleplay_and_crisis_output(
    intent: Intent,
    risk_level: RiskLevel,
    selected_skill: str,
    selected_agent: str,
    response: str,
) -> None:
    guardrail = OutputGuardrail()

    result = await guardrail.evaluate(
        user_message="测试输入",
        response=response,
        intent=intent,
        risk_level=risk_level,
        selected_skill=selected_skill,
        selected_agent=selected_agent,
    )

    assert result.action == OutputGuardrailAction.ALLOW
    assert result.response == response


@pytest.mark.anyio
async def test_harness_applies_global_output_guardrail_before_trace() -> None:
    harness = AgentHarness(
        trace_logger=TraceLogger(repository=InMemoryTraceRepository()),
        safety_classifier=RuleBasedSafetyClassifier(),
        intent_router=RuleBasedIntentRouter(),
        skill_registry=SkillRegistry(executable_skills=(UnsafeSupportSkill(),)),
        output_guardrail=OutputGuardrail(),
    )

    response = await harness.run(
        ChatRequest(
            user_id="output_guardrail_user",
            message="最近和人交流时有点紧张。",
            context={},
        )
    )

    assert "患有" not in response.response
    assert response.structured_data["output_guardrail_replaced"] is True
    assert response.trace.selected_agent == "output_guardrail"
    assert response.trace.output_guardrail_action == "replace"
    assert response.trace.output_guardrail_categories == [
        "diagnosis",
        "treatment_promise",
    ]


@pytest.mark.anyio
async def test_harness_persists_successful_repair_and_recheck_trace() -> None:
    client = SequenceLLMClient(
        [
            _invented_fact_assessment("我上周被同学打伤了"),
            json.dumps(
                {"repaired_response": "老师您好，我最近压力比较大，想找您聊一聊。"},
                ensure_ascii=False,
            ),
            '{"violations":[]}',
        ]
    )
    harness = AgentHarness(
        trace_logger=TraceLogger(repository=InMemoryTraceRepository()),
        safety_classifier=RuleBasedSafetyClassifier(),
        intent_router=RuleBasedIntentRouter(),
        skill_registry=SkillRegistry(executable_skills=(InventedFactSupportSkill(),)),
        output_guardrail=OutputGuardrail(client),
    )

    response = await harness.run(
        ChatRequest(
            user_id="output_repair_user",
            message="最近和人交流时有点紧张。",
            context={},
        )
    )

    assert "打伤" not in response.response
    assert response.trace.selected_agent == "output_guardrail_repair"
    assert response.trace.output_guardrail_action == "repair"
    assert response.trace.output_guardrail_repair_attempted is True
    assert response.trace.output_guardrail_repair_succeeded is True
    assert response.trace.output_guardrail_recheck_action == "allow"
