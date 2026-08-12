"""Contracts for server-resolved natural-language memory correction."""

from datetime import datetime, timedelta, timezone
import hashlib
import json

import httpx
import pytest

from app.agents.memory_correction import MemoryCorrectionExtractor
from app.api import memory_center as memory_center_api
from app.main import app
from app.memory.settings_store import InMemoryUserMemorySettingsRepository
from app.models_long_term_memory import (
    EpisodicMemoryRecord,
    MemoryEvidenceType,
    MemoryRecordStatus,
    MemorySourceType,
    MemoryType,
)
from app.services.memory_correction_service import (
    MemoryCorrectionService,
    conservative_target_is_unambiguous,
)


NOW = datetime.now(timezone.utc)


class _JSONLLM:
    json_mode_enabled = True

    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.user_prompt = ""

    async def generate_text(self, **kwargs: object) -> str:
        raise AssertionError("JSON mode should be used")

    async def generate_json_text(self, **kwargs: object) -> str:
        self.user_prompt = str(kwargs["user_prompt"])
        return json.dumps(self.payload, ensure_ascii=False)


class _Repository:
    def __init__(self, memories: list[EpisodicMemoryRecord]) -> None:
        self.memories = memories
        self.calls: list[dict[str, object]] = []

    async def search_memory_fts_candidates(
        self,
        **kwargs: object,
    ) -> list[EpisodicMemoryRecord]:
        self.calls.append(kwargs)
        return self.memories


class _SupersessionService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def supersede(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        return object()


def _memory(
    memory_id: str,
    *,
    user_id: str = "owner",
    summary: str = "以前准备完整逐字稿。",
) -> EpisodicMemoryRecord:
    digest = hashlib.sha256(summary.encode()).hexdigest()
    return EpisodicMemoryRecord(
        memory_id=memory_id,
        user_id=user_id,
        memory_type=MemoryType.HELPFUL_STRATEGY,
        summary=summary,
        source_type=MemorySourceType.USER_CONFIRMED,
        evidence_type=MemoryEvidenceType.USER_CONFIRMED,
        confidence=1.0,
        status=MemoryRecordStatus.ACTIVE,
        occurred_at=NOW,
        created_at=NOW,
        updated_at=NOW,
        expires_at=NOW + timedelta(days=30),
        consent_version="practice-summary-v1",
        content_hash=digest,
        idempotency_key=(digest + digest)[:64],
        version=2,
    )


def _proposal(**updates: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "action": "propose",
        "target_query": "以前准备完整逐字稿",
        "replacement_summary": "现在只准备三个关键词提示。",
    }
    payload.update(updates)
    return payload


@pytest.mark.anyio
async def test_correction_returns_owner_scoped_candidates_without_mutation() -> None:
    repository = _Repository([_memory("memory-server-resolved")])
    service = MemoryCorrectionService(
        extractor=MemoryCorrectionExtractor(_JSONLLM(_proposal())),
        memory_repository=repository,  # type: ignore[arg-type]
        settings_repository=InMemoryUserMemorySettingsRepository(),
    )

    result = await service.resolve(
        user_id="owner",
        statement="我之前说错了，不是完整逐字稿，现在只准备三个关键词。",
    )

    assert result.status == "confirmation_required"
    assert len(result.candidates) == 1
    assert result.candidates[0].memory_id == "memory-server-resolved"
    assert result.candidates[0].expected_version == 2
    assert result.candidates[0].replacement_summary == "现在只准备三个关键词提示。"
    assert repository.calls[0]["user_id"] == "owner"
    assert repository.calls[0]["statuses"] == (MemoryRecordStatus.ACTIVE,)
    assert repository.calls[0]["limit"] == 3


def test_conservative_target_selector_requires_strong_query_coverage() -> None:
    assert conservative_target_is_unambiguous(
        target_query="小组发言准备完整逐字稿",
        candidate_summary="小组发言前准备完整逐字稿曾经有帮助。",
    )
    assert not conservative_target_is_unambiguous(
        target_query="宿舍关灯简短请求",
        candidate_summary="面试时准备三个关键词。",
    )


@pytest.mark.anyio
async def test_auto_update_supersedes_one_strong_unique_target() -> None:
    repository = _Repository(
        [
            _memory("memory-auto-update"),
            _memory("memory-weak-distractor", summary="面试时准备三个关键词。"),
        ]
    )
    supersession = _SupersessionService()
    service = MemoryCorrectionService(
        extractor=MemoryCorrectionExtractor(_JSONLLM(_proposal())),
        memory_repository=repository,  # type: ignore[arg-type]
        settings_repository=InMemoryUserMemorySettingsRepository(),
    )

    result = await service.auto_update(
        user_id="owner",
        statement="之前的完整逐字稿记错了，现在只准备三个关键词。",
        supersession_service=supersession,
    )

    assert result.status == "updated"
    assert result.memory_type is MemoryType.HELPFUL_STRATEGY
    assert supersession.calls == [
        {
            "user_id": "owner",
            "memory_id": "memory-auto-update",
            "summary": "现在只准备三个关键词提示。",
            "expected_version": 2,
        }
    ]


@pytest.mark.anyio
async def test_auto_update_abstains_when_target_is_ambiguous() -> None:
    repository = _Repository(
        [_memory("memory-one"), _memory("memory-two")]
    )
    supersession = _SupersessionService()
    service = MemoryCorrectionService(
        extractor=MemoryCorrectionExtractor(_JSONLLM(_proposal())),
        memory_repository=repository,  # type: ignore[arg-type]
        settings_repository=InMemoryUserMemorySettingsRepository(),
    )

    result = await service.auto_update(
        user_id="owner",
        statement="之前的完整逐字稿记错了，现在只准备三个关键词。",
        supersession_service=supersession,
    )

    assert result.status == "ambiguous_target"
    assert supersession.calls == []


@pytest.mark.anyio
async def test_auto_update_abstains_on_one_weak_target() -> None:
    repository = _Repository(
        [_memory("memory-weak", summary="面试时准备三个关键词。")]
    )
    supersession = _SupersessionService()
    service = MemoryCorrectionService(
        extractor=MemoryCorrectionExtractor(_JSONLLM(_proposal())),
        memory_repository=repository,  # type: ignore[arg-type]
        settings_repository=InMemoryUserMemorySettingsRepository(),
    )

    result = await service.auto_update(
        user_id="owner",
        statement="之前的完整逐字稿记错了，现在只准备三个关键词。",
        supersession_service=supersession,
    )

    assert result.status == "weak_target"
    assert supersession.calls == []


@pytest.mark.anyio
async def test_correction_model_cannot_submit_memory_id() -> None:
    repository = _Repository([_memory("memory-never-reached")])
    service = MemoryCorrectionService(
        extractor=MemoryCorrectionExtractor(
            _JSONLLM(_proposal(memory_id="memory-model-selected"))
        ),
        memory_repository=repository,  # type: ignore[arg-type]
        settings_repository=InMemoryUserMemorySettingsRepository(),
    )

    result = await service.resolve(
        user_id="owner",
        statement="把以前那条改一下。",
    )

    assert result.status == "resolution_failed"
    assert result.error_category == "SCHEMA_VALIDATION_ERROR"
    assert result.candidates == []
    assert repository.calls == []


@pytest.mark.anyio
async def test_non_correction_does_not_search_memory() -> None:
    repository = _Repository([_memory("memory-never-reached")])
    service = MemoryCorrectionService(
        extractor=MemoryCorrectionExtractor(
            _JSONLLM(
                {
                    "action": "skip",
                    "target_query": None,
                    "replacement_summary": None,
                }
            )
        ),
        memory_repository=repository,  # type: ignore[arg-type]
        settings_repository=InMemoryUserMemorySettingsRepository(),
    )

    result = await service.resolve(
        user_id="owner",
        statement="这次我想尝试只写三个关键词。",
    )

    assert result.status == "not_correction"
    assert repository.calls == []


@pytest.mark.anyio
async def test_unsafe_replacement_is_rejected_before_candidate_search() -> None:
    repository = _Repository([_memory("memory-never-reached")])
    service = MemoryCorrectionService(
        extractor=MemoryCorrectionExtractor(
            _JSONLLM(
                _proposal(
                    replacement_summary="我确诊了焦虑症，请保存电话13912345678。"
                )
            )
        ),
        memory_repository=repository,  # type: ignore[arg-type]
        settings_repository=InMemoryUserMemorySettingsRepository(),
    )

    result = await service.resolve(
        user_id="owner",
        statement="把之前的记录改成诊断和电话。",
    )

    assert result.status == "rejected"
    assert result.error_category == "diagnosis_or_trauma_inference_rejected"
    assert repository.calls == []


@pytest.mark.anyio
async def test_correction_api_returns_preview_without_calling_supersede(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _Repository([_memory("memory-api-preview")])
    service = MemoryCorrectionService(
        extractor=MemoryCorrectionExtractor(_JSONLLM(_proposal())),
        memory_repository=repository,  # type: ignore[arg-type]
        settings_repository=InMemoryUserMemorySettingsRepository(),
    )
    monkeypatch.setattr(memory_center_api, "memory_correction_service", service)
    monkeypatch.setenv("SOCIALEASE_AUTH_MODE", "demo")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/users/owner/memory-corrections/resolve",
            headers={"X-Demo-User-Id": "owner"},
            json={
                "statement": "之前不是完整逐字稿，现在只准备三个关键词。"
            },
        )

    assert response.status_code == 200
    assert response.json()["status"] == "confirmation_required"
    assert response.json()["candidates"][0]["memory_id"] == "memory-api-preview"
    assert response.json()["output_parser_version"] == "memory-correction-json-v1"
