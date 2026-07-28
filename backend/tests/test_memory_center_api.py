"""API and lifecycle tests for the owner-controlled Memory Center."""

from datetime import datetime, timedelta, timezone
import hashlib
from uuid import uuid4

import httpx
import pytest

from app.db.factory import repository_factory
from app.main import app
from app.memory.retriever import EpisodicMemoryRetriever
from app.models_long_term_memory import (
    EpisodicMemoryRecord,
    MemoryEvidenceType,
    MemoryPolicyReason,
    MemoryProposalStatus,
    MemoryRetrievalRequest,
    MemoryRecordStatus,
    MemorySourceType,
    MemoryType,
    PendingMemoryProposalRecord,
    PracticeThreadCheckpoint,
    PracticeThreadStatus,
)
from app.models_memory import UserConsentState


NOW = datetime.now(timezone.utc)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def client(monkeypatch: pytest.MonkeyPatch) -> httpx.AsyncClient:
    monkeypatch.setenv("SOCIALEASE_AUTH_MODE", "demo")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as async_client:
        yield async_client


def _headers(user_id: str) -> dict[str, str]:
    return {"X-Demo-User-Id": user_id}


def _memory(user_id: str, *, summary: str = "先写一句开场对表达练习有帮助。") -> EpisodicMemoryRecord:
    normalized = " ".join(summary.casefold().split())
    digest = hashlib.sha256(normalized.encode()).hexdigest()
    return EpisodicMemoryRecord(
        memory_id=f"memory_{uuid4().hex}",
        user_id=user_id,
        memory_type=MemoryType.HELPFUL_STRATEGY,
        summary=summary,
        scenario_type="group_discussion",
        source_type=MemorySourceType.USER_CONFIRMED,
        evidence_type=MemoryEvidenceType.USER_CONFIRMED,
        confidence=1.0,
        status=MemoryRecordStatus.ACTIVE,
        occurred_at=NOW,
        created_at=NOW,
        updated_at=NOW,
        expires_at=NOW + timedelta(days=365),
        consent_version="practice-summary-v1",
        content_hash=digest,
        idempotency_key=uuid4().hex * 2,
    )


def _proposal(user_id: str, *, summary: str) -> PendingMemoryProposalRecord:
    digest = hashlib.sha256(summary.encode()).hexdigest()
    return PendingMemoryProposalRecord(
        proposal_id=f"proposal_{uuid4().hex}",
        user_id=user_id,
        memory_type=MemoryType.SOCIAL_CONTEXT,
        summary=summary,
        scenario_type="group_discussion",
        source_type=MemorySourceType.CHAT,
        source_id="request-memory-center",
        evidence_type=MemoryEvidenceType.EXPLICIT_USER_STATEMENT,
        confidence=0.9,
        occurred_at=NOW,
        status=MemoryProposalStatus.PENDING_CONFIRMATION,
        policy_reason=MemoryPolicyReason.SOCIAL_CONTEXT_CONFIRMATION_REQUIRED,
        content_hash=digest,
        idempotency_key=uuid4().hex * 2,
        created_at=NOW,
        updated_at=NOW,
        expires_at=NOW + timedelta(days=30),
    )


@pytest.mark.anyio
async def test_memory_center_separates_layers_and_explains_history(
    client: httpx.AsyncClient,
) -> None:
    user_id = f"memory_center_snapshot_{uuid4().hex}"
    factory = repository_factory()
    await factory.user_memory_settings_repository().save(
        user_id=user_id,
        consent_state=UserConsentState(consent_to_practice_summary=True),
    )
    memory = _memory(user_id)
    await factory.long_term_memory_repository().create_memory(
        memory,
        reason_code="user_confirmed_proposal",
    )
    checkpoint = PracticeThreadCheckpoint(
        thread_id=f"thread_{uuid4().hex}",
        user_id=user_id,
        current_stage="paused",
        current_scenario="group_discussion",
        status=PracticeThreadStatus.PAUSED,
        last_activity_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    )
    await factory.long_term_memory_repository().save_checkpoint(
        checkpoint,
        expected_version=None,
        reason_code="practice_paused",
    )
    proposal = _proposal(user_id, summary="小组讨论通常安排在课程结束后。")
    await factory.memory_proposal_repository().save_pending(proposal)

    response = await client.get(
        f"/api/users/{user_id}/memories",
        headers=_headers(user_id),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["stable_memory"]["consent_state"][
        "consent_to_practice_summary"
    ] is True
    assert payload["active_threads"][0]["thread_id"] == checkpoint.thread_id
    assert payload["memories"][0]["summary"] == memory.summary
    assert payload["memories"][0]["saved_reason"] == "user_confirmed_proposal"
    assert payload["pending_proposals"][0]["proposal_id"] == proposal.proposal_id
    assert payload["doctor"]["contains_memory_content"] is False
    assert payload["doctor"]["auto_fix_applied"] is False
    assert "聊天历史" in payload["memory_history_distinction"]


@pytest.mark.anyio
async def test_memory_edit_archive_restore_delete_are_versioned_and_scoped(
    client: httpx.AsyncClient,
) -> None:
    user_id = f"memory_center_owner_{uuid4().hex}"
    other_id = f"memory_center_other_{uuid4().hex}"
    repository = repository_factory().long_term_memory_repository()
    memory = _memory(user_id)
    await repository.create_memory(memory, reason_code="user_confirmed_proposal")

    forbidden = await client.post(
        f"/api/users/{user_id}/memories/{memory.memory_id}/archive",
        headers=_headers(other_id),
        json={"expected_version": 1},
    )
    edited = await client.patch(
        f"/api/users/{user_id}/memories/{memory.memory_id}",
        headers=_headers(user_id),
        json={
            "summary": "先写下一个关键词，再尝试表达观点。",
            "expected_version": 1,
        },
    )
    archived = await client.post(
        f"/api/users/{user_id}/memories/{memory.memory_id}/archive",
        headers=_headers(user_id),
        json={"expected_version": 2},
    )
    stale = await client.post(
        f"/api/users/{user_id}/memories/{memory.memory_id}/restore",
        headers=_headers(user_id),
        json={"expected_version": 2},
    )
    restored = await client.post(
        f"/api/users/{user_id}/memories/{memory.memory_id}/restore",
        headers=_headers(user_id),
        json={"expected_version": 3},
    )
    deleted = await client.request(
        "DELETE",
        f"/api/users/{user_id}/memories/{memory.memory_id}",
        headers=_headers(user_id),
        json={"expected_version": 4},
    )

    assert forbidden.status_code == 403
    assert edited.status_code == 200
    assert edited.json()["memory"]["version"] == 2
    assert archived.json()["memory"]["status"] == "archived"
    assert stale.status_code == 409
    assert restored.json()["memory"]["status"] == "active"
    assert deleted.json()["deleted"] is True
    assert await repository.get_memory(memory.memory_id, user_id) is None
    serialized_events = str(
        [
            event.model_dump(mode="json")
            for event in await repository.list_events(
                user_id=user_id, subject_id=memory.memory_id
            )
        ]
    )
    assert "先写下一个关键词" not in serialized_events


@pytest.mark.anyio
async def test_memory_edit_rejects_unsafe_content(
    client: httpx.AsyncClient,
) -> None:
    user_id = f"memory_center_safe_edit_{uuid4().hex}"
    memory = _memory(user_id)
    await repository_factory().long_term_memory_repository().create_memory(
        memory,
        reason_code="user_confirmed_proposal",
    )

    response = await client.patch(
        f"/api/users/{user_id}/memories/{memory.memory_id}",
        headers=_headers(user_id),
        json={
            "summary": "我确诊了焦虑症，请长期保存电话 13912345678。",
            "expected_version": 1,
        },
    )

    assert response.status_code == 422
    unchanged = await repository_factory().long_term_memory_repository().get_memory(
        memory.memory_id,
        user_id,
    )
    assert unchanged is not None
    assert unchanged.summary == memory.summary


@pytest.mark.anyio
async def test_disabling_one_type_blocks_future_retrieval_without_deleting_record(
    client: httpx.AsyncClient,
) -> None:
    user_id = f"memory_center_disable_type_{uuid4().hex}"
    factory = repository_factory()
    settings_repository = factory.user_memory_settings_repository()
    await settings_repository.save(
        user_id=user_id,
        consent_state=UserConsentState(consent_to_practice_summary=True),
    )
    memory = _memory(user_id)
    memory_repository = factory.long_term_memory_repository()
    await memory_repository.create_memory(
        memory,
        reason_code="user_confirmed_proposal",
    )
    retriever = EpisodicMemoryRetriever(
        repository=memory_repository,
        settings_repository=settings_repository,
    )
    request = MemoryRetrievalRequest(
        user_id=user_id,
        query="先写一句开场对表达练习有帮助",
        allowed_memory_types=[MemoryType.HELPFUL_STRATEGY],
        scenario_type="group_discussion",
    )
    before = await retriever.retrieve(request, record_usage=False)

    response = await client.put(
        f"/api/users/{user_id}/memory/personalization/helpful_strategy",
        headers=_headers(user_id),
        json={"enabled": False},
    )
    result = await retriever.retrieve(request, record_usage=False)

    assert response.status_code == 200
    assert response.json()["disabled_memory_types"] == ["helpful_strategy"]
    assert [hit.memory_id for hit in before.hits] == [memory.memory_id]
    assert result.hits == []
    assert await memory_repository.get_memory(memory.memory_id, user_id) is not None


@pytest.mark.anyio
async def test_confirm_and_reject_proposals_erase_pending_bodies(
    client: httpx.AsyncClient,
) -> None:
    user_id = f"memory_center_proposal_{uuid4().hex}"
    factory = repository_factory()
    await factory.user_memory_settings_repository().save(
        user_id=user_id,
        consent_state=UserConsentState(consent_to_practice_summary=True),
    )
    confirmed = _proposal(user_id, summary="我更常在课程结束后参加小组讨论。")
    rejected = _proposal(user_id, summary="我通常在周末参加社团活动。")
    proposal_repository = factory.memory_proposal_repository()
    await proposal_repository.save_pending(confirmed)
    await proposal_repository.save_pending(rejected)

    confirm_response = await client.post(
        f"/api/users/{user_id}/memory-proposals/{confirmed.proposal_id}/confirm",
        headers=_headers(user_id),
        json={"expected_version": 1},
    )
    reject_response = await client.post(
        f"/api/users/{user_id}/memory-proposals/{rejected.proposal_id}/reject",
        headers=_headers(user_id),
        json={"expected_version": 1},
    )
    pending_response = await client.get(
        f"/api/users/{user_id}/memory-proposals",
        headers=_headers(user_id),
    )

    assert confirm_response.status_code == 200
    assert confirm_response.json()["status"] == "confirmed"
    assert confirm_response.json()["memory"]["summary"] == confirmed.summary
    assert reject_response.status_code == 200
    assert reject_response.json()["status"] == "rejected"
    assert pending_response.json()["proposals"] == []
    assert (
        await proposal_repository.get_for_user(confirmed.proposal_id, user_id) is None
    )
    assert (
        await proposal_repository.get_for_user(rejected.proposal_id, user_id) is None
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/api/users/{owner}/memories", None),
        ("GET", "/api/users/{owner}/memory-proposals", None),
        (
            "PATCH",
            "/api/users/{owner}/memories/memory-x",
            {"summary": "安全摘要", "expected_version": 1},
        ),
        (
            "POST",
            "/api/users/{owner}/memories/memory-x/archive",
            {"expected_version": 1},
        ),
        (
            "PUT",
            "/api/users/{owner}/memory/personalization/helpful_strategy",
            {"enabled": False},
        ),
        (
            "POST",
            "/api/users/{owner}/memories/memory-x/restore",
            {"expected_version": 1},
        ),
        (
            "DELETE",
            "/api/users/{owner}/memories/memory-x",
            {"expected_version": 1},
        ),
        (
            "POST",
            "/api/users/{owner}/memory-proposals/proposal-x/confirm",
            {"expected_version": 1},
        ),
        (
            "POST",
            "/api/users/{owner}/memory-proposals/proposal-x/reject",
            {"expected_version": 1},
        ),
    ],
)
async def test_every_memory_center_route_rejects_cross_user_access(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    body: dict[str, object] | None,
) -> None:
    owner = f"memory_route_owner_{uuid4().hex}"
    other = f"memory_route_other_{uuid4().hex}"

    response = await client.request(
        method,
        path.format(owner=owner),
        headers=_headers(other),
        json=body,
    )

    assert response.status_code == 403
