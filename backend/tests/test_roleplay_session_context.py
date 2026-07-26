"""Deterministic tests for Redis-style role-play session memory and compaction."""

from datetime import datetime, timedelta, timezone
import os
from uuid import uuid4

import pytest

from app.agents.roleplay import RoleplayAgent
from app.db.factory import repository_factory
from app.memory.roleplay_compactor import RoleplayCompactor
from app.memory.roleplay_context_manager import RoleplayContextManager
from app.memory.session_context_settings import RoleplaySessionContextSettings
from app.memory.session_context_store import (
    DisabledSessionContextStore,
    InMemorySessionContextStore,
    RedisSessionContextStore,
)
from app.memory.thread_checkpoint_service import ThreadCheckpointService
from app.memory.token_estimator import (
    ConservativeTokenEstimator,
    create_token_estimator,
)
from app.models_memory import UserConsentState
from app.models_roleplay import (
    RoleplayFeedbackRequest,
    RoleplayMessageRequest,
    RoleplayMessageRole,
    RoleplayScenario,
    RoleplayStartRequest,
)
from app.models_session_context import (
    DurableCheckpointContext,
    RoleplayCompactState,
    SessionContextMessage,
)
from app.services.roleplay_service import RoleplayService


class MutableClock:
    """Controllable UTC clock for TTL tests."""

    def __init__(self) -> None:
        self.value = datetime(2026, 7, 17, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self.value

    def advance(self, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


class CapturingLLMClient:
    """Return deterministic turns while retaining prompts."""

    def __init__(self) -> None:
        self.user_prompts: list[str] = []

    async def generate_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.2,
    ) -> str:
        del system_prompt, temperature
        self.user_prompts.append(user_prompt)
        return "我理解你的意思。你希望接下来怎样具体表达？"


class CharacterTokenEstimator:
    """Exact deterministic estimator used to prove estimator injection."""

    backend_name = "character_test"
    model_name = "test-model"

    def count(self, text: str) -> int:
        return max(1, len(text))


class StaticLLMClient:
    """Return one fixed compaction candidate."""

    def __init__(self, response: str) -> None:
        self.response = response

    async def generate_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.2,
    ) -> str:
        del system_prompt, user_prompt, temperature
        return self.response


def _settings(
    *,
    recent_min: int = 4,
    recent_target: int = 6,
    recent_max: int = 8,
    max_tokens: int = 4000,
) -> RoleplaySessionContextSettings:
    return RoleplaySessionContextSettings(
        redis_url=None,
        active_ttl_seconds=3600,
        paused_ttl_seconds=86400,
        max_input_tokens=max_tokens,
        recent_min_messages=recent_min,
        recent_target_messages=recent_target,
        recent_max_messages=recent_max,
        compact_target_tokens=1000,
        compact_trigger_ratio=0.75,
        redis_socket_timeout_seconds=0.5,
    )


def _manager(
    store: InMemorySessionContextStore,
    *,
    settings: RoleplaySessionContextSettings | None = None,
) -> RoleplayContextManager:
    return RoleplayContextManager(
        store=store,
        settings=settings or _settings(),
        compactor=RoleplayCompactor(),
    )


@pytest.mark.anyio
async def test_in_memory_store_applies_ttl_and_user_isolation() -> None:
    clock = MutableClock()
    store = InMemorySessionContextStore(now=clock.now)
    await store.initialize(
        user_id="owner",
        session_id="session",
        opening_message="开始练习。",
        ttl_seconds=60,
    )
    await store.append_message(
        user_id="owner",
        session_id="session",
        role=RoleplayMessageRole.USER,
        content="我想练习表达边界。",
        ttl_seconds=60,
    )

    assert await store.get(user_id="other", session_id="session") is None
    assert len((await store.get(user_id="owner", session_id="session")).messages) == 2

    clock.advance(61)

    assert await store.get(user_id="owner", session_id="session") is None


@pytest.mark.anyio
async def test_delete_user_removes_only_that_users_session_contexts() -> None:
    store = InMemorySessionContextStore()
    for user_id, session_id in (
        ("owner", "first"),
        ("owner", "second"),
        ("other", "third"),
    ):
        await store.initialize(
            user_id=user_id,
            session_id=session_id,
            opening_message="开始练习。",
            ttl_seconds=60,
        )

    deleted = await store.delete_user(user_id="owner")

    assert deleted == 2
    assert await store.get(user_id="owner", session_id="first") is None
    assert await store.get(user_id="owner", session_id="second") is None
    assert await store.get(user_id="other", session_id="third") is not None


@pytest.mark.anyio
async def test_context_manager_compacts_old_messages_and_excludes_latest_user_duplicate() -> None:
    store = InMemorySessionContextStore()
    manager = _manager(
        store,
        settings=_settings(recent_min=2, recent_target=4, recent_max=5),
    )
    await manager.initialize(
        user_id="user",
        session_id="session",
        opening_message="opening",
    )
    for index in range(7):
        await manager.append(
            user_id="user",
            session_id="session",
            role=(
                RoleplayMessageRole.USER
                if index % 2 == 0
                else RoleplayMessageRole.AGENT
            ),
            content=f"turn-{index}",
        )
    latest = "latest-user-message"
    await manager.append(
        user_id="user",
        session_id="session",
        role=RoleplayMessageRole.USER,
        content=latest,
    )

    prompt_context = await manager.build_prompt_context(
        user_id="user",
        session_id="session",
        scenario="dorm_conflict",
        difficulty=3,
        guidance="使用清楚、具体的请求。",
        current_user_message=latest,
        fallback_recent_messages=[],
    )
    stored = await store.get(user_id="user", session_id="session")

    assert prompt_context.diagnostics.compaction_triggered is True
    assert prompt_context.diagnostics.compacted_message_count > 0
    assert prompt_context.compact_state is not None
    assert stored is not None
    assert len(stored.messages) == 4
    assert all(latest not in message for message in prompt_context.recent_messages)


@pytest.mark.anyio
async def test_dynamic_window_respects_application_token_budget() -> None:
    store = InMemorySessionContextStore()
    manager = _manager(
        store,
        settings=_settings(
            recent_min=2,
            recent_target=4,
            recent_max=8,
            max_tokens=1200,
        ),
    )
    await manager.initialize(
        user_id="budget-user",
        session_id="budget-session",
        opening_message="开始长对话练习。",
    )
    for index in range(8):
        await manager.append(
            user_id="budget-user",
            session_id="budget-session",
            role=(
                RoleplayMessageRole.USER
                if index % 2 == 0
                else RoleplayMessageRole.AGENT
            ),
            content=f"第{index}轮" + ("这是一段较长的角色扮演上下文。" * 45),
        )
    latest = "这是当前消息，不应在历史窗口中重复。"
    await manager.append(
        user_id="budget-user",
        session_id="budget-session",
        role=RoleplayMessageRole.USER,
        content=latest,
    )

    prompt_context = await manager.build_prompt_context(
        user_id="budget-user",
        session_id="budget-session",
        scenario="group_discussion",
        difficulty=3,
        guidance="先说观点，再补充一个理由。",
        current_user_message=latest,
        fallback_recent_messages=[],
    )

    assert prompt_context.diagnostics.estimated_input_tokens <= 1200
    assert prompt_context.diagnostics.budget_utilization <= 1.0
    assert len(prompt_context.recent_messages) <= 8
    assert all(latest not in message for message in prompt_context.recent_messages)


@pytest.mark.anyio
async def test_context_manager_uses_injected_token_estimator_and_reports_backend() -> None:
    store = InMemorySessionContextStore()
    settings = _settings(max_tokens=2000)
    estimator = CharacterTokenEstimator()
    manager = RoleplayContextManager(
        store=store,
        settings=settings,
        compactor=RoleplayCompactor(token_estimator=estimator),
        token_estimator=estimator,
    )
    await manager.initialize(
        user_id="token-user",
        session_id="token-session",
        opening_message="opening",
    )

    prompt_context = await manager.build_prompt_context(
        user_id="token-user",
        session_id="token-session",
        scenario="group_discussion",
        difficulty=2,
        guidance="表达一个观点。",
        current_user_message="我想先简单说一句。",
        fallback_recent_messages=[],
    )

    assert prompt_context.diagnostics.token_estimator_backend == "character_test"
    assert prompt_context.diagnostics.token_estimator_model == "test-model"


def test_unknown_model_tokenizer_falls_back_to_conservative_estimator() -> None:
    estimator = create_token_estimator(
        backend="auto",
        model_name="unknown-openai-compatible-model",
    )

    assert isinstance(estimator, ConservativeTokenEstimator)


@pytest.mark.anyio
async def test_pause_and_resume_apply_distinct_sliding_ttls() -> None:
    clock = MutableClock()
    store = InMemorySessionContextStore(now=clock.now)
    manager = _manager(store)
    await manager.initialize(
        user_id="ttl-user",
        session_id="ttl-session",
        opening_message="opening",
    )

    assert await manager.pause(user_id="ttl-user", session_id="ttl-session")
    clock.advance(3601)
    assert await store.get(user_id="ttl-user", session_id="ttl-session") is not None

    assert await manager.resume(user_id="ttl-user", session_id="ttl-session")
    clock.advance(3601)
    assert await store.get(user_id="ttl-user", session_id="ttl-session") is None


@pytest.mark.anyio
async def test_deterministic_compaction_redacts_sensitive_identifiers() -> None:
    compactor = RoleplayCompactor()
    now = datetime.now(timezone.utc)

    compact = await compactor.compact(
        previous=None,
        messages=[
            SessionContextMessage(
                role=RoleplayMessageRole.USER,
                content="请联系我 13912345678，邮箱 test@example.com。",
                created_at=now,
            ),
            SessionContextMessage(
                role=RoleplayMessageRole.AGENT,
                content="你希望怎样提出这个请求？",
                created_at=now,
            ),
        ],
        compacted_through_message=2,
    )
    serialized = compact.model_dump_json()

    assert "13912345678" not in serialized
    assert "test@example.com" not in serialized
    assert "[redacted:phone]" in serialized
    assert "[redacted:email]" in serialized


@pytest.mark.anyio
async def test_compaction_merges_previous_state_and_preserves_explicit_anchors() -> None:
    now = datetime.now(timezone.utc)
    previous = RoleplayCompactState(
        user_goal="练习清楚表达边界",
        current_topic="宿舍作息",
        expressed_needs=["希望晚上安静一些"],
        practiced_skills=["使用具体请求"],
        compacted_through_message=4,
        source_message_count=4,
        updated_at=now,
    )

    compact = await RoleplayCompactor().compact(
        previous=previous,
        messages=[
            SessionContextMessage(
                role=RoleplayMessageRole.USER,
                content="我想补充说明第二天需要早起。",
                created_at=now,
            )
        ],
        compacted_through_message=5,
    )

    assert compact.user_goal == "练习清楚表达边界"
    assert "希望晚上安静一些" in compact.expressed_needs
    assert "我想补充说明第二天需要早起。" in compact.expressed_needs
    assert compact.practiced_skills == ["使用具体请求"]
    assert compact.source_message_count == 5
    assert compact.version == 2


@pytest.mark.anyio
async def test_prohibited_llm_compaction_inference_uses_safe_fallback() -> None:
    response = (
        '{"user_goal":"用户确诊社交焦虑症","current_topic":"课堂",'
        '"expressed_needs":[],"attempted_phrases":[],'
        '"counterpart_position":null,"unresolved_question":null,'
        '"practiced_skills":[]}'
    )
    now = datetime.now(timezone.utc)
    compact = await RoleplayCompactor(
        llm_client=StaticLLMClient(response),
    ).compact(
        previous=None,
        messages=[
            SessionContextMessage(
                role=RoleplayMessageRole.USER,
                content="我想练习课堂发言。",
                created_at=now,
            )
        ],
        compacted_through_message=1,
    )

    assert "确诊" not in compact.model_dump_json()
    assert compact.user_goal == "我想练习课堂发言。"


@pytest.mark.anyio
async def test_compact_state_respects_its_own_token_budget() -> None:
    now = datetime.now(timezone.utc)
    estimator = ConservativeTokenEstimator()
    compact = await RoleplayCompactor(
        target_tokens=200,
        token_estimator=estimator,
    ).compact(
        previous=None,
        messages=[
            SessionContextMessage(
                role=RoleplayMessageRole.USER,
                content="我希望练习表达需求。" * 80,
                created_at=now,
            ),
            SessionContextMessage(
                role=RoleplayMessageRole.AGENT,
                content="你可以先描述情境，再说出一个具体请求。" * 40,
                created_at=now,
            ),
        ],
        compacted_through_message=2,
    )

    assert estimator.count(compact.model_dump_json()) <= 200


@pytest.mark.anyio
async def test_roleplay_uses_short_term_raw_history_while_database_stays_minimized() -> None:
    user_id = f"redis_context_{uuid4().hex}"
    client = CapturingLLMClient()
    store = InMemorySessionContextStore()
    manager = _manager(store)
    service = RoleplayService(
        agent=RoleplayAgent(llm_client=client),
        context_manager=manager,
    )
    start = await service.start_session(
        RoleplayStartRequest(
            user_id=user_id,
            scenario=RoleplayScenario.DORM_CONFLICT,
            difficulty=3,
        )
    )
    first_message = "我希望室友十二点以后把音乐声音调小。"
    await service.send_message(
        RoleplayMessageRequest(
            session_id=start.session.session_id,
            user_id=user_id,
            message=first_message,
        )
    )
    second = await service.send_message(
        RoleplayMessageRequest(
            session_id=start.session.session_id,
            user_id=user_id,
            message="但我担心这样说听起来太强硬。",
        )
    )

    assert first_message in client.user_prompts[-1]
    persisted_user_messages = [
        message
        for message in second.session.messages
        if message.role == RoleplayMessageRole.USER
    ]
    assert all(
        message.content == "[raw roleplay message minimized by privacy policy]"
        for message in persisted_user_messages
    )
    assert second.context_diagnostics["available"] is True
    assert second.context_diagnostics["backend"] == "memory_test_double"

    await service.get_feedback(
        RoleplayFeedbackRequest(
            session_id=start.session.session_id,
            user_id=user_id,
        )
    )
    assert await store.get(user_id=user_id, session_id=start.session.session_id) is None


@pytest.mark.anyio
async def test_roleplay_degrades_safely_when_redis_context_is_disabled() -> None:
    user_id = f"disabled_context_{uuid4().hex}"
    client = CapturingLLMClient()
    settings = _settings()
    manager = RoleplayContextManager(
        store=DisabledSessionContextStore(),
        settings=settings,
        compactor=RoleplayCompactor(target_tokens=settings.compact_target_tokens),
    )
    service = RoleplayService(
        agent=RoleplayAgent(llm_client=client),
        context_manager=manager,
    )
    start = await service.start_session(
        RoleplayStartRequest(
            user_id=user_id,
            scenario=RoleplayScenario.CLASSROOM_SPEECH,
            difficulty=2,
        )
    )

    response = await service.send_message(
        RoleplayMessageRequest(
            session_id=start.session.session_id,
            user_id=user_id,
            message="我想先说一个核心观点。",
        )
    )

    assert response.blocked is False
    assert response.context_diagnostics["available"] is False
    assert response.context_diagnostics["fallback_used"] is True
    assert response.context_diagnostics["error_category"] == (
        "SESSION_CONTEXT_UNAVAILABLE"
    )


@pytest.mark.anyio
async def test_fallback_history_and_active_memory_share_total_input_budget() -> None:
    settings = _settings(max_tokens=1200)
    estimator = CharacterTokenEstimator()
    manager = RoleplayContextManager(
        store=DisabledSessionContextStore(),
        settings=settings,
        compactor=RoleplayCompactor(token_estimator=estimator),
        token_estimator=estimator,
    )
    compact_state = RoleplayCompactState(
        current_topic="scenario:group_discussion;stage:paused",
        unresolved_question="恢复后继续表达一个核心观点。",
        version=2,
        updated_at=datetime.now(timezone.utc),
    )
    durable = DurableCheckpointContext(
        compact_state=compact_state,
        checkpoint_version=2,
        estimated_tokens=estimator.count(compact_state.model_dump_json()),
        token_budget=512,
    )

    prompt_context = await manager.build_prompt_context(
        user_id="budget-owner",
        session_id="budget-thread",
        scenario="group_discussion",
        difficulty=3,
        guidance="先说观点，再补充理由。",
        current_user_message="这次我想说得更简短。",
        fallback_recent_messages=["agent:" + ("较长历史。" * 400)] * 8,
        durable_checkpoint=durable,
    )

    rendered_tokens = estimator.count(
        compact_state.model_dump_json()
        + "\n".join(prompt_context.recent_messages)
    )
    assert prompt_context.diagnostics.estimated_input_tokens <= 1200
    assert rendered_tokens <= 1200
    assert prompt_context.diagnostics.durable_checkpoint_used is True


@pytest.mark.anyio
async def test_expired_redis_context_restores_exact_durable_checkpoint() -> None:
    user_id = f"durable_restore_{uuid4().hex}"
    repository_factory().user_memory_settings_repository().save(
        user_id=user_id,
        consent_state=UserConsentState(consent_to_practice_summary=True),
    )
    clock = MutableClock()
    store = InMemorySessionContextStore(now=clock.now)
    manager = _manager(store)
    client = CapturingLLMClient()
    checkpoint_service = ThreadCheckpointService(
        repository=repository_factory().long_term_memory_repository(),
        settings_repository=repository_factory().user_memory_settings_repository(),
        token_estimator=ConservativeTokenEstimator(),
        active_memory_token_budget=256,
    )
    service = RoleplayService(
        agent=RoleplayAgent(llm_client=client),
        context_manager=manager,
        checkpoint_service=checkpoint_service,
    )
    start = await service.start_session(
        RoleplayStartRequest(
            user_id=user_id,
            scenario=RoleplayScenario.CLASSROOM_SPEECH,
            difficulty=2,
        )
    )
    clock.advance(3601)
    assert await store.get(
        user_id=user_id,
        session_id=start.session.session_id,
    ) is None

    current_message = "不，我现在想先练习一个更简短的开场。"
    response = await service.send_message(
        RoleplayMessageRequest(
            session_id=start.session.session_id,
            user_id=user_id,
            message=current_message,
        )
    )
    prompt = client.user_prompts[-1]

    assert response.context_diagnostics["durable_checkpoint_used"] is True
    assert response.context_diagnostics["durable_checkpoint_version"] == 1
    assert (
        response.context_diagnostics["active_memory_estimated_tokens"]
        <= response.context_diagnostics["active_memory_token_budget"]
    )
    assert "stage:roleplay_started" in prompt
    assert "发送一轮练习回复" in prompt
    assert current_message in prompt
    assert "overrides any conflicting or stale detail" in prompt
    assert prompt.index("stage:roleplay_started") < prompt.index(current_message)


@pytest.mark.anyio
async def test_crisis_turn_clears_short_term_context_without_storing_raw_crisis_text() -> None:
    user_id = f"crisis_context_{uuid4().hex}"
    store = InMemorySessionContextStore()
    service = RoleplayService(context_manager=_manager(store))
    start = await service.start_session(
        RoleplayStartRequest(
            user_id=user_id,
            scenario=RoleplayScenario.CLASSROOM_SPEECH,
            difficulty=2,
        )
    )

    response = await service.send_message(
        RoleplayMessageRequest(
            session_id=start.session.session_id,
            user_id=user_id,
            message="我不想活了，可能会伤害自己。",
        )
    )

    assert response.blocked is True
    assert await store.get(user_id=user_id, session_id=start.session.session_id) is None
    assert all(
        "不想活" not in message.content for message in response.session.messages
    )


@pytest.mark.redis_integration
@pytest.mark.anyio
async def test_real_redis_store_round_trip_when_configured() -> None:
    redis_url = os.getenv("SOCIALEASE_TEST_REDIS_URL", "").strip()
    if not redis_url:
        pytest.skip("SOCIALEASE_TEST_REDIS_URL is required for Redis integration tests.")
    store = RedisSessionContextStore(redis_url=redis_url)
    if not await store.ping():
        pytest.skip("Configured Redis test server is unavailable.")
    user_id = f"redis-integration-{uuid4().hex}"
    session_id = uuid4().hex
    try:
        await store.initialize(
            user_id=user_id,
            session_id=session_id,
            opening_message="opening",
            ttl_seconds=60,
        )
        updated = await store.append_message(
            user_id=user_id,
            session_id=session_id,
            role=RoleplayMessageRole.USER,
            content="raw short-term context",
            ttl_seconds=60,
        )
        loaded = await store.get(user_id=user_id, session_id=session_id)

        assert loaded is not None
        assert loaded.version == updated.version
        assert loaded.messages[-1].content == "raw short-term context"
        assert await store.get(user_id="other", session_id=session_id) is None
    finally:
        await store.delete(user_id=user_id, session_id=session_id)
        await store.close()
