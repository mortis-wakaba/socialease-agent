"""Tests for idempotent read-only legacy conversation backfills."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.conversation.legacy_importer import LegacyConversationImporter
from app.conversation.repository import SQLiteConversationRepository
from app.db.repositories import SQLiteRoleplaySessionRepository
from app.models_conversation import (
    ConversationEventRole,
    ConversationEventType,
    ConversationStatus,
    ModuleRun,
    ModuleType,
    RoleplayParameters,
)
from app.models_roleplay import (
    RoleplayGuidance,
    RoleplayMessage,
    RoleplayMessageRole,
    RoleplaySession,
)


def test_roleplay_import_is_atomic_idempotent_and_read_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("SOCIALEASE_DB_PATH", str(tmp_path / "legacy.db"))
    monkeypatch.delenv("SOCIALEASE_DATABASE_URL", raising=False)
    monkeypatch.setenv("SOCIALEASE_AUTH_MODE", "demo")
    repository = SQLiteConversationRepository()
    importer = LegacyConversationImporter(repository)
    session = _roleplay_session()

    first, first_count = importer.import_roleplay_sessions([session])
    second, second_count = importer.import_roleplay_sessions([session])

    assert first_count == 1
    assert second_count == 0
    assert first == second
    conversation = first[0]
    assert conversation.status == ConversationStatus.ARCHIVED
    assert conversation.active_module_depth == 0

    events = repository.list_events(
        conversation_id=conversation.conversation_id,
        user_id=session.user_id,
        limit=20,
    ).items
    assert [event.sequence_no for event in events] == [1, 2, 3, 4]
    assert [event.event_type for event in events] == [
        ConversationEventType.MODULE_STARTED,
        ConversationEventType.MODULE_MESSAGE,
        ConversationEventType.MODULE_MESSAGE,
        ConversationEventType.MODULE_COMPLETED,
    ]
    assert events[1].role == ConversationEventRole.USER
    assert events[2].role == ConversationEventRole.ASSISTANT
    assert events[1].created_at == session.messages[0].created_at

    runs = repository.list_all_module_runs(
        conversation_id=conversation.conversation_id,
        user_id=session.user_id,
    )
    assert len(runs) == 1
    assert runs[0].domain_session_id == session.session_id

    with pytest.raises(LookupError, match="active conversation"):
        repository.append_event(
            conversation_id=conversation.conversation_id,
            user_id=session.user_id,
            event_type=ConversationEventType.USER_MESSAGE,
            role=ConversationEventRole.USER,
            content="不能继续写入旧记录",
            idempotency_key="legacy-readonly-check",
        )


def test_legacy_roleplay_source_supports_complete_batched_scans(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("SOCIALEASE_DB_PATH", str(tmp_path / "legacy-pages.db"))
    monkeypatch.delenv("SOCIALEASE_DATABASE_URL", raising=False)
    repository = SQLiteRoleplaySessionRepository()
    base = _roleplay_session()
    for index in range(3):
        repository.save(
            base.model_copy(
                update={
                    "session_id": f"legacy-session-{index}",
                    "updated_at": base.updated_at + timedelta(minutes=index),
                }
            )
        )

    first = repository.list_for_user("owner", limit=2)
    second = repository.list_for_user("owner", limit=2, offset=2)

    assert [session.session_id for session in first] == [
        "legacy-session-2",
        "legacy-session-1",
    ]
    assert [session.session_id for session in second] == ["legacy-session-0"]


def test_import_skips_roleplay_session_linked_to_unified_module(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("SOCIALEASE_DB_PATH", str(tmp_path / "linked.db"))
    monkeypatch.delenv("SOCIALEASE_DATABASE_URL", raising=False)
    monkeypatch.setenv("SOCIALEASE_AUTH_MODE", "demo")
    repository = SQLiteConversationRepository()
    conversation = repository.create(user_id="owner", title="统一会话")
    session = _roleplay_session()
    repository.create_module_run(
        ModuleRun(
            module_run_id="unified-module-run",
            conversation_id=conversation.conversation_id,
            user_id=session.user_id,
            module_type=ModuleType.ROLEPLAY,
            depth=1,
            module_parameters=RoleplayParameters(
                scenario_description="小组讨论",
            ),
            domain_session_id=session.session_id,
            started_at=session.created_at,
        )
    )

    imported, imported_count = LegacyConversationImporter(
        repository
    ).import_roleplay_sessions([session])

    assert imported == []
    assert imported_count == 0
    conversations = repository.list_for_user("owner", limit=20).items
    assert [item.conversation_id for item in conversations] == [
        conversation.conversation_id
    ]


def _roleplay_session() -> RoleplaySession:
    created_at = datetime(2026, 7, 1, 8, 0, tzinfo=UTC)
    return RoleplaySession(
        session_id="legacy-session-1",
        user_id="owner",
        scenario="小组讨论",
        difficulty=2,
        messages=[
            RoleplayMessage(
                role=RoleplayMessageRole.USER,
                content="我想补充一个看法。",
                created_at=created_at + timedelta(minutes=1),
            ),
            RoleplayMessage(
                role=RoleplayMessageRole.AGENT,
                content="好，你可以继续说明理由。",
                created_at=created_at + timedelta(minutes=2),
            ),
        ],
        retrieved_guidance=RoleplayGuidance(
            query="小组讨论",
            answer="demo guidance",
            unknown=False,
            confidence=0.8,
        ),
        created_at=created_at,
        updated_at=created_at + timedelta(minutes=3),
    )
