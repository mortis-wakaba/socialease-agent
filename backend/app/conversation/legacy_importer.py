"""Idempotent read-only import of legacy domain sessions."""

from hashlib import sha256

from app.conversation.repository import ConversationRepository
from app.models_conversation import (
    Conversation,
    ConversationEvent,
    ConversationEventRole,
    ConversationEventType,
    ConversationImportSnapshot,
    ConversationStatus,
    ModuleLifecycleEventPayload,
    ModuleRun,
    ModuleRunStatus,
    ModuleType,
    RoleplayMessageEventPayload,
    RoleplayParameters,
)
from app.models_roleplay import (
    RoleplayMessageRole,
    RoleplaySession,
)


LEGACY_HISTORY_NOTICE_VERSION = "legacy-readonly-v1"


class LegacyConversationImporter:
    """Project legacy records into immutable unified timelines without dual writes."""

    def __init__(self, repository: ConversationRepository) -> None:
        self._repository = repository

    def import_roleplay_sessions(
        self,
        sessions: list[RoleplaySession],
    ) -> tuple[list[Conversation], int]:
        """Import each session once using deterministic identifiers."""
        imported: list[Conversation] = []
        created_count = 0
        for session in sessions:
            linked_conversation = (
                self._repository.get_conversation_for_domain_session(
                    user_id=session.user_id,
                    module_type=ModuleType.ROLEPLAY,
                    domain_session_id=session.session_id,
                )
            )
            if linked_conversation is not None:
                if (
                    linked_conversation.history_notice_version
                    == LEGACY_HISTORY_NOTICE_VERSION
                ):
                    imported.append(linked_conversation)
                continue
            snapshot = _roleplay_snapshot(session)
            existing = self._repository.get_for_user(
                snapshot.conversation.conversation_id,
                session.user_id,
            )
            if existing is not None:
                imported.append(existing)
                continue
            imported.append(self._repository.import_snapshot(snapshot))
            created_count += 1
        return imported, created_count


def _roleplay_snapshot(session: RoleplaySession) -> ConversationImportSnapshot:
    conversation_id = _stable_id(
        "legacy_rp_conversation",
        session.user_id,
        session.session_id,
    )
    module_run_id = _stable_id(
        "legacy_rp_module",
        session.user_id,
        session.session_id,
    )
    scenario = (
        session.scenario_spec.safe_summary
        if session.scenario_spec is not None
        else session.scenario or "角色扮演练习"
    )
    practice_goal = (
        session.scenario_spec.practice_goal
        if session.scenario_spec is not None
        else None
    )
    conversation = Conversation(
        conversation_id=conversation_id,
        user_id=session.user_id,
        title=f"旧练习 · {scenario}"[:160],
        status=ConversationStatus.ARCHIVED,
        history_notice_version=LEGACY_HISTORY_NOTICE_VERSION,
        created_at=session.created_at,
        updated_at=session.updated_at,
    )
    run = ModuleRun(
        module_run_id=module_run_id,
        conversation_id=conversation_id,
        user_id=session.user_id,
        module_type=ModuleType.ROLEPLAY,
        depth=1,
        status=ModuleRunStatus.COMPLETED,
        module_parameters=RoleplayParameters(
            scenario_description=scenario,
            practice_goal=practice_goal,
            difficulty=session.difficulty,
        ),
        domain_session_id=session.session_id,
        started_at=session.created_at,
        ended_at=session.updated_at,
    )
    lifecycle_payload = ModuleLifecycleEventPayload(
        module_run_id=module_run_id,
        module_type=ModuleType.ROLEPLAY,
    )
    events = [
        ConversationEvent(
            event_id=_stable_id("legacy_rp_event_start", conversation_id),
            conversation_id=conversation_id,
            user_id=session.user_id,
            sequence_no=1,
            event_type=ConversationEventType.MODULE_STARTED,
            role=ConversationEventRole.SYSTEM,
            content="已导入旧角色扮演记录（只读）。",
            structured_payload=lifecycle_payload,
            module_run_id=module_run_id,
            idempotency_key=f"legacy-roleplay:{session.session_id}:start",
            created_at=session.created_at,
        )
    ]
    for index, message in enumerate(session.messages, start=2):
        events.append(
            ConversationEvent(
                event_id=_stable_id(
                    "legacy_rp_event_message",
                    conversation_id,
                    str(index),
                ),
                conversation_id=conversation_id,
                user_id=session.user_id,
                sequence_no=index,
                event_type=ConversationEventType.MODULE_MESSAGE,
                role=_conversation_role(message.role),
                content=message.content,
                structured_payload=RoleplayMessageEventPayload(
                    session_id=session.session_id,
                ),
                module_run_id=module_run_id,
                idempotency_key=(
                    f"legacy-roleplay:{session.session_id}:message:{index - 1}"
                ),
                created_at=message.created_at,
            )
        )
    events.append(
        ConversationEvent(
            event_id=_stable_id("legacy_rp_event_end", conversation_id),
            conversation_id=conversation_id,
            user_id=session.user_id,
            sequence_no=len(events) + 1,
            event_type=ConversationEventType.MODULE_COMPLETED,
            role=ConversationEventRole.SYSTEM,
            content="旧角色扮演记录到此结束。",
            structured_payload=lifecycle_payload,
            module_run_id=module_run_id,
            idempotency_key=f"legacy-roleplay:{session.session_id}:completed",
            created_at=session.updated_at,
        )
    )
    return ConversationImportSnapshot(
        source_type="roleplay",
        source_id=session.session_id,
        conversation=conversation,
        events=events,
        module_runs=[run],
    )


def _conversation_role(role: RoleplayMessageRole) -> ConversationEventRole:
    if role == RoleplayMessageRole.USER:
        return ConversationEventRole.USER
    if role == RoleplayMessageRole.AGENT:
        return ConversationEventRole.ASSISTANT
    return ConversationEventRole.SYSTEM


def _stable_id(prefix: str, *parts: str) -> str:
    digest = sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:32]
    return f"{prefix}_{digest}"
