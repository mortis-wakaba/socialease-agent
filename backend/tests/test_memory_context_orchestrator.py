"""Tests for stable and exact working-memory context orchestration."""

from datetime import UTC, datetime

import pytest

from app.memory.context_orchestrator import MemoryContextOrchestrator
from app.memory.settings_store import InMemoryUserMemorySettingsRepository
from app.models_memory import (
    PracticePreferences,
    UserConsentState,
    UserPracticeSummary,
)
from app.models_session_context import DurableCheckpointContext, RoleplayCompactState


NOW = datetime(2026, 8, 9, tzinfo=UTC)


class ProfileRepository:
    """Return one bounded stable practice summary."""

    async def get_summary(self, user_id: str) -> UserPracticeSummary:
        del user_id
        return UserPracticeSummary(
            recent_scenarios=["小组讨论"],
            latest_practice_at=NOW,
        )


@pytest.mark.anyio
async def test_orchestrator_auto_injects_only_consent_allowed_stable_memory() -> None:
    settings = InMemoryUserMemorySettingsRepository()
    await settings.save(
        user_id="owner",
        consent_state=UserConsentState(
            consent_to_practice_summary=True,
            consent_to_save_preferences=True,
        ),
        practice_preferences=PracticePreferences(
            preferred_feedback_style="gentle_specific"
        ),
    )
    orchestrator = MemoryContextOrchestrator(
        user_profile_repository=ProfileRepository(),  # type: ignore[arg-type]
        settings_repository=settings,
    )

    packet = await orchestrator.assemble(
        user_id="owner",
        skill_name="general_support_skill",
        current_request="今天想练习开场",
    )

    assert packet.stable_memory.values["preferred_feedback_style"] == (
        "gentle_specific"
    )
    assert packet.episodic_memories == []
    assert packet.working_memory is None


@pytest.mark.anyio
async def test_orchestrator_never_auto_searches_episodic_memory_from_query_text() -> None:
    settings = InMemoryUserMemorySettingsRepository()
    await settings.save(
        user_id="owner",
        consent_state=UserConsentState(consent_to_practice_summary=True),
    )
    orchestrator = MemoryContextOrchestrator(
        user_profile_repository=ProfileRepository(),  # type: ignore[arg-type]
        settings_repository=settings,
    )

    for message in (
        "继续上次那个练习",
        "我以前练过哪些表达方法？",
        "删除我的长期记忆",
    ):
        packet = await orchestrator.assemble(
            user_id="owner",
            skill_name="general_support_skill",
            current_request=message,
        )
        assert packet.episodic_memories == []


@pytest.mark.anyio
async def test_exact_application_checkpoint_is_restored_without_semantic_search() -> None:
    settings = InMemoryUserMemorySettingsRepository()
    checkpoint = DurableCheckpointContext(
        compact_state=RoleplayCompactState(
            current_topic="课堂发言开场",
            user_goal="先表达一个简短观点",
            updated_at=NOW,
        ),
        checkpoint_version=3,
        estimated_tokens=32,
        token_budget=128,
    )
    orchestrator = MemoryContextOrchestrator(
        user_profile_repository=ProfileRepository(),  # type: ignore[arg-type]
        settings_repository=settings,
    )

    packet = await orchestrator.assemble(
        user_id="owner",
        skill_name="roleplay_skill",
        current_request="任何下一轮消息",
        durable_checkpoint=checkpoint,
    )

    assert packet.working_memory == checkpoint
    assert packet.episodic_memories == []
    assert any(
        item.memory_layer.value == "working" and item.selected
        for item in packet.selections
    )
