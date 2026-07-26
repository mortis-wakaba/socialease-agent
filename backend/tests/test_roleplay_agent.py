"""Unit tests for optional LLM-backed role-play turns."""

from datetime import datetime, timezone

import pytest

from app.agents.roleplay import RoleplayAgent
from app.models_knowledge import Citation
from app.models_roleplay import (
    RoleplayGuidance,
    RoleplayMessage,
    RoleplayMessageFeatures,
    RoleplayMessageRole,
    RoleplaySession,
)
from app.services.scenario_interpreter import ScenarioInterpreter


class FakeLLMClient:
    """Simple async fake returning a configured response."""

    def __init__(self, response: str = "LLM reply", should_fail: bool = False) -> None:
        self.response = response
        self.should_fail = should_fail
        self.calls: list[dict[str, str | float]] = []

    async def generate_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.2,
    ) -> str:
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "temperature": temperature,
            }
        )
        if self.should_fail:
            raise RuntimeError("provider unavailable")
        return self.response


def make_session() -> RoleplaySession:
    """Create a compact grounded role-play session fixture."""
    now = datetime.now(timezone.utc)
    return RoleplaySession(
        session_id="session",
        user_id="user",
        scenario=None,
        scenario_spec=ScenarioInterpreter().interpret(
            description="课堂上轮到我发言时，我想先说清楚核心观点"
        ),
        difficulty=3,
        messages=[
            RoleplayMessage(
                role=RoleplayMessageRole.AGENT,
                content="请先说出你的核心观点。",
                created_at=now,
            ),
            RoleplayMessage(
                role=RoleplayMessageRole.USER,
                content="我想先讲结论。",
                created_at=now,
            ),
        ],
        retrieved_guidance=RoleplayGuidance(
            query="课堂发言",
            answer="先说核心观点，再补充一个理由。",
            citations=[
                Citation(
                    title="Guide",
                    source_name="Project Authored",
                    source_type="project_authored",
                    snippet="先说核心观点。",
                )
            ],
            unknown=False,
            confidence=1.0,
        ),
        created_at=now,
        updated_at=now,
    )


def test_feedback_uses_privacy_safe_features_when_content_is_minimized() -> None:
    now = datetime.now(timezone.utc)
    session = make_session().model_copy(
        update={
            "messages": [
                RoleplayMessage(
                    role=RoleplayMessageRole.AGENT,
                    content="请先说出你的核心观点。",
                    created_at=now,
                ),
                RoleplayMessage(
                    role=RoleplayMessageRole.USER,
                    content="[raw roleplay message minimized by privacy policy]",
                    created_at=now,
                    features=RoleplayMessageFeatures(
                        char_count=32,
                        has_reason=True,
                        has_request=True,
                        has_boundary_statement=True,
                        has_empathy_marker=True,
                        has_specific_time_or_place=True,
                        sensitive_detected=["email"],
                    ),
                ),
            ]
        }
    )

    feedback = RoleplayAgent().feedback(session)

    assert feedback.clarity_score == 5
    assert feedback.naturalness_score == 3
    assert feedback.assertiveness_score == 4
    assert feedback.empathy_score == 3
    assert {item.dimension for item in feedback.rubric_breakdown} == {
        "clarity",
        "naturalness",
        "assertiveness",
        "empathy",
    }
    assert all(item.signals for item in feedback.rubric_breakdown)


def test_rubric_evaluator_distinguishes_rich_features_from_sparse_features() -> None:
    now = datetime.now(timezone.utc)
    base = make_session()
    sparse_session = base.model_copy(
        update={
            "messages": [
                RoleplayMessage(
                    role=RoleplayMessageRole.USER,
                    content="[raw roleplay message minimized by privacy policy]",
                    created_at=now,
                    features=RoleplayMessageFeatures(char_count=4),
                )
            ]
        }
    )
    rich_session = base.model_copy(
        update={
            "messages": [
                RoleplayMessage(
                    role=RoleplayMessageRole.USER,
                    content="[raw roleplay message minimized by privacy policy]",
                    created_at=now,
                    features=RoleplayMessageFeatures(
                        char_count=46,
                        sentence_count=2,
                        question_count=1,
                        first_person_count=2,
                        reason_marker_count=1,
                        request_marker_count=1,
                        boundary_marker_count=1,
                        empathy_marker_count=1,
                        politeness_marker_count=1,
                        specificity_marker_count=1,
                        collaborative_marker_count=1,
                        repair_marker_count=1,
                    ),
                )
            ]
        }
    )

    sparse_feedback = RoleplayAgent().feedback(sparse_session)
    rich_feedback = RoleplayAgent().feedback(rich_session)

    assert rich_feedback.clarity_score > sparse_feedback.clarity_score
    assert rich_feedback.naturalness_score > sparse_feedback.naturalness_score
    assert rich_feedback.assertiveness_score > sparse_feedback.assertiveness_score
    assert rich_feedback.empathy_score > sparse_feedback.empathy_score
    assert rich_feedback.rubric_breakdown[0].signals[0].present is True


@pytest.mark.anyio
async def test_next_turn_uses_llm_when_available() -> None:
    llm_client = FakeLLMClient(response="这很清楚。你能再补充一个理由吗？")
    agent = RoleplayAgent(llm_client=llm_client)

    response, llm_usage = await agent.next_turn(make_session(), "我想先讲结论。")

    assert response == "这很清楚。你能再补充一个理由吗？"
    assert llm_usage.used is True
    assert llm_usage.fallback_used is False
    assert llm_client.calls
    prompt = str(llm_client.calls[0]["user_prompt"])
    assert "课堂上轮到我发言" in prompt
    assert "先说核心观点" in prompt


@pytest.mark.anyio
async def test_next_turn_falls_back_without_llm() -> None:
    agent = RoleplayAgent()

    response, llm_usage = await agent.next_turn(make_session(), "我想先讲结论。")

    assert response == "我在听。你能先用一句话说出核心意思吗？ 你也可以把句子说完整一点。"
    assert llm_usage.used is False
    assert llm_usage.fallback_used is False


@pytest.mark.anyio
async def test_next_turn_falls_back_when_llm_fails() -> None:
    agent = RoleplayAgent(llm_client=FakeLLMClient(should_fail=True))

    response, llm_usage = await agent.next_turn(make_session(), "短句")

    assert response == "我在听。你能先用一句话说出核心意思吗？ 你也可以把句子说完整一点。"
    assert llm_usage.used is False
    assert llm_usage.fallback_used is True
    assert llm_usage.error_category == "TRANSIENT_PROVIDER_ERROR"
