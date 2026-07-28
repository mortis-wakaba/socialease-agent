"""Rule, privacy, and tenant-scope tests for the read-only Memory Doctor."""

from datetime import datetime, timedelta, timezone
import hashlib
from uuid import uuid4

import httpx
import pytest

from app.main import app
from app.models_long_term_memory import (
    EpisodicMemoryRecord,
    MemoryEvidenceType,
    MemoryPolicyReason,
    MemoryProposalStatus,
    MemoryRecordStatus,
    MemorySourceType,
    MemoryType,
    PendingMemoryProposalRecord,
    PracticeThreadCheckpoint,
    PracticeThreadStatus,
)
from app.models_memory import (
    AgentMemoryType,
    UserConsentState,
    UserMemorySettings,
)
from app.models_memory_doctor import (
    MemoryDoctorCheckStatus,
    MemoryDoctorIssueCode,
)
from app.services.memory_doctor_service import MemoryDoctorService


NOW = datetime(2026, 7, 26, 8, 0, tzinfo=timezone.utc)


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


class ScopedMemoryRepository:
    """Minimal repository fake that enforces the same owner boundary as SQL."""

    def __init__(
        self,
        *,
        memories: list[EpisodicMemoryRecord],
        checkpoints: list[PracticeThreadCheckpoint],
    ) -> None:
        self.memories = memories
        self.checkpoints = checkpoints
        self.requested_users: list[str] = []

    async def list_memories(
        self,
        user_id: str,
        *,
        statuses: tuple[MemoryRecordStatus, ...] | None = None,
        limit: int = 100,
    ) -> list[EpisodicMemoryRecord]:
        self.requested_users.append(user_id)
        records = [item for item in self.memories if item.user_id == user_id]
        if statuses is not None:
            records = [item for item in records if item.status in statuses]
        return records[:limit]

    async def list_checkpoints(
        self,
        user_id: str,
        *,
        limit: int = 100,
    ) -> list[PracticeThreadCheckpoint]:
        self.requested_users.append(user_id)
        return [
            item for item in self.checkpoints if item.user_id == user_id
        ][:limit]


class ScopedProposalRepository:
    """Owner-filtered pending proposal fake."""

    def __init__(self, proposals: list[PendingMemoryProposalRecord]) -> None:
        self.proposals = proposals
        self.requested_users: list[str] = []

    async def list_pending(
        self,
        user_id: str,
        *,
        limit: int = 100,
    ) -> list[PendingMemoryProposalRecord]:
        self.requested_users.append(user_id)
        return [
            item
            for item in self.proposals
            if item.user_id == user_id
            and item.status == MemoryProposalStatus.PENDING_CONFIRMATION
        ][:limit]


class ScopedSettingsRepository:
    """Return configured settings only for their exact owner."""

    def __init__(self, settings: dict[str, UserMemorySettings]) -> None:
        self.settings = settings
        self.requested_users: list[str] = []

    async def get(self, user_id: str) -> UserMemorySettings:
        self.requested_users.append(user_id)
        return self.settings.get(user_id, UserMemorySettings())


class CharacterEstimator:
    """Predictable estimator for deterministic budget checks."""

    backend_name = "test_characters"
    model_name = None

    def count(self, text: str) -> int:
        return max(1, len(text))


class EnabledEmbeddingInspector:
    """Simulate an adapter returning an unsafe raw index identifier."""

    enabled = True

    def orphan_subject_hashes(self, *, user_id: str) -> list[str]:
        assert user_id == "doctor-embedding"
        return ["raw-vector-record-id"]


@pytest.mark.anyio
async def test_doctor_detects_quality_issues_without_returning_memory_content() -> None:
    user_id = "doctor_owner"
    duplicate_summary = "小组讨论前先写一句开场，对表达观点有帮助。"
    conflict_summary = "小组讨论前不要写开场，这对表达观点没有帮助。"
    foreign_summary = "foreign secret memory body"
    stale_at = NOW - timedelta(days=220)
    future_at = NOW + timedelta(days=2)
    records = [
        _memory(
            user_id,
            "duplicate-a",
            duplicate_summary,
            occurred_at=stale_at,
            source_type=MemorySourceType.CHAT,
            source_id=None,
        ),
        _memory(
            user_id,
            "duplicate-b",
            duplicate_summary,
            occurred_at=NOW - timedelta(days=2),
        ),
        _memory(
            user_id,
            "conflict",
            conflict_summary,
            occurred_at=future_at,
            created_at=future_at,
            updated_at=future_at,
        ),
        _memory("other-user", "foreign", foreign_summary),
    ]
    checkpoints = [
        PracticeThreadCheckpoint(
            thread_id="thread-stale",
            user_id=user_id,
            current_stage="practice",
            current_scenario="group_discussion",
            unresolved_next_step="继续练习一个很长的下一步" * 20,
            status=PracticeThreadStatus.PAUSED,
            last_activity_at=stale_at,
            created_at=stale_at,
            updated_at=stale_at,
        ),
        PracticeThreadCheckpoint(
            thread_id="thread-foreign",
            user_id="other-user",
            status=PracticeThreadStatus.ACTIVE,
            last_activity_at=NOW,
            created_at=NOW,
            updated_at=NOW,
        ),
    ]
    proposals = [
        _proposal(user_id, "proposal-old", "待确认正文", NOW - timedelta(days=9)),
        _proposal("other-user", "proposal-foreign", foreign_summary, NOW),
    ]
    memory_repository = ScopedMemoryRepository(
        memories=records,
        checkpoints=checkpoints,
    )
    proposal_repository = ScopedProposalRepository(proposals)
    settings_repository = ScopedSettingsRepository(
        {
            user_id: UserMemorySettings(
                consent_state=UserConsentState(
                    consent_to_practice_summary=False
                ),
                disabled_memory_types=[AgentMemoryType.HELPFUL_STRATEGY],
            )
        }
    )
    service = MemoryDoctorService(
        memory_repository=memory_repository,
        proposal_repository=proposal_repository,
        settings_repository=settings_repository,
        token_estimator=CharacterEstimator(),
        active_memory_token_budget=128,
    )

    report = await service.diagnose(user_id, now=NOW)

    codes = {issue.code for issue in report.issues}
    assert {
        MemoryDoctorIssueCode.DUPLICATE_MEMORY,
        MemoryDoctorIssueCode.CONFLICTING_MEMORY,
        MemoryDoctorIssueCode.STALE_UNUSED_MEMORY,
        MemoryDoctorIssueCode.CONSENT_INACTIVE_MEMORY,
        MemoryDoctorIssueCode.TYPE_PERSONALIZATION_DISABLED,
        MemoryDoctorIssueCode.SOURCE_REFERENCE_MISSING,
        MemoryDoctorIssueCode.TIMESTAMP_INVALID,
        MemoryDoctorIssueCode.ACTIVE_MEMORY_OVER_BUDGET,
        MemoryDoctorIssueCode.STALE_CHECKPOINT,
        MemoryDoctorIssueCode.PENDING_PROPOSAL_AGED,
    }.issubset(codes)
    serialized = report.model_dump_json()
    for secret in (
        duplicate_summary,
        conflict_summary,
        foreign_summary,
        "待确认正文",
        "duplicate-a",
        "thread-stale",
        "proposal-old",
    ):
        assert secret not in serialized
    assert report.auto_fix_applied is False
    assert report.contains_memory_content is False
    embedding_check = next(
        item
        for item in report.checks
        if item.code == MemoryDoctorIssueCode.ORPHAN_EMBEDDING
    )
    assert embedding_check.status == MemoryDoctorCheckStatus.NOT_APPLICABLE
    assert embedding_check.detail_code == "embedding_index_disabled"
    assert memory_repository.requested_users == [user_id, user_id]
    assert proposal_repository.requested_users == [user_id]
    assert settings_repository.requested_users == [user_id]
    budget_issue = next(
        issue
        for issue in report.issues
        if issue.code == MemoryDoctorIssueCode.ACTIVE_MEMORY_OVER_BUDGET
    )
    assert budget_issue.metadata["budget_drop_count"] >= 1
    assert (
        budget_issue.metadata["selected_tokens"]
        <= budget_issue.metadata["token_budget"]
    )
    assert (
        budget_issue.metadata["estimated_tokens"]
        > budget_issue.metadata["selected_tokens"]
    )


@pytest.mark.anyio
async def test_doctor_clean_report_has_one_status_for_every_rule() -> None:
    user_id = "doctor-clean"
    service = MemoryDoctorService(
        memory_repository=ScopedMemoryRepository(memories=[], checkpoints=[]),
        proposal_repository=ScopedProposalRepository([]),
        settings_repository=ScopedSettingsRepository({}),
    )

    report = await service.diagnose(user_id, now=NOW)

    assert report.issues == []
    assert len(report.checks) == len(MemoryDoctorIssueCode)
    assert all(
        check.status == MemoryDoctorCheckStatus.PASSED
        for check in report.checks
        if check.code != MemoryDoctorIssueCode.ORPHAN_EMBEDDING
    )


@pytest.mark.anyio
async def test_doctor_hashes_embedding_adapter_identifiers_before_reporting() -> None:
    service = MemoryDoctorService(
        memory_repository=ScopedMemoryRepository(memories=[], checkpoints=[]),
        proposal_repository=ScopedProposalRepository([]),
        settings_repository=ScopedSettingsRepository({}),
        embedding_inspector=EnabledEmbeddingInspector(),
    )

    report = await service.diagnose("doctor-embedding", now=NOW)

    issue = next(
        item
        for item in report.issues
        if item.code == MemoryDoctorIssueCode.ORPHAN_EMBEDDING
    )
    assert issue.subject_id_hashes != ["raw-vector-record-id"]
    assert "raw-vector-record-id" not in report.model_dump_json()


def test_doctor_rejects_cross_user_preloaded_records() -> None:
    service = MemoryDoctorService(
        memory_repository=ScopedMemoryRepository(memories=[], checkpoints=[]),
        proposal_repository=ScopedProposalRepository([]),
        settings_repository=ScopedSettingsRepository({}),
    )

    with pytest.raises(ValueError, match="cross-user memory"):
        service.diagnose_loaded(
            user_id="doctor-owner",
            memories=[
                _memory(
                    "different-user",
                    "foreign-loaded",
                    "不应进入当前用户诊断的数据",
                )
            ],
            checkpoints=[],
            proposals=[],
            settings=UserMemorySettings(),
            now=NOW,
        )


@pytest.mark.anyio
async def test_doctor_api_is_owner_scoped_and_read_only(
    client: httpx.AsyncClient,
) -> None:
    user_id = f"doctor_api_{uuid4().hex}"

    forbidden = await client.get(
        f"/api/users/{user_id}/memory-doctor",
        headers={"X-Demo-User-Id": "different-user"},
    )
    response = await client.get(
        f"/api/users/{user_id}/memory-doctor",
        headers={"X-Demo-User-Id": user_id},
    )

    assert forbidden.status_code == 403
    assert response.status_code == 200
    payload = response.json()
    assert payload["user_id"] == user_id
    assert payload["auto_fix_applied"] is False
    assert payload["contains_memory_content"] is False
    assert payload["issues"] == []


def _memory(
    user_id: str,
    suffix: str,
    summary: str,
    *,
    occurred_at: datetime = NOW,
    created_at: datetime = NOW,
    updated_at: datetime = NOW,
    source_type: MemorySourceType = MemorySourceType.USER_CONFIRMED,
    source_id: str | None = None,
) -> EpisodicMemoryRecord:
    normalized = " ".join(summary.casefold().split())
    return EpisodicMemoryRecord(
        memory_id=f"memory-{suffix}",
        user_id=user_id,
        memory_type=MemoryType.HELPFUL_STRATEGY,
        summary=summary,
        scenario_type="group_discussion",
        source_type=source_type,
        source_id=source_id,
        evidence_type=MemoryEvidenceType.USER_CONFIRMED,
        confidence=0.9,
        status=MemoryRecordStatus.ACTIVE,
        occurred_at=occurred_at,
        created_at=created_at,
        updated_at=updated_at,
        expires_at=occurred_at + timedelta(days=365),
        consent_version="practice-summary-v1",
        content_hash=hashlib.sha256(normalized.encode()).hexdigest(),
        idempotency_key=hashlib.sha256(
            f"{user_id}:{suffix}".encode()
        ).hexdigest(),
    )


def _proposal(
    user_id: str,
    suffix: str,
    summary: str,
    created_at: datetime,
) -> PendingMemoryProposalRecord:
    return PendingMemoryProposalRecord(
        proposal_id=f"proposal-{suffix}",
        user_id=user_id,
        memory_type=MemoryType.SOCIAL_CONTEXT,
        summary=summary,
        scenario_type="group_discussion",
        source_type=MemorySourceType.CHAT,
        source_id="request-doctor-test",
        evidence_type=MemoryEvidenceType.EXPLICIT_USER_STATEMENT,
        confidence=0.9,
        occurred_at=created_at,
        status=MemoryProposalStatus.PENDING_CONFIRMATION,
        policy_reason=MemoryPolicyReason.SOCIAL_CONTEXT_CONFIRMATION_REQUIRED,
        content_hash=hashlib.sha256(summary.encode()).hexdigest(),
        idempotency_key=hashlib.sha256(
            f"{user_id}:{suffix}".encode()
        ).hexdigest(),
        created_at=created_at,
        updated_at=created_at,
        expires_at=created_at + timedelta(days=30),
    )
