"""Unit tests for optional LLM-backed role-play turns."""

from datetime import datetime, timezone

import pytest

from app.agents.roleplay import RoleplayAgent
from app.models_knowledge import Citation
from app.models_roleplay import (
    RoleplayGuidance,
    RoleplayMessage,
    RoleplayMessageRole,
    RoleplayScenario,
    RoleplaySession,
)


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
        scenario=RoleplayScenario.CLASSROOM_SPEECH,
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
    assert "classroom_speech" in prompt
    assert "先说核心观点" in prompt


@pytest.mark.anyio
async def test_next_turn_falls_back_without_llm() -> None:
    agent = RoleplayAgent()

    response, llm_usage = await agent.next_turn(make_session(), "我想先讲结论。")

    assert response == "我听到了。你能用一句话先说出你的核心观点吗？ 你也可以把句子说完整一点。"
    assert llm_usage.used is False
    assert llm_usage.fallback_used is False


@pytest.mark.anyio
async def test_next_turn_falls_back_when_llm_fails() -> None:
    agent = RoleplayAgent(llm_client=FakeLLMClient(should_fail=True))

    response, llm_usage = await agent.next_turn(make_session(), "短句")

    assert response == "我听到了。你能用一句话先说出你的核心观点吗？ 你也可以把句子说完整一点。"
    assert llm_usage.used is False
    assert llm_usage.fallback_used is True
