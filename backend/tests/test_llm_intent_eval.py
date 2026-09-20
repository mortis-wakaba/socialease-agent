"""Tests for the optional OpenAI-compatible intent benchmark."""

import json
from collections import Counter

import httpx
import pytest

from app.evals.llm_intent import (
    LLMIntentEvalCase,
    classification_metrics,
    run_llm_intent_benchmark,
)
from app.evals.loader import DATA_DIR, load_jsonl
from app.models import Intent


def test_llm_intent_datasets_are_balanced_disjoint_and_demo_only() -> None:
    dev_cases = load_jsonl(DATA_DIR / "intent_llm.jsonl", LLMIntentEvalCase)
    heldout_cases = load_jsonl(
        DATA_DIR / "intent_llm_heldout.jsonl", LLMIntentEvalCase
    )
    cases = [*dev_cases, *heldout_cases]
    dev_counts = Counter(case.expected_intent for case in dev_cases)
    heldout_counts = Counter(case.expected_intent for case in heldout_cases)

    assert len(dev_cases) == 45
    assert len(heldout_cases) == 63
    assert len(cases) == 108
    assert set(dev_counts.values()) == {5}
    assert set(heldout_counts.values()) == {7}
    assert all(case.split == "dev" for case in dev_cases)
    assert all(case.split == "heldout" for case in heldout_cases)
    assert len({case.id for case in cases}) == len(cases)
    assert len({case.message for case in cases}) == len(cases)
    assert all(case.demo for case in cases)
    assert all(case.expected_intent != Intent.CRISIS for case in cases)


def test_classification_metrics_count_invalid_prediction_as_miss() -> None:
    expected = [
        Intent.EMOTIONAL_SUPPORT,
        Intent.EMOTIONAL_SUPPORT,
        Intent.ROLEPLAY_PRACTICE,
    ]
    predicted = [Intent.EMOTIONAL_SUPPORT, None, Intent.EMOTIONAL_SUPPORT]

    accuracy, macro_f1, per_label = classification_metrics(expected, predicted)

    assert accuracy == pytest.approx(1 / 3)
    assert macro_f1 == pytest.approx(0.25)
    assert per_label == {
        "emotional_support": pytest.approx(0.5),
        "roleplay_practice": pytest.approx(0.0),
    }


@pytest.mark.anyio
async def test_benchmark_records_quality_latency_and_gpu_evidence() -> None:
    responses = iter(
        [
            {
                "intent": "roleplay_practice",
                "confidence": 0.92,
                "reason": "interactive rehearsal",
                "explicitly_requested": True,
                "parameters_complete": True,
                "secondary_intents": [],
            },
            {
                "intent": "emotional_support",
                "confidence": 0.88,
                "reason": "talk-only request",
                "explicitly_requested": True,
                "parameters_complete": True,
                "secondary_intents": [],
            },
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/generate":
            return httpx.Response(200, json={"done": True})
        if request.url.path == "/api/ps":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "name": "qwen3.5:4b",
                            "size": 100,
                            "size_vram": 100,
                        }
                    ]
                },
            )
        payload = json.loads(request.read())
        assert request.url.path == "/v1/chat/completions"
        response_format = payload["response_format"]
        assert response_format["type"] == "json_schema"
        assert response_format["json_schema"]["name"] == (
            "socialease_intent_prediction"
        )
        assert response_format["json_schema"]["strict"] is True
        assert response_format["json_schema"]["schema"]["type"] == "object"
        assert payload["max_tokens"] == 64
        assert "s" not in response_format["json_schema"]["schema"]["properties"]
        assert payload["reasoning_effort"] == "none"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": json.dumps(next(responses))}}
                ]
            },
        )

    cases = [
        LLMIntentEvalCase(
            id="roleplay",
            category="test",
            message="你扮演同学。",
            expected_intent=Intent.ROLEPLAY_PRACTICE,
            demo=True,
        ),
        LLMIntentEvalCase(
            id="support",
            category="test",
            message="我只想聊聊。",
            expected_intent=Intent.EMOTIONAL_SUPPORT,
            demo=True,
        ),
    ]
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
    ) as http_client:
        report = await run_llm_intent_benchmark(
            base_url="http://127.0.0.1:11434/v1",
            api_key="ollama",
            model="qwen3.5:4b",
            cases=cases,
            http_client=http_client,
        )

    assert report.raw_accuracy == 1.0
    assert report.routed_macro_f1 == 1.0
    assert report.json_valid_rate == 1.0
    assert report.schema_valid_rate == 1.0
    assert report.specialized_false_positive_rate == 0.0
    assert report.split_metrics["dev"].dataset_size == 2
    assert report.split_metrics["dev"].routed_accuracy == 1.0
    assert report.runtime.processor == "gpu"
    assert report.runtime.vram_fraction == 1.0
    assert all(case.latency_ms >= 0 for case in report.cases)
    serialized = report.model_dump_json()
    assert "你扮演同学" not in serialized
    assert "talk-only request" not in serialized
