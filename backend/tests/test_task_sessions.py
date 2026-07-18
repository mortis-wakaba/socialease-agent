"""Deterministic tests for worksheet and support Redis-style task sessions."""

from datetime import datetime, timedelta, timezone
import os
from uuid import uuid4

import pytest

from app.agents.worksheet import WorksheetAgent
from app.db.repositories import InMemoryWorksheetRepository
from app.memory.task_state_store import InMemoryTaskStateStore, RedisTaskStateStore
from app.memory.worksheet_store import WorksheetStore
from app.models_support import SupportQueryRequest, SupportSearchContext
from app.models_worksheet import (
    WorksheetCreateRequest,
    WorksheetDraftContext,
    WorksheetSupplementRequest,
)
from app.services.support_resource_service import SupportResourceService
from app.services.errors import ServiceNotFoundError
from app.services.worksheet_service import WorksheetService


class MutableClock:
    """Controllable clock for shared TTL tests."""

    def __init__(self) -> None:
        self.value = datetime(2026, 7, 17, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self.value


class ConflictOnceTaskStore(InMemoryTaskStateStore):
    """Inject one real version advance before returning a CAS conflict."""

    def __init__(self) -> None:
        super().__init__()
        self.fail_next_compare = False
        self.compare_calls = 0

    async def compare_and_set(self, **kwargs: object) -> bool:
        self.compare_calls += 1
        if self.fail_next_compare:
            self.fail_next_compare = False
            user_id = str(kwargs["user_id"])
            task_id = str(kwargs["task_id"])
            expected_version = int(kwargs["expected_version"])
            ttl_seconds = int(kwargs["ttl_seconds"])
            current = await self.get(user_id=user_id, task_id=task_id)
            assert current is not None
            concurrent = current.model_copy(
                update={"version": expected_version + 1},
                deep=True,
            )
            assert await super().compare_and_set(
                user_id=user_id,
                task_id=task_id,
                state=concurrent,
                expected_version=expected_version,
                ttl_seconds=ttl_seconds,
            )
            return False
        return await super().compare_and_set(**kwargs)


@pytest.mark.anyio
async def test_shared_task_store_enforces_owner_ttl_and_cas() -> None:
    clock = MutableClock()
    store: InMemoryTaskStateStore[SupportSearchContext] = InMemoryTaskStateStore(now=clock.now)
    state = SupportSearchContext(
        user_id="owner",
        search_session_id="search",
        last_query="资料",
        version=1,
        updated_at=clock.now(),
    )
    await store.put(user_id="owner", task_id="search", state=state, ttl_seconds=60)

    assert await store.get(user_id="other", task_id="search") is None
    updated = state.model_copy(update={"version": 2, "last_query": "新资料"})
    assert await store.compare_and_set(
        user_id="owner", task_id="search", state=updated,
        expected_version=1, ttl_seconds=60
    )
    assert not await store.compare_and_set(
        user_id="owner", task_id="search", state=updated,
        expected_version=1, ttl_seconds=60
    )
    clock.value += timedelta(seconds=61)
    assert await store.get(user_id="owner", task_id="search") is None


def _worksheet_service(
    draft_store: InMemoryTaskStateStore[WorksheetDraftContext],
) -> WorksheetService:
    return WorksheetService(
        agent=WorksheetAgent(),
        store=WorksheetStore(repository=InMemoryWorksheetRepository()),
        draft_store=draft_store,
    )


@pytest.mark.anyio
async def test_worksheet_supplement_merges_into_same_draft_and_allows_targeted_correction() -> None:
    drafts: InMemoryTaskStateStore[WorksheetDraftContext] = InMemoryTaskStateStore()
    service = _worksheet_service(drafts)
    created = await service.create_worksheet(
        WorksheetCreateRequest(
            user_id="worksheet-owner",
            message="情境：明天课堂发言。自动想法：我会说错。情绪：焦虑。强度：7/10。",
        )
    )
    assert created.worksheet is not None
    worksheet_id = created.worksheet.worksheet_id

    supplemented = await service.supplement_worksheet(
        WorksheetSupplementRequest(
            worksheet_id=worksheet_id,
            user_id="worksheet-owner",
            message="反对证据：上次小组讨论时，同学认真听完了。",
        )
    )
    corrected = await service.supplement_worksheet(
        WorksheetSupplementRequest(
            worksheet_id=worksheet_id,
            user_id="worksheet-owner",
            message="更正，情绪：紧张。",
        )
    )

    assert supplemented.worksheet is not None
    assert supplemented.worksheet.worksheet_id == worksheet_id
    assert "认真听完" in (supplemented.worksheet.fields.evidence_against or "")
    assert corrected.worksheet is not None
    assert corrected.worksheet.fields.emotion == "紧张"
    assert corrected.worksheet.fields.situation == "明天课堂发言"


@pytest.mark.anyio
async def test_worksheet_draft_retries_one_cas_conflict() -> None:
    drafts = ConflictOnceTaskStore()
    service = _worksheet_service(drafts)
    created = await service.create_worksheet(
        WorksheetCreateRequest(user_id="owner", message="情境：课堂发言。")
    )
    assert created.worksheet is not None
    drafts.fail_next_compare = True

    await service.supplement_worksheet(
        WorksheetSupplementRequest(
            worksheet_id=created.worksheet.worksheet_id,
            user_id="owner",
            message="情绪：焦虑。",
        )
    )
    stored = await drafts.get(
        user_id="owner",
        task_id=created.worksheet.worksheet_id,
    )

    assert stored is not None
    assert stored.version == 3
    assert stored.recent_supplements[-1] == "情绪：焦虑。"
    assert drafts.compare_calls == 2


@pytest.mark.anyio
async def test_worksheet_supplement_hides_cross_user_resource() -> None:
    drafts: InMemoryTaskStateStore[WorksheetDraftContext] = InMemoryTaskStateStore()
    service = _worksheet_service(drafts)
    created = await service.create_worksheet(
        WorksheetCreateRequest(user_id="owner", message="情境：课堂发言。")
    )
    assert created.worksheet is not None

    with pytest.raises(ServiceNotFoundError, match="Worksheet not found"):
        await service.supplement_worksheet(
            WorksheetSupplementRequest(
                worksheet_id=created.worksheet.worksheet_id,
                user_id="other",
                message="情绪：焦虑。",
            )
        )


@pytest.mark.anyio
async def test_worksheet_crisis_clears_short_term_draft() -> None:
    drafts: InMemoryTaskStateStore[WorksheetDraftContext] = InMemoryTaskStateStore()
    service = _worksheet_service(drafts)
    created = await service.create_worksheet(
        WorksheetCreateRequest(user_id="crisis-owner", message="情境：课堂发言。")
    )
    assert created.worksheet is not None
    worksheet_id = created.worksheet.worksheet_id
    assert await drafts.get(user_id="crisis-owner", task_id=worksheet_id) is not None

    blocked = await service.supplement_worksheet(
        WorksheetSupplementRequest(
            worksheet_id=worksheet_id,
            user_id="crisis-owner",
            message="我不想活了，可能会伤害自己。",
        )
    )

    assert blocked.blocked is True
    assert await drafts.get(user_id="crisis-owner", task_id=worksheet_id) is None


@pytest.mark.anyio
async def test_support_search_session_resolves_ordinals_and_isolates_users() -> None:
    searches: InMemoryTaskStateStore[SupportSearchContext] = InMemoryTaskStateStore()
    service = SupportResourceService(search_store=searches)
    first = await service.query_resources(
        SupportQueryRequest(user_id="owner", query="社交焦虑 CBT 公开自助资料")
    )
    assert first.search_session_id
    assert len(first.citations) >= 2

    followup = await service.query_resources(
        SupportQueryRequest(
            user_id="owner",
            search_session_id=first.search_session_id,
            query="第二个主要讲什么？",
        )
    )
    cross_user = await service.query_resources(
        SupportQueryRequest(
            user_id="other",
            search_session_id=first.search_session_id,
            query="第二个主要讲什么？",
        )
    )

    assert followup.unknown is False
    assert followup.resolved_reference_index == 1
    assert followup.citations == [first.citations[1]]
    assert cross_user.unknown is True
    assert cross_user.citations == []


@pytest.mark.anyio
async def test_support_search_session_retries_one_cas_conflict() -> None:
    searches = ConflictOnceTaskStore()
    service = SupportResourceService(search_store=searches)
    first = await service.query_resources(
        SupportQueryRequest(user_id="owner", query="社交焦虑 CBT 公开自助资料")
    )
    assert first.search_session_id
    searches.fail_next_compare = True

    await service.query_resources(
        SupportQueryRequest(
            user_id="owner",
            search_session_id=first.search_session_id,
            query="第二个主要讲什么？",
        )
    )
    stored = await searches.get(
        user_id="owner",
        task_id=first.search_session_id,
    )

    assert stored is not None
    assert stored.version == 3
    assert stored.last_query == "第二个主要讲什么？"
    assert stored.selected_citation_index == 1
    assert searches.compare_calls == 2


@pytest.mark.redis_integration
@pytest.mark.anyio
async def test_shared_task_store_real_redis_round_trip_when_configured() -> None:
    redis_url = os.getenv("SOCIALEASE_TEST_REDIS_URL", "").strip()
    if not redis_url:
        pytest.skip("SOCIALEASE_TEST_REDIS_URL is required for Redis integration tests.")
    store = RedisTaskStateStore(
        redis_url=redis_url,
        namespace="task-state-integration",
        model_type=SupportSearchContext,
    )
    if not await store.ping():
        pytest.skip("Configured Redis test server is unavailable.")
    user_id = f"task-user-{uuid4().hex}"
    task_id = uuid4().hex
    state = SupportSearchContext(
        user_id=user_id,
        search_session_id=task_id,
        last_query="公开资源",
        version=1,
        updated_at=datetime.now(timezone.utc),
    )
    try:
        await store.put(user_id=user_id, task_id=task_id, state=state, ttl_seconds=60)
        loaded = await store.get(user_id=user_id, task_id=task_id)
        assert loaded is not None
        updated = loaded.model_copy(update={"version": 2, "last_query": "第二次查询"})
        assert await store.compare_and_set(
            user_id=user_id,
            task_id=task_id,
            state=updated,
            expected_version=1,
            ttl_seconds=60,
        )
        assert (await store.get(user_id=user_id, task_id=task_id)).last_query == "第二次查询"
    finally:
        await store.delete(user_id=user_id, task_id=task_id)
        await store.close()
