"""PostgreSQL adapter for ordered, encrypted-capable conversation timelines."""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.engine import Engine, RowMapping

from app.conversation.content_protector import (
    ConversationContentProtector,
    ProtectedContent,
    configured_content_protector,
)
from app.conversation.repository import (
    ConversationConcurrencyError,
    _decode_conversation_cursor,
    _decode_event_cursor,
    _encode_conversation_cursor,
    _encode_event_cursor,
    _event_associated_data,
    _validate_idempotent_event,
    _validated_limit,
)
from app.db.postgres.engine import shared_postgres_engine
from app.db.config import database_settings
from app.models_conversation import (
    HISTORY_NOTICE_VERSION,
    Conversation,
    ConversationEvent,
    ConversationEventPage,
    ConversationEventPayload,
    ConversationEventRole,
    ConversationEventType,
    ConversationPage,
    ConversationStatus,
    ModuleProposal,
    ModuleProposalStatus,
    ModuleRun,
    ModuleRunStatus,
)


class PostgresConversationRepository:
    """PostgreSQL repository using row locks for per-conversation ordering."""

    def __init__(
        self,
        *,
        database_url: str | None = None,
        engine: Engine | None = None,
        protector: ConversationContentProtector | None = None,
    ) -> None:
        resolved_url = database_url or database_settings().database_url
        self.engine = engine or shared_postgres_engine(resolved_url)
        self._protector = protector or configured_content_protector()

    def create(
        self,
        *,
        user_id: str,
        title: str,
        history_notice_version: str = HISTORY_NOTICE_VERSION,
    ) -> Conversation:
        """Create a persistent user-owned conversation."""
        now = datetime.now(UTC)
        conversation = Conversation(
            conversation_id=uuid4().hex,
            user_id=user_id,
            title=title,
            history_notice_version=history_notice_version,
            created_at=now,
            updated_at=now,
        )
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    """INSERT INTO conversations
                    (conversation_id, user_id, title, status,
                     active_module_depth, version, history_notice_version,
                     created_at, updated_at)
                    VALUES
                    (:conversation_id, :user_id, :title, :status,
                     :active_module_depth, :version, :history_notice_version,
                     :created_at, :updated_at)"""
                ),
                _conversation_params(conversation),
            )
        return conversation

    def get_for_user(
        self,
        conversation_id: str,
        user_id: str,
    ) -> Conversation | None:
        """Return an undeleted conversation only to its owner."""
        with self.engine.connect() as connection:
            row = connection.execute(
                text(
                    """SELECT * FROM conversations
                    WHERE conversation_id = :conversation_id
                      AND user_id = :user_id
                      AND status != :deleted"""
                ),
                {
                    "conversation_id": conversation_id,
                    "user_id": user_id,
                    "deleted": ConversationStatus.DELETED.value,
                },
            ).mappings().first()
        return _conversation_from_row(row) if row else None

    def list_for_user(
        self,
        user_id: str,
        *,
        cursor: str | None = None,
        limit: int = 20,
    ) -> ConversationPage:
        """List owned conversations using an opaque stable cursor."""
        limit = _validated_limit(limit, maximum=100)
        cursor_values = _decode_conversation_cursor(cursor) if cursor else None
        cursor_clause = ""
        params: dict[str, object] = {
            "user_id": user_id,
            "deleted": ConversationStatus.DELETED.value,
            "limit": limit + 1,
        }
        if cursor_values:
            cursor_clause = (
                "AND (updated_at < :cursor_updated_at OR "
                "(updated_at = :cursor_updated_at "
                "AND conversation_id < :cursor_id))"
            )
            params.update(
                {
                    "cursor_updated_at": datetime.fromisoformat(cursor_values[0]),
                    "cursor_id": cursor_values[1],
                }
            )
        with self.engine.connect() as connection:
            rows = connection.execute(
                text(
                    f"""SELECT * FROM conversations
                    WHERE user_id = :user_id AND status != :deleted
                    {cursor_clause}
                    ORDER BY updated_at DESC, conversation_id DESC
                    LIMIT :limit"""
                ),
                params,
            ).mappings().all()
        has_more = len(rows) > limit
        page_rows = rows[:limit]
        items = [_conversation_from_row(row) for row in page_rows]
        next_cursor = None
        if has_more and items:
            last = items[-1]
            next_cursor = _encode_conversation_cursor(
                last.updated_at.isoformat(),
                last.conversation_id,
            )
        return ConversationPage(items=items, next_cursor=next_cursor)

    def update_metadata(
        self,
        *,
        conversation_id: str,
        user_id: str,
        expected_version: int,
        title: str | None = None,
        status: ConversationStatus | None = None,
        active_module_depth: int | None = None,
    ) -> Conversation | None:
        """Optimistically update conversation metadata."""
        assignments = ["version = version + 1", "updated_at = :updated_at"]
        params: dict[str, object] = {
            "updated_at": datetime.now(UTC),
            "conversation_id": conversation_id,
            "user_id": user_id,
            "expected_version": expected_version,
            "deleted": ConversationStatus.DELETED.value,
        }
        for column, value in (
            ("title", title),
            ("status", status.value if status else None),
            ("active_module_depth", active_module_depth),
        ):
            if value is not None:
                assignments.append(f"{column} = :{column}")
                params[column] = value
        with self.engine.begin() as connection:
            result = connection.execute(
                text(
                    f"""UPDATE conversations
                    SET {", ".join(assignments)}
                    WHERE conversation_id = :conversation_id
                      AND user_id = :user_id
                      AND version = :expected_version
                      AND status != :deleted"""
                ),
                params,
            )
        if result.rowcount == 0:
            if self.get_for_user(conversation_id, user_id) is None:
                return None
            raise ConversationConcurrencyError("conversation version changed")
        return self.get_for_user(conversation_id, user_id)

    def append_event(
        self,
        *,
        conversation_id: str,
        user_id: str,
        event_type: ConversationEventType,
        role: ConversationEventRole,
        content: str,
        idempotency_key: str,
        structured_payload: ConversationEventPayload | None = None,
        module_run_id: str | None = None,
        parent_module_run_id: str | None = None,
    ) -> ConversationEvent:
        """Lock one conversation and append the next ordered event."""
        with self.engine.begin() as connection:
            owner = connection.execute(
                text(
                    """SELECT conversation_id FROM conversations
                    WHERE conversation_id = :conversation_id
                      AND user_id = :user_id
                      AND status = :active
                    FOR UPDATE"""
                ),
                {
                    "conversation_id": conversation_id,
                    "user_id": user_id,
                    "active": ConversationStatus.ACTIVE.value,
                },
            ).mappings().first()
            if owner is None:
                raise LookupError("active conversation not found")

            existing_row = connection.execute(
                text(
                    """SELECT * FROM conversation_events
                    WHERE conversation_id = :conversation_id
                      AND user_id = :user_id
                      AND idempotency_key = :idempotency_key"""
                ),
                {
                    "conversation_id": conversation_id,
                    "user_id": user_id,
                    "idempotency_key": idempotency_key,
                },
            ).mappings().first()
            if existing_row:
                existing = self._event_from_row(existing_row)
                _validate_idempotent_event(
                    existing=existing,
                    event_type=event_type,
                    role=role,
                    content=content,
                    structured_payload=structured_payload,
                    module_run_id=module_run_id,
                    parent_module_run_id=parent_module_run_id,
                )
                return existing

            sequence_no = connection.execute(
                text(
                    """SELECT COALESCE(MAX(sequence_no), 0) + 1 AS next_sequence
                    FROM conversation_events
                    WHERE conversation_id = :conversation_id"""
                ),
                {"conversation_id": conversation_id},
            ).scalar_one()
            now = datetime.now(UTC)
            event_id = uuid4().hex
            protected = self._protector.protect(
                content,
                associated_data=_event_associated_data(
                    event_id,
                    conversation_id,
                    user_id,
                    sequence_no,
                ),
            )
            event = ConversationEvent(
                event_id=event_id,
                conversation_id=conversation_id,
                user_id=user_id,
                sequence_no=sequence_no,
                event_type=event_type,
                role=role,
                content=content,
                structured_payload=structured_payload,
                module_run_id=module_run_id,
                parent_module_run_id=parent_module_run_id,
                idempotency_key=idempotency_key,
                created_at=now,
            )
            connection.execute(
                text(
                    """INSERT INTO conversation_events
                    (event_id, conversation_id, user_id, sequence_no,
                     event_type, role, content_plaintext, content_ciphertext,
                     content_nonce, content_key_version, structured_payload,
                     module_run_id, parent_module_run_id, idempotency_key,
                     created_at)
                    VALUES
                    (:event_id, :conversation_id, :user_id, :sequence_no,
                     :event_type, :role, :content_plaintext,
                     :content_ciphertext, :content_nonce, :content_key_version,
                     CAST(:structured_payload AS jsonb), :module_run_id,
                     :parent_module_run_id, :idempotency_key, :created_at)"""
                ),
                _event_params(event, protected),
            )
            connection.execute(
                text(
                    """UPDATE conversations
                    SET version = version + 1, updated_at = :updated_at
                    WHERE conversation_id = :conversation_id
                      AND user_id = :user_id"""
                ),
                {
                    "updated_at": now,
                    "conversation_id": conversation_id,
                    "user_id": user_id,
                },
            )
        return event

    def list_events(
        self,
        *,
        conversation_id: str,
        user_id: str,
        cursor: str | None = None,
        limit: int = 50,
    ) -> ConversationEventPage:
        """List owner-scoped events in ascending timeline order."""
        limit = _validated_limit(limit, maximum=200)
        after_sequence = _decode_event_cursor(cursor) if cursor else 0
        with self.engine.connect() as connection:
            rows = connection.execute(
                text(
                    """SELECT events.*
                    FROM conversation_events AS events
                    JOIN conversations AS conversations
                      ON conversations.conversation_id = events.conversation_id
                    WHERE events.conversation_id = :conversation_id
                      AND events.user_id = :user_id
                      AND conversations.user_id = :user_id
                      AND conversations.status != :deleted
                      AND events.sequence_no > :after_sequence
                    ORDER BY events.sequence_no ASC
                    LIMIT :limit"""
                ),
                {
                    "conversation_id": conversation_id,
                    "user_id": user_id,
                    "deleted": ConversationStatus.DELETED.value,
                    "after_sequence": after_sequence,
                    "limit": limit + 1,
                },
            ).mappings().all()
        has_more = len(rows) > limit
        items = [self._event_from_row(row) for row in rows[:limit]]
        next_cursor = (
            _encode_event_cursor(items[-1].sequence_no)
            if has_more and items
            else None
        )
        return ConversationEventPage(items=items, next_cursor=next_cursor)

    def save_proposal(self, proposal: ModuleProposal) -> ModuleProposal:
        """Persist a proposal or return its request-hash replay."""
        with self.engine.begin() as connection:
            row = connection.execute(
                text(
                    """INSERT INTO conversation_module_proposals
                    (proposal_id, conversation_id, user_id, proposed_module,
                     reason_code, status, request_hash, payload, expires_at,
                     created_at)
                    SELECT :proposal_id, :conversation_id, :user_id,
                     :proposed_module, :reason_code, :status, :request_hash,
                     CAST(:payload AS jsonb), :expires_at, :created_at
                    WHERE EXISTS (
                        SELECT 1 FROM conversations
                        WHERE conversation_id = :conversation_id
                          AND user_id = :user_id AND status = :active
                    )
                    ON CONFLICT (conversation_id, request_hash) DO NOTHING
                    RETURNING payload"""
                ),
                {**_proposal_params(proposal), "active": "active"},
            ).mappings().first()
            if row:
                return ModuleProposal.model_validate(row["payload"])
            existing = connection.execute(
                text(
                    """SELECT payload FROM conversation_module_proposals
                    WHERE conversation_id = :conversation_id
                      AND user_id = :user_id
                      AND request_hash = :request_hash"""
                ),
                {
                    "conversation_id": proposal.conversation_id,
                    "user_id": proposal.user_id,
                    "request_hash": proposal.request_hash,
                },
            ).mappings().first()
        if existing:
            return ModuleProposal.model_validate(existing["payload"])
        raise LookupError("active conversation not found")

    def get_proposal_for_user(
        self,
        *,
        proposal_id: str,
        conversation_id: str,
        user_id: str,
    ) -> ModuleProposal | None:
        """Return one proposal only inside its complete owner scope."""
        with self.engine.connect() as connection:
            row = connection.execute(
                text(
                    """SELECT payload FROM conversation_module_proposals
                    WHERE proposal_id = :proposal_id
                      AND conversation_id = :conversation_id
                      AND user_id = :user_id"""
                ),
                {
                    "proposal_id": proposal_id,
                    "conversation_id": conversation_id,
                    "user_id": user_id,
                },
            ).mappings().first()
        return ModuleProposal.model_validate(row["payload"]) if row else None

    def get_proposal_by_request(
        self,
        *,
        conversation_id: str,
        user_id: str,
        request_hash: str,
    ) -> ModuleProposal | None:
        """Return a deduplicated proposal by scoped request hash."""
        with self.engine.connect() as connection:
            row = connection.execute(
                text(
                    """SELECT payload FROM conversation_module_proposals
                    WHERE conversation_id = :conversation_id
                      AND user_id = :user_id
                      AND request_hash = :request_hash"""
                ),
                {
                    "conversation_id": conversation_id,
                    "user_id": user_id,
                    "request_hash": request_hash,
                },
            ).mappings().first()
        return ModuleProposal.model_validate(row["payload"]) if row else None

    def transition_proposal(
        self,
        *,
        proposal_id: str,
        conversation_id: str,
        user_id: str,
        expected_status: ModuleProposalStatus,
        target_status: ModuleProposalStatus,
    ) -> ModuleProposal | None:
        """Atomically consume one proposal decision."""
        with self.engine.begin() as connection:
            row = connection.execute(
                text(
                    """SELECT payload FROM conversation_module_proposals
                    WHERE proposal_id = :proposal_id
                      AND conversation_id = :conversation_id
                      AND user_id = :user_id
                    FOR UPDATE"""
                ),
                {
                    "proposal_id": proposal_id,
                    "conversation_id": conversation_id,
                    "user_id": user_id,
                },
            ).mappings().first()
            if row is None:
                return None
            current = ModuleProposal.model_validate(row["payload"])
            if current.status != expected_status:
                raise ConversationConcurrencyError(
                    "module proposal state changed"
                )
            updated = current.model_copy(update={"status": target_status})
            connection.execute(
                text(
                    """UPDATE conversation_module_proposals
                    SET status = :status, payload = CAST(:payload AS jsonb)
                    WHERE proposal_id = :proposal_id"""
                ),
                {
                    "status": target_status.value,
                    "payload": updated.model_dump_json(),
                    "proposal_id": proposal_id,
                },
            )
        return updated

    def create_module_run(self, run: ModuleRun) -> ModuleRun:
        """Persist a new module frame in an active owner conversation."""
        with self.engine.begin() as connection:
            result = connection.execute(
                text(
                    """INSERT INTO conversation_module_runs
                    (module_run_id, conversation_id, user_id, module_type,
                     parent_module_run_id, depth, status, domain_session_id,
                     version, payload, started_at, ended_at)
                    SELECT :module_run_id, :conversation_id, :user_id,
                     :module_type, :parent_module_run_id, :depth, :status,
                     :domain_session_id, :version, CAST(:payload AS jsonb),
                     :started_at, :ended_at
                    WHERE EXISTS (
                        SELECT 1 FROM conversations
                        WHERE conversation_id = :conversation_id
                          AND user_id = :user_id AND status = :active
                    )"""
                ),
                {**_run_params(run), "active": ConversationStatus.ACTIVE.value},
            )
        if result.rowcount == 0:
            raise LookupError("active conversation not found")
        return run

    def list_module_stack(
        self,
        *,
        conversation_id: str,
        user_id: str,
    ) -> list[ModuleRun]:
        """Return nonterminal module frames in depth order."""
        with self.engine.connect() as connection:
            rows = connection.execute(
                text(
                    """SELECT payload FROM conversation_module_runs
                    WHERE conversation_id = :conversation_id
                      AND user_id = :user_id
                      AND status IN (:active, :suspended)
                    ORDER BY depth ASC"""
                ),
                {
                    "conversation_id": conversation_id,
                    "user_id": user_id,
                    "active": ModuleRunStatus.ACTIVE.value,
                    "suspended": ModuleRunStatus.SUSPENDED.value,
                },
            ).mappings().all()
        return [ModuleRun.model_validate(row["payload"]) for row in rows]

    def transition_module_run(
        self,
        *,
        module_run_id: str,
        conversation_id: str,
        user_id: str,
        expected_status: ModuleRunStatus,
        expected_version: int,
        target_status: ModuleRunStatus,
        ended_at: datetime | None,
    ) -> ModuleRun | None:
        """Optimistically transition one module frame."""
        with self.engine.begin() as connection:
            row = connection.execute(
                text(
                    """SELECT payload FROM conversation_module_runs
                    WHERE module_run_id = :module_run_id
                      AND conversation_id = :conversation_id
                      AND user_id = :user_id
                    FOR UPDATE"""
                ),
                {
                    "module_run_id": module_run_id,
                    "conversation_id": conversation_id,
                    "user_id": user_id,
                },
            ).mappings().first()
            if row is None:
                return None
            current = ModuleRun.model_validate(row["payload"])
            if (
                current.status != expected_status
                or current.version != expected_version
            ):
                raise ConversationConcurrencyError("module run state changed")
            updated = current.model_copy(
                update={
                    "status": target_status,
                    "ended_at": ended_at,
                    "version": current.version + 1,
                }
            )
            connection.execute(
                text(
                    """UPDATE conversation_module_runs
                    SET status = :status, version = :version,
                        ended_at = :ended_at, payload = CAST(:payload AS jsonb)
                    WHERE module_run_id = :module_run_id"""
                ),
                {
                    "status": target_status.value,
                    "version": updated.version,
                    "ended_at": ended_at,
                    "payload": updated.model_dump_json(),
                    "module_run_id": module_run_id,
                },
            )
        return updated

    def _event_from_row(self, row: RowMapping) -> ConversationEvent:
        protected = ProtectedContent(
            plaintext=row["content_plaintext"],
            ciphertext=row["content_ciphertext"],
            nonce=row["content_nonce"],
            key_version=row["content_key_version"],
        )
        content = self._protector.recover(
            protected,
            associated_data=_event_associated_data(
                row["event_id"],
                row["conversation_id"],
                row["user_id"],
                row["sequence_no"],
            ),
        )
        return ConversationEvent(
            event_id=row["event_id"],
            conversation_id=row["conversation_id"],
            user_id=row["user_id"],
            sequence_no=row["sequence_no"],
            event_type=row["event_type"],
            role=row["role"],
            content=content,
            structured_payload=row["structured_payload"],
            module_run_id=row["module_run_id"],
            parent_module_run_id=row["parent_module_run_id"],
            idempotency_key=row["idempotency_key"],
            created_at=row["created_at"],
        )


def _conversation_params(conversation: Conversation) -> dict[str, object]:
    return {
        "conversation_id": conversation.conversation_id,
        "user_id": conversation.user_id,
        "title": conversation.title,
        "status": conversation.status.value,
        "active_module_depth": conversation.active_module_depth,
        "version": conversation.version,
        "history_notice_version": conversation.history_notice_version,
        "created_at": conversation.created_at,
        "updated_at": conversation.updated_at,
    }


def _conversation_from_row(row: RowMapping) -> Conversation:
    return Conversation(
        conversation_id=row["conversation_id"],
        user_id=row["user_id"],
        title=row["title"],
        status=row["status"],
        active_module_depth=row["active_module_depth"],
        version=row["version"],
        history_notice_version=row["history_notice_version"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _event_params(
    event: ConversationEvent,
    protected: ProtectedContent,
) -> dict[str, object]:
    return {
        "event_id": event.event_id,
        "conversation_id": event.conversation_id,
        "user_id": event.user_id,
        "sequence_no": event.sequence_no,
        "event_type": event.event_type.value,
        "role": event.role.value,
        "content_plaintext": protected.plaintext,
        "content_ciphertext": protected.ciphertext,
        "content_nonce": protected.nonce,
        "content_key_version": protected.key_version,
        "structured_payload": (
            event.structured_payload.model_dump_json()
            if event.structured_payload
            else "null"
        ),
        "module_run_id": event.module_run_id,
        "parent_module_run_id": event.parent_module_run_id,
        "idempotency_key": event.idempotency_key,
        "created_at": event.created_at,
    }


def _proposal_params(proposal: ModuleProposal) -> dict[str, object]:
    return {
        "proposal_id": proposal.proposal_id,
        "conversation_id": proposal.conversation_id,
        "user_id": proposal.user_id,
        "proposed_module": proposal.proposed_module.value,
        "reason_code": proposal.reason_code.value,
        "status": proposal.status.value,
        "request_hash": proposal.request_hash,
        "payload": proposal.model_dump_json(),
        "expires_at": proposal.expires_at,
        "created_at": proposal.created_at,
    }


def _run_params(run: ModuleRun) -> dict[str, object]:
    return {
        "module_run_id": run.module_run_id,
        "conversation_id": run.conversation_id,
        "user_id": run.user_id,
        "module_type": run.module_type.value,
        "parent_module_run_id": run.parent_module_run_id,
        "depth": run.depth,
        "status": run.status.value,
        "domain_session_id": run.domain_session_id,
        "version": run.version,
        "payload": run.model_dump_json(),
        "started_at": run.started_at,
        "ended_at": run.ended_at,
    }
