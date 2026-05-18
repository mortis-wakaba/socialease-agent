"""Unit tests for optional LLM-backed worksheet extraction."""

import pytest

from app.agents.worksheet import WorksheetAgent


class FakeLLMClient:
    """Async fake returning configured extraction content."""

    def __init__(self, response: str, should_fail: bool = False) -> None:
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


@pytest.mark.anyio
async def test_create_fields_uses_valid_llm_json() -> None:
    agent = WorksheetAgent(
        llm_client=FakeLLMClient(
            '{"situation":"课堂发言","automatic_thought":"我会说错",'
            '"emotion":"焦虑","emotion_intensity":7,"evidence_for":null,'
            '"evidence_against":null,"alternative_thought":null,"next_action":null}'
        )
    )

    fields, missing, _, llm_usage = await agent.create_fields("我明天要课堂发言。")

    assert fields.situation == "课堂发言"
    assert fields.emotion_intensity == 7
    assert "evidence_for" in missing
    assert llm_usage.used is True
    assert llm_usage.fallback_used is False


@pytest.mark.anyio
async def test_create_fields_keeps_missing_values_null_in_llm_path() -> None:
    agent = WorksheetAgent(
        llm_client=FakeLLMClient(
            '{"situation":"明天课堂发言","automatic_thought":null,'
            '"emotion":"紧张","emotion_intensity":null,"evidence_for":null,'
            '"evidence_against":null,"alternative_thought":null,"next_action":null}'
        )
    )

    fields, missing, _, llm_usage = await agent.create_fields("我明天要课堂发言，有点紧张。")

    assert fields.automatic_thought is None
    assert fields.next_action is None
    assert "automatic_thought" in missing
    assert "next_action" in missing
    assert llm_usage.used is True


@pytest.mark.anyio
async def test_create_fields_falls_back_on_invalid_llm_json() -> None:
    agent = WorksheetAgent(llm_client=FakeLLMClient("not-json"))

    fields, missing, _, llm_usage = await agent.create_fields("情境：课堂发言。情绪：焦虑。强度：6。")

    assert fields.situation == "课堂发言"
    assert fields.emotion == "焦虑"
    assert fields.emotion_intensity == 6
    assert "automatic_thought" in missing
    assert llm_usage.used is False
    assert llm_usage.fallback_used is True


@pytest.mark.anyio
async def test_create_fields_falls_back_when_llm_fails() -> None:
    agent = WorksheetAgent(llm_client=FakeLLMClient("", should_fail=True))

    fields, _, _, llm_usage = await agent.create_fields("我明天要课堂发言，有点紧张。")

    assert fields.emotion == "紧张"
    assert llm_usage.used is False
    assert llm_usage.fallback_used is True
