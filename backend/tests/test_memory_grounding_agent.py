"""Contracts for typed production memory-grounding generation."""

import json

import pytest

from app.agents.memory_grounding import MemoryGroundingGenerationAgent


class _JSONLLM:
    json_mode_enabled = True

    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.requests: list[dict[str, object]] = []

    async def generate_text(self, **kwargs: object) -> str:
        raise AssertionError("JSON mode should be used")

    async def generate_json_text(self, **kwargs: object) -> str:
        self.requests.append(kwargs)
        return json.dumps(self.payload, ensure_ascii=False)


def _payload(**updates: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "response_mode": "historical_recall",
        "grounded_summary": "记录显示，先准备核心观点和简短理由更容易开口。",
        "evidence_numbers": [1],
        "uncertainty_note": None,
        "pause_supported": True,
        "needs_real_support": False,
        "privacy_candidates": [],
    }
    payload.update(updates)
    return payload


@pytest.mark.anyio
async def test_memory_grounding_returns_cited_historical_answer() -> None:
    llm = _JSONLLM(_payload())
    agent = MemoryGroundingGenerationAgent(llm_client=llm)

    response, metadata = await agent.respond(
        current_request="以前课堂发言什么准备方式有帮助？",
        evidence=["helpful_strategy: 先准备核心观点和简短理由。"],
    )

    assert "核心观点" in response
    assert metadata["fallback_used"] is False
    assert metadata["cited_evidence_count"] == 1
    assert metadata["output_parser_version"] == "memory-grounding-json-v1"
    assert '"number": 1' in str(llm.requests[0]["user_prompt"])


@pytest.mark.anyio
async def test_memory_grounding_fails_closed_on_unknown_evidence_number() -> None:
    agent = MemoryGroundingGenerationAgent(
        llm_client=_JSONLLM(_payload(evidence_numbers=[2]))
    )

    response, metadata = await agent.respond(
        current_request="以前课堂发言什么准备方式有帮助？",
        evidence=["helpful_strategy: 先准备核心观点。"],
    )

    assert "不足" in response
    assert metadata["fallback_used"] is True
    assert metadata["fallback_reason"] == "SCHEMA_VALIDATION_ERROR"


@pytest.mark.anyio
async def test_memory_grounding_rejects_medical_claims() -> None:
    agent = MemoryGroundingGenerationAgent(
        llm_client=_JSONLLM(_payload(grounded_summary="你已经确诊为焦虑症。"))
    )

    _response, metadata = await agent.respond(
        current_request="以前练习有什么记录？",
        evidence=["practice_experience: 完成过一次开场练习。"],
    )

    assert metadata["fallback_used"] is True
    assert metadata["fallback_reason"] == "OUTPUT_GUARDRAIL"
    assert metadata["llm_usage"]["error_category"] == (
        "PROHIBITED_SUPPORT_CONTENT"
    )


@pytest.mark.anyio
async def test_memory_grounding_allows_a_thought_grounded_in_selected_history() -> None:
    """Historical recall is grounded against evidence, not current-message spans."""
    agent = MemoryGroundingGenerationAgent(
        llm_client=_JSONLLM(
            _payload(
                grounded_summary=(
                    "记录显示，当时担心一开口大家会觉得自己的观点很差。"
                )
            )
        )
    )

    response, metadata = await agent.respond(
        current_request="我上次课堂发言前在担心什么？",
        evidence=[
            "practice_experience: 当时担心一开口大家会觉得自己的观点很差。"
        ],
    )

    assert "担心" in response
    assert metadata["fallback_used"] is False
    assert metadata["cited_evidence_count"] == 1
