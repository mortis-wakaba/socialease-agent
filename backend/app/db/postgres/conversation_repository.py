"""PostgreSQL adapter for ordered, encrypted-capable conversation timelines."""

from datetime import UTC, datetime
import json
from uuid import uuid4

from sqlalchemy import bindparam, text
from sqlalchemy.engine import Connection, Engine, RowMapping

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
    ConversationImportSnapshot,
    ConversationPage,
    ConversationStatus,
    ModuleProposal,
    ModuleProposalStatus,
    ModuleRun,
    ModuleRunStatus,
    ModuleType,
)
from app.models_conversation_context import ConversationCompactSummary


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

    def import_snapshot(
        self,
        snapshot: ConversationImportSnapshot,
    ) -> Conversation:
        """Atomically insert one deterministic, read-only legacy timeline."""
        conversation = snapshot.conversation
        protected_events = [
            (
                event,
                self._protector.protect(
                    event.content,
                    associated_data=_event_associated_data(
                        event.event_id,
                        event.conversation_id,
                        event.user_id,
                        event.sequence_no,
                    ),
                ),
            )
            for event in snapshot.events
        ]
        with self.engine.begin() as connection:
            result = connection.execute(
                text(
                    """INSERT INTO conversations
                    (conversation_id, user_id, title, status,
                     active_module_depth, version, history_notice_version,
                     created_at, updated_at)
                    VALUES
                    (:conversation_id, :user_id, :title, :status,
                     :active_module_depth, :version, :history_notice_version,
                     :created_at, :updated_at)
                    ON CONFLICT (conversation_id) DO NOTHING"""
                ),
                _conversation_params(conversation),
            )
            if result.rowcount == 0:
                row = connection.execute(
                    text(
                        """SELECT * FROM conversations
                        WHERE conversation_id = :conversation_id
                          AND user_id = :user_id"""
                    ),
                    {
                        "conversation_id": conversation.conversation_id,
                        "user_id": conversation.user_id,
                    },
                ).mappings().first()
                if row is None:
                    raise ConversationConcurrencyError(
                        "legacy import id belongs to another owner"
                    )
                return _conversation_from_row(row)
            for event, protected in protected_events:
                connection.execute(
                    text(
                        """INSERT INTO conversation_events
                        (event_id, conversation_id, user_id, sequence_no,
                         event_type, role, content_plaintext,
                         content_ciphertext, content_nonce, content_key_version,
                         structured_payload, module_run_id,
                         parent_module_run_id, idempotency_key, created_at)
                        VALUES
                        (:event_id, :conversation_id, :user_id, :sequence_no,
                         :event_type, :role, :content_plaintext,
                         :content_ciphertext, :content_nonce,
                         :content_key_version,
                         CAST(:structured_payload AS jsonb), :module_run_id,
                         :parent_module_run_id, :idempotency_key, :created_at)"""
                    ),
                    _event_params(event, protected),
                )
            for run in snapshot.module_runs:
                connection.execute(
                    text(
                        """INSERT INTO conversation_module_runs
                        (module_run_id, conversation_id, user_id, module_type,
                         parent_module_run_id, depth, status, domain_session_id,
                         version, payload, started_at, ended_at)
                        VALUES
                        (:module_run_id, :conversation_id, :user_id,
                         :module_type, :parent_module_run_id, :depth, :status,
                         :domain_session_id, :version, CAST(:payload AS jsonb),
                         :started_at, :ended_at)"""
                    ),
                    _run_params(run),
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

    def list_recent_events(
        self,
        *,
        conversation_id: str,
        user_id: str,
        limit: int = 64,
    ) -> list[ConversationEvent]:
        """Return the newest bounded window in ascending timeline order."""
        limit = _validated_limit(limit, maximum=200)
        with self.engine.connect() as connection:
            rows = connection.execute(
                text(
                    """SELECT events.* FROM conversation_events AS events
                    JOIN conversations AS conversations
                      ON conversations.conversation_id = events.conversation_id
                    WHERE events.conversation_id = :conversation_id
                      AND events.user_id = :user_id
                      AND conversations.user_id = :user_id
                      AND conversations.status != :deleted
                    ORDER BY events.sequence_no DESC
                    LIMIT :limit"""
                ),
                {
                    "conversation_id": conversation_id,
                    "user_id": user_id,
                    "deleted": ConversationStatus.DELETED.value,
                    "limit": limit,
                },
            ).mappings().all()
        return [self._event_from_row(row) for row in reversed(rows)]

    def get_event_by_idempotency(
        self,
        *,
        conversation_id: str,
        user_id: str,
        idempotency_key: str,
    ) -> ConversationEvent | None:
        """Return one event inside its complete owner scope."""
        with self.engine.connect() as connection:
            row = connection.execute(
                text(
                    """SELECT events.* FROM conversation_events AS events
                    JOIN conversations AS conversations
                      ON conversations.conversation_id = events.conversation_id
                    WHERE events.conversation_id = :conversation_id
                      AND events.user_id = :user_id
                      AND events.idempotency_key = :idempotency_key
                      AND conversations.user_id = :user_id
                      AND conversations.status != :deleted"""
                ),
                {
                    "conversation_id": conversation_id,
                    "user_id": user_id,
                    "idempotency_key": idempotency_key,
                    "deleted": ConversationStatus.DELETED.value,
                },
            ).mappings().first()
        return self._event_from_row(row) if row else None

    def get_compact_summary(
        self,
        *,
        conversation_id: str,
        user_id: str,
    ) -> ConversationCompactSummary | None:
        """Return the durable summary only inside its owner scope."""
        with self.engine.connect() as connection:
            row = connection.execute(
                text(
                    """SELECT summaries.payload
                    FROM conversation_context_summaries AS summaries
                    JOIN conversations AS conversations
                      ON conversations.conversation_id =
                         summaries.conversation_id
                    WHERE summaries.conversation_id = :conversation_id
                      AND summaries.user_id = :user_id
                      AND conversations.user_id = :user_id
                      AND conversations.status != :deleted"""
                ),
                {
                    "conversation_id": conversation_id,
                    "user_id": user_id,
                    "deleted": ConversationStatus.DELETED.value,
                },
            ).mappings().first()
        return (
            ConversationCompactSummary.model_validate(row["payload"])
            if row
            else None
        )

    def save_compact_summary(
        self,
        summary: ConversationCompactSummary,
        *,
        expected_version: int | None,
    ) -> ConversationCompactSummary:
        """Create or optimistically replace a durable summary."""
        params = {
            "conversation_id": summary.conversation_id,
            "user_id": summary.user_id,
            "sequence": summary.compacted_through_sequence,
            "version": summary.version,
            "payload": summary.model_dump_json(),
            "updated_at": summary.updated_at,
            "deleted": ConversationStatus.DELETED.value,
        }
        with self.engine.begin() as connection:
            if expected_version is None:
                result = connection.execute(
                    text(
                        """INSERT INTO conversation_context_summaries
                        (conversation_id, user_id,
                         compacted_through_sequence, version, payload,
                         updated_at)
                        SELECT :conversation_id, :user_id, :sequence, :version,
                         CAST(:payload AS jsonb), :updated_at
                        WHERE EXISTS (
                            SELECT 1 FROM conversations
                            WHERE conversation_id = :conversation_id
                              AND user_id = :user_id
                              AND status != :deleted
                        )
                        ON CONFLICT (conversation_id) DO NOTHING"""
                    ),
                    params,
                )
            else:
                params["expected_version"] = expected_version
                result = connection.execute(
                    text(
                        """UPDATE conversation_context_summaries
                        SET compacted_through_sequence = :sequence,
                            version = :version,
                            payload = CAST(:payload AS jsonb),
                            updated_at = :updated_at
                        WHERE conversation_id = :conversation_id
                          AND user_id = :user_id
                          AND version = :expected_version"""
                    ),
                    params,
                )
        if result.rowcount == 0:
            raise ConversationConcurrencyError("conversation summary state changed")
        return summary

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
                    SELECT :module_run_id, CAST(:conversation_id AS VARCHAR(64)),
                     CAST(:user_id AS VARCHAR(128)),
                     :module_type, :parent_module_run_id, :depth, :status,
                     :domain_session_id, :version, CAST(:payload AS jsonb),
                     :started_at, :ended_at
                    WHERE EXISTS (
                        SELECT 1 FROM conversations
                        WHERE conversation_id =
                              CAST(:conversation_id AS VARCHAR(64))
                          AND user_id = CAST(:user_id AS VARCHAR(128))
                          AND status = :active
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

    def get_module_run_for_user(
        self,
        *,
        module_run_id: str,
        conversation_id: str,
        user_id: str,
    ) -> ModuleRun | None:
        """Return one module run only inside its complete owner scope."""
        with self.engine.connect() as connection:
            row = connection.execute(
                text(
                    """SELECT payload FROM conversation_module_runs
                    WHERE module_run_id = :module_run_id
                      AND conversation_id = :conversation_id
                      AND user_id = :user_id"""
                ),
                {
                    "module_run_id": module_run_id,
                    "conversation_id": conversation_id,
                    "user_id": user_id,
                },
            ).mappings().first()
        return ModuleRun.model_validate(row["payload"]) if row else None

    def list_all_module_runs(
        self,
        *,
        conversation_id: str,
        user_id: str,
    ) -> list[ModuleRun]:
        """Return all active and terminal module runs for export/deletion."""
        with self.engine.connect() as connection:
            rows = connection.execute(
                text(
                    """SELECT payload FROM conversation_module_runs
                    WHERE conversation_id = :conversation_id
                      AND user_id = :user_id
                    ORDER BY depth ASC, started_at ASC"""
                ),
                {
                    "conversation_id": conversation_id,
                    "user_id": user_id,
                },
            ).mappings().all()
        return [ModuleRun.model_validate(row["payload"]) for row in rows]

    def get_conversation_for_domain_session(
        self,
        *,
        user_id: str,
        module_type: ModuleType,
        domain_session_id: str,
    ) -> Conversation | None:
        """Return the conversation already owning one domain session."""
        with self.engine.connect() as connection:
            row = connection.execute(
                text(
                    """SELECT c.* FROM conversations AS c
                    JOIN conversation_module_runs AS r
                      ON r.conversation_id = c.conversation_id
                    WHERE r.user_id = :user_id
                      AND r.module_type = :module_type
                      AND r.domain_session_id = :domain_session_id
                    LIMIT 1"""
                ),
                {
                    "user_id": user_id,
                    "module_type": module_type.value,
                    "domain_session_id": domain_session_id,
                },
            ).mappings().first()
        return _conversation_from_row(row) if row else None

    def list_proposals(
        self,
        *,
        conversation_id: str,
        user_id: str,
    ) -> list[ModuleProposal]:
        """Return all owner-scoped proposals for export."""
        with self.engine.connect() as connection:
            rows = connection.execute(
                text(
                    """SELECT payload FROM conversation_module_proposals
                    WHERE conversation_id = :conversation_id
                      AND user_id = :user_id
                    ORDER BY created_at ASC"""
                ),
                {
                    "conversation_id": conversation_id,
                    "user_id": user_id,
                },
            ).mappings().all()
        return [ModuleProposal.model_validate(row["payload"]) for row in rows]

    def delete_for_user(
        self,
        *,
        conversation_id: str,
        user_id: str,
    ) -> dict[str, int] | None:
        """Delete one conversation and its directly attributable records."""
        with self.engine.begin() as connection:
            receipt = connection.execute(
                text(
                    """SELECT deleted_counts
                    FROM conversation_deletion_receipts
                    WHERE conversation_id = :conversation_id
                      AND user_id = :user_id"""
                ),
                {
                    "conversation_id": conversation_id,
                    "user_id": user_id,
                },
            ).mappings().first()
            if receipt is not None:
                return {
                    key: int(value)
                    for key, value in receipt["deleted_counts"].items()
                }
            owner = connection.execute(
                text(
                    """SELECT 1 FROM conversations
                    WHERE conversation_id = :conversation_id
                      AND user_id = :user_id
                    FOR UPDATE"""
                ),
                {
                    "conversation_id": conversation_id,
                    "user_id": user_id,
                },
            ).first()
            if owner is None:
                return None
            event_ids = list(
                connection.execute(
                    text(
                        """SELECT event_id FROM conversation_events
                        WHERE conversation_id = :conversation_id
                          AND user_id = :user_id"""
                    ),
                    {
                        "conversation_id": conversation_id,
                        "user_id": user_id,
                    },
                ).scalars()
            )
            run_rows = connection.execute(
                text(
                    """SELECT module_type, domain_session_id
                    FROM conversation_module_runs
                    WHERE conversation_id = :conversation_id
                      AND user_id = :user_id"""
                ),
                {
                    "conversation_id": conversation_id,
                    "user_id": user_id,
                },
            ).mappings().all()
            domain_ids = [
                row["domain_session_id"]
                for row in run_rows
                if row["domain_session_id"]
            ]
            source_ids = [*event_ids, *domain_ids]
            counts = {
                "events": len(event_ids),
                "module_runs": len(run_rows),
                "module_proposals": int(
                    connection.execute(
                        text(
                            """SELECT COUNT(*)
                            FROM conversation_module_proposals
                            WHERE conversation_id = :conversation_id
                              AND user_id = :user_id"""
                        ),
                        {
                            "conversation_id": conversation_id,
                            "user_id": user_id,
                        },
                    ).scalar_one()
                ),
                "compact_summaries": int(
                    connection.execute(
                        text(
                            """SELECT COUNT(*)
                            FROM conversation_context_summaries
                            WHERE conversation_id = :conversation_id
                              AND user_id = :user_id"""
                        ),
                        {
                            "conversation_id": conversation_id,
                            "user_id": user_id,
                        },
                    ).scalar_one()
                ),
                "episodic_memories": 0,
                "memory_proposals": 0,
                "domain_sessions": 0,
            }
            if source_ids:
                memory_ids = list(
                    connection.execute(
                        _expanding_text(
                            """SELECT memory_id FROM episodic_memories
                            WHERE user_id = :user_id
                              AND source_id IN :source_ids""",
                            "source_ids",
                        ),
                        {"user_id": user_id, "source_ids": source_ids},
                    ).scalars()
                )
                proposal_ids = list(
                    connection.execute(
                        _expanding_text(
                            """SELECT proposal_id FROM memory_proposals
                            WHERE user_id = :user_id
                              AND source_id IN :source_ids""",
                            "source_ids",
                        ),
                        {"user_id": user_id, "source_ids": source_ids},
                    ).scalars()
                )
                subject_ids = [*memory_ids, *proposal_ids]
                if subject_ids:
                    connection.execute(
                        _expanding_text(
                            """DELETE FROM memory_events
                            WHERE user_id = :user_id
                              AND subject_id IN :subject_ids""",
                            "subject_ids",
                        ),
                        {"user_id": user_id, "subject_ids": subject_ids},
                    )
                counts["memory_proposals"] = int(
                    connection.execute(
                        _expanding_text(
                            """DELETE FROM memory_proposals
                            WHERE user_id = :user_id
                              AND source_id IN :source_ids""",
                            "source_ids",
                        ),
                        {"user_id": user_id, "source_ids": source_ids},
                    ).rowcount
                    or 0
                )
                counts["episodic_memories"] = int(
                    connection.execute(
                        _expanding_text(
                            """DELETE FROM episodic_memories
                            WHERE user_id = :user_id
                              AND source_id IN :source_ids""",
                            "source_ids",
                        ),
                        {"user_id": user_id, "source_ids": source_ids},
                    ).rowcount
                    or 0
                )
            counts["domain_sessions"] = _delete_postgres_domain_sessions(
                connection,
                user_id=user_id,
                run_rows=run_rows,
            )
            connection.execute(
                text(
                    """DELETE FROM conversations
                    WHERE conversation_id = :conversation_id
                      AND user_id = :user_id"""
                ),
                {
                    "conversation_id": conversation_id,
                    "user_id": user_id,
                },
            )
            counts["conversations"] = 1
            connection.execute(
                text(
                    """INSERT INTO conversation_deletion_receipts
                    (conversation_id, user_id, deleted_counts, deleted_at)
                    VALUES
                    (:conversation_id, :user_id,
                     CAST(:deleted_counts AS jsonb), :deleted_at)"""
                ),
                {
                    "conversation_id": conversation_id,
                    "user_id": user_id,
                    "deleted_counts": json.dumps(counts),
                    "deleted_at": datetime.now(UTC),
                },
            )
        return counts

    def delete_all_for_user(self, *, user_id: str) -> dict[str, int]:
        """Delete every user conversation through the scoped delete path."""
        totals: dict[str, int] = {}
        while True:
            page = self.list_for_user(user_id, limit=100)
            if not page.items:
                break
            for conversation in page.items:
                counts = self.delete_for_user(
                    conversation_id=conversation.conversation_id,
                    user_id=user_id,
                )
                for key, value in (counts or {}).items():
                    totals[key] = totals.get(key, 0) + value
        return totals

    def update_module_domain_session(
        self,
        *,
        module_run_id: str,
        conversation_id: str,
        user_id: str,
        expected_version: int,
        domain_session_id: str,
    ) -> ModuleRun:
        """Attach a lazily created domain session with optimistic locking."""
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
                raise LookupError("module run not found")
            current = ModuleRun.model_validate(row["payload"])
            if current.version != expected_version:
                raise ConversationConcurrencyError("module run state changed")
            updated = current.model_copy(
                update={
                    "domain_session_id": domain_session_id,
                    "version": current.version + 1,
                }
            )
            connection.execute(
                text(
                    """UPDATE conversation_module_runs
                    SET domain_session_id = :domain_session_id,
                        version = :version,
                        payload = CAST(:payload AS jsonb)
                    WHERE module_run_id = :module_run_id"""
                ),
                {
                    "domain_session_id": domain_session_id,
                    "version": updated.version,
                    "payload": updated.model_dump_json(),
                    "module_run_id": module_run_id,
                },
            )
        return updated

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


def _expanding_text(statement: str, parameter: str):
    return text(statement).bindparams(bindparam(parameter, expanding=True))


def _delete_postgres_domain_sessions(
    connection: Connection,
    *,
    user_id: str,
    run_rows: list[RowMapping],
) -> int:
    """Delete durable domain sessions attributable to one conversation."""
    grouped: dict[str, list[str]] = {}
    for row in run_rows:
        domain_session_id = row["domain_session_id"]
        if domain_session_id:
            grouped.setdefault(row["module_type"], []).append(domain_session_id)
    deleted = 0
    for module_type, table, id_column in (
        ("roleplay", "roleplay_sessions", "session_id"),
        ("worksheet", "worksheets", "worksheet_id"),
    ):
        identifiers = grouped.get(module_type, [])
        if not identifiers:
            continue
        deleted += int(
            connection.execute(
                _expanding_text(
                    f"""DELETE FROM {table}
                    WHERE user_id = :user_id
                      AND {id_column} IN :identifiers""",
                    "identifiers",
                ),
                {"user_id": user_id, "identifiers": identifiers},
            ).rowcount
            or 0
        )
    exposure_ids = grouped.get("exposure", [])
    if exposure_ids:
        connection.execute(
            _expanding_text(
                """DELETE FROM exposure_attempts
                WHERE user_id = :user_id AND plan_id IN :identifiers""",
                "identifiers",
            ),
            {"user_id": user_id, "identifiers": exposure_ids},
        )
        deleted += int(
            connection.execute(
                _expanding_text(
                    """DELETE FROM exposure_plans
                    WHERE user_id = :user_id AND plan_id IN :identifiers""",
                    "identifiers",
                ),
                {"user_id": user_id, "identifiers": exposure_ids},
            ).rowcount
            or 0
        )
    return deleted
