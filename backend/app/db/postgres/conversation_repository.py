"""PostgreSQL adapter for ordered, encrypted-capable conversation timelines."""

from datetime import UTC, datetime, timedelta
import json
from uuid import uuid4

from sqlalchemy import bindparam, text
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.conversation.content_protector import (
    ConversationContentProtector,
    ProtectedContent,
    configured_content_protector,
)
from app.conversation.repository import (
    ConversationCommandClaim,
    ConversationConcurrencyError,
    ConversationIdempotencyError,
    ModuleStartJob,
    _command_associated_data,
    _decode_conversation_cursor,
    _decode_event_cursor,
    _encode_conversation_cursor,
    _encode_event_cursor,
    _event_associated_data,
    _validate_idempotent_event,
    _validated_limit,
)
from app.db.postgres.engine import (
    postgres_read_connection,
    postgres_transaction,
    postgres_write_connection,
    shared_postgres_async_engine,
)
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
    ModuleType,
)
from app.models_conversation_context import ConversationCompactSummary


class PostgresConversationRepository:
    """PostgreSQL repository using row locks for per-conversation ordering."""

    def __init__(
        self,
        *,
        database_url: str | None = None,
        engine: AsyncEngine | None = None,
        protector: ConversationContentProtector | None = None,
    ) -> None:
        resolved_url = database_url or database_settings().database_url
        self.engine = engine or shared_postgres_async_engine(resolved_url)
        self._protector = protector or configured_content_protector()

    def module_start_transaction(self):
        """Bind one transaction across conversation and domain repositories."""
        return postgres_transaction(self.engine)

    async def create(
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
        async with self.engine.begin() as connection:
            (await connection.execute(
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
            ))
        return conversation

    async def get_for_user(
        self,
        conversation_id: str,
        user_id: str,
    ) -> Conversation | None:
        """Return an undeleted conversation only to its owner."""
        async with self.engine.connect() as connection:
            row = (await connection.execute(
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
            )).mappings().first()
        return _conversation_from_row(row) if row else None

    async def claim_command(
        self,
        *,
        conversation_id: str,
        user_id: str,
        idempotency_key: str,
        request_hash: str,
    ) -> ConversationCommandClaim:
        """Atomically acquire one logical command or return its saved result."""
        now = datetime.now(UTC)
        async with self.engine.begin() as connection:
            owner = (await connection.execute(
                text(
                    """SELECT 1 FROM conversations
                    WHERE conversation_id = :conversation_id
                      AND user_id = :user_id AND status != :deleted
                    FOR UPDATE"""
                ),
                {
                    "conversation_id": conversation_id,
                    "user_id": user_id,
                    "deleted": ConversationStatus.DELETED.value,
                },
            )).first()
            if owner is None:
                raise LookupError("conversation not found")
            inserted = (await connection.execute(
                text(
                    """INSERT INTO conversation_commands
                    (conversation_id, user_id, idempotency_key, request_hash,
                     status, created_at)
                    VALUES (:conversation_id, :user_id, :idempotency_key,
                            :request_hash, 'processing', :created_at)
                    ON CONFLICT (conversation_id, idempotency_key) DO NOTHING
                    RETURNING conversation_id"""
                ),
                {
                    "conversation_id": conversation_id,
                    "user_id": user_id,
                    "idempotency_key": idempotency_key,
                    "request_hash": request_hash,
                    "created_at": now,
                },
            )).first()
            if inserted is not None:
                return ConversationCommandClaim(acquired=True)
            row = (await connection.execute(
                text(
                    """SELECT * FROM conversation_commands
                    WHERE conversation_id = :conversation_id
                      AND idempotency_key = :idempotency_key
                    FOR UPDATE"""
                ),
                {
                    "conversation_id": conversation_id,
                    "idempotency_key": idempotency_key,
                },
            )).mappings().first()
        if row is None or row["user_id"] != user_id:
            raise ConversationIdempotencyError(
                "idempotency key belongs to another command"
            )
        if row["request_hash"] != request_hash:
            raise ConversationIdempotencyError(
                "idempotency key was reused with different input"
            )
        if row["status"] != "completed":
            return ConversationCommandClaim(acquired=False)
        result = self._protector.recover(
            ProtectedContent(
                plaintext=row["result_plaintext"],
                ciphertext=row["result_ciphertext"],
                nonce=row["result_nonce"],
                key_version=row["result_key_version"],
            ),
            associated_data=_command_associated_data(
                conversation_id,
                user_id,
                idempotency_key,
            ),
        )
        return ConversationCommandClaim(
            acquired=False,
            completed_result=result,
        )

    async def complete_command(
        self,
        *,
        conversation_id: str,
        user_id: str,
        idempotency_key: str,
        result: str,
    ) -> None:
        """Persist the final encrypted-capable result exactly once."""
        protected = self._protector.protect(
            result,
            associated_data=_command_associated_data(
                conversation_id,
                user_id,
                idempotency_key,
            ),
        )
        async with self.engine.begin() as connection:
            updated = (await connection.execute(
                text(
                    """UPDATE conversation_commands
                    SET status = 'completed',
                        result_plaintext = :result_plaintext,
                        result_ciphertext = :result_ciphertext,
                        result_nonce = :result_nonce,
                        result_key_version = :result_key_version,
                        completed_at = :completed_at
                    WHERE conversation_id = :conversation_id
                      AND user_id = :user_id
                      AND idempotency_key = :idempotency_key
                      AND status = 'processing'"""
                ),
                {
                    "result_plaintext": protected.plaintext,
                    "result_ciphertext": protected.ciphertext,
                    "result_nonce": protected.nonce,
                    "result_key_version": protected.key_version,
                    "completed_at": datetime.now(UTC),
                    "conversation_id": conversation_id,
                    "user_id": user_id,
                    "idempotency_key": idempotency_key,
                },
            ))
        if updated.rowcount != 1:
            raise ConversationConcurrencyError(
                "conversation command is no longer claimable"
            )

    async def list_for_user(
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
        async with self.engine.connect() as connection:
            rows = (await connection.execute(
                text(
                    f"""SELECT * FROM conversations
                    WHERE user_id = :user_id AND status != :deleted
                    {cursor_clause}
                    ORDER BY updated_at DESC, conversation_id DESC
                    LIMIT :limit"""
                ),
                params,
            )).mappings().all()
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

    async def update_metadata(
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
        async with self.engine.begin() as connection:
            result = (await connection.execute(
                text(
                    f"""UPDATE conversations
                    SET {", ".join(assignments)}
                    WHERE conversation_id = :conversation_id
                      AND user_id = :user_id
                      AND version = :expected_version
                      AND status != :deleted"""
                ),
                params,
            ))
        if result.rowcount == 0:
            if await self.get_for_user(conversation_id, user_id) is None:
                return None
            raise ConversationConcurrencyError("conversation version changed")
        return await self.get_for_user(conversation_id, user_id)

    async def append_event(
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
        async with self.engine.begin() as connection:
            owner = (await connection.execute(
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
            )).mappings().first()
            if owner is None:
                raise LookupError("active conversation not found")

            existing_row = (await connection.execute(
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
            )).mappings().first()
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

            sequence_no = (await connection.execute(
                text(
                    """SELECT COALESCE(MAX(sequence_no), 0) + 1 AS next_sequence
                    FROM conversation_events
                    WHERE conversation_id = :conversation_id"""
                ),
                {"conversation_id": conversation_id},
            )).scalar_one()
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
            (await connection.execute(
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
            ))
            (await connection.execute(
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
            ))
        return event

    async def list_events(
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
        async with self.engine.connect() as connection:
            rows = (await connection.execute(
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
            )).mappings().all()
        has_more = len(rows) > limit
        items = [self._event_from_row(row) for row in rows[:limit]]
        next_cursor = (
            _encode_event_cursor(items[-1].sequence_no)
            if has_more and items
            else None
        )
        return ConversationEventPage(items=items, next_cursor=next_cursor)

    async def list_recent_events(
        self,
        *,
        conversation_id: str,
        user_id: str,
        limit: int = 64,
    ) -> list[ConversationEvent]:
        """Return the newest bounded window in ascending timeline order."""
        limit = _validated_limit(limit, maximum=200)
        async with self.engine.connect() as connection:
            rows = (await connection.execute(
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
            )).mappings().all()
        return [self._event_from_row(row) for row in reversed(rows)]

    async def get_event_by_idempotency(
        self,
        *,
        conversation_id: str,
        user_id: str,
        idempotency_key: str,
    ) -> ConversationEvent | None:
        """Return one event inside its complete owner scope."""
        async with self.engine.connect() as connection:
            row = (await connection.execute(
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
            )).mappings().first()
        return self._event_from_row(row) if row else None

    async def get_compact_summary(
        self,
        *,
        conversation_id: str,
        user_id: str,
    ) -> ConversationCompactSummary | None:
        """Return the durable summary only inside its owner scope."""
        async with self.engine.connect() as connection:
            row = (await connection.execute(
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
            )).mappings().first()
        return (
            ConversationCompactSummary.model_validate(row["payload"])
            if row
            else None
        )

    async def save_compact_summary(
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
        async with self.engine.begin() as connection:
            if expected_version is None:
                result = (await connection.execute(
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
                ))
            else:
                params["expected_version"] = expected_version
                result = (await connection.execute(
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
                ))
        if result.rowcount == 0:
            raise ConversationConcurrencyError("conversation summary state changed")
        return summary

    async def save_proposal(self, proposal: ModuleProposal) -> ModuleProposal:
        """Persist a proposal or return its request-hash replay."""
        async with self.engine.begin() as connection:
            row = (await connection.execute(
                text(
                    """INSERT INTO conversation_module_proposals
                    (proposal_id, conversation_id, user_id, proposed_module,
                     reason_code, status, request_hash, payload, expires_at,
                     created_at)
                    SELECT CAST(:proposal_id AS VARCHAR(64)),
                     CAST(:conversation_id AS VARCHAR(64)),
                     CAST(:user_id AS VARCHAR(128)),
                     CAST(:proposed_module AS VARCHAR(16)),
                     CAST(:reason_code AS VARCHAR(64)),
                     CAST(:status AS VARCHAR(16)),
                     CAST(:request_hash AS VARCHAR(128)),
                     CAST(:payload AS jsonb), :expires_at, :created_at
                    WHERE EXISTS (
                        SELECT 1 FROM conversations
                        WHERE conversation_id =
                              CAST(:conversation_id AS VARCHAR(64))
                          AND user_id = CAST(:user_id AS VARCHAR(128))
                          AND status = CAST(:active AS VARCHAR(16))
                    )
                    ON CONFLICT (conversation_id, request_hash) DO NOTHING
                    RETURNING payload"""
                ),
                {**_proposal_params(proposal), "active": "active"},
            )).mappings().first()
            if row:
                return ModuleProposal.model_validate(row["payload"])
            existing = (await connection.execute(
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
            )).mappings().first()
        if existing:
            return ModuleProposal.model_validate(existing["payload"])
        raise LookupError("active conversation not found")

    async def get_proposal_for_user(
        self,
        *,
        proposal_id: str,
        conversation_id: str,
        user_id: str,
    ) -> ModuleProposal | None:
        """Return one proposal only inside its complete owner scope."""
        async with self.engine.connect() as connection:
            row = (await connection.execute(
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
            )).mappings().first()
        return ModuleProposal.model_validate(row["payload"]) if row else None

    async def get_proposal_by_request(
        self,
        *,
        conversation_id: str,
        user_id: str,
        request_hash: str,
    ) -> ModuleProposal | None:
        """Return a deduplicated proposal by scoped request hash."""
        async with self.engine.connect() as connection:
            row = (await connection.execute(
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
            )).mappings().first()
        return ModuleProposal.model_validate(row["payload"]) if row else None

    async def transition_proposal(
        self,
        *,
        proposal_id: str,
        conversation_id: str,
        user_id: str,
        expected_status: ModuleProposalStatus,
        target_status: ModuleProposalStatus,
    ) -> ModuleProposal | None:
        """Atomically consume one proposal decision."""
        async with self.engine.begin() as connection:
            row = (await connection.execute(
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
            )).mappings().first()
            if row is None:
                return None
            current = ModuleProposal.model_validate(row["payload"])
            if current.status != expected_status:
                raise ConversationConcurrencyError(
                    "module proposal state changed"
                )
            updated = current.model_copy(update={"status": target_status})
            (await connection.execute(
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
            ))
        return updated

    async def create_module_run(self, run: ModuleRun) -> ModuleRun:
        """Persist a new module frame in an active owner conversation."""
        async with self.engine.begin() as connection:
            result = (await connection.execute(
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
            ))
        if result.rowcount == 0:
            raise LookupError("active conversation not found")
        return run

    async def begin_module_start(
        self,
        *,
        proposal: ModuleProposal,
        run: ModuleRun,
        parent: ModuleRun | None,
    ) -> ModuleRun:
        """Atomically consume a proposal, reserve its frame, and enqueue startup."""
        accepted = proposal.model_copy(
            update={"status": ModuleProposalStatus.ACCEPTED}
        )
        now = datetime.now(UTC)
        async with postgres_write_connection(self.engine) as connection:
            proposal_result = await connection.execute(
                text(
                    """UPDATE conversation_module_proposals
                    SET status = :accepted, payload = CAST(:payload AS jsonb)
                    WHERE proposal_id = :proposal_id
                      AND conversation_id = :conversation_id
                      AND user_id = :user_id AND status = :pending"""
                ),
                {
                    "accepted": ModuleProposalStatus.ACCEPTED.value,
                    "payload": accepted.model_dump_json(),
                    "proposal_id": proposal.proposal_id,
                    "conversation_id": proposal.conversation_id,
                    "user_id": proposal.user_id,
                    "pending": ModuleProposalStatus.PENDING.value,
                },
            )
            if proposal_result.rowcount == 0:
                raise ConversationConcurrencyError("module proposal state changed")
            if parent is not None:
                suspended = parent.model_copy(
                    update={
                        "status": ModuleRunStatus.SUSPENDED,
                        "version": parent.version + 1,
                    }
                )
                parent_result = await connection.execute(
                    text(
                        """UPDATE conversation_module_runs
                        SET status = :suspended, version = :new_version,
                            payload = CAST(:payload AS jsonb)
                        WHERE module_run_id = :module_run_id
                          AND conversation_id = :conversation_id
                          AND user_id = :user_id
                          AND status = :active AND version = :expected_version"""
                    ),
                    {
                        "suspended": ModuleRunStatus.SUSPENDED.value,
                        "new_version": suspended.version,
                        "payload": suspended.model_dump_json(),
                        "module_run_id": parent.module_run_id,
                        "conversation_id": parent.conversation_id,
                        "user_id": parent.user_id,
                        "active": ModuleRunStatus.ACTIVE.value,
                        "expected_version": parent.version,
                    },
                )
                if parent_result.rowcount == 0:
                    raise ConversationConcurrencyError("parent module state changed")
            await connection.execute(
                text(
                    """INSERT INTO conversation_module_runs
                    (module_run_id, conversation_id, user_id, module_type,
                     parent_module_run_id, depth, status, domain_session_id,
                     version, payload, started_at, ended_at)
                    VALUES
                    (:module_run_id, :conversation_id, :user_id, :module_type,
                     :parent_module_run_id, :depth, :status, :domain_session_id,
                     :version, CAST(:payload AS jsonb), :started_at, :ended_at)"""
                ),
                _run_params(run),
            )
            conversation_result = await connection.execute(
                text(
                    """UPDATE conversations
                    SET active_module_depth = :depth, version = version + 1,
                        updated_at = :updated_at
                    WHERE conversation_id = :conversation_id
                      AND user_id = :user_id AND status = :active"""
                ),
                {
                    "depth": run.depth,
                    "updated_at": now,
                    "conversation_id": run.conversation_id,
                    "user_id": run.user_id,
                    "active": ConversationStatus.ACTIVE.value,
                },
            )
            if conversation_result.rowcount == 0:
                raise LookupError("active conversation not found")
            await connection.execute(
                text(
                    """INSERT INTO conversation_module_start_outbox
                    (module_run_id, conversation_id, user_id, proposal_id,
                     status, attempt_count, next_attempt_at, created_at, updated_at)
                    VALUES (:module_run_id, :conversation_id, :user_id,
                            :proposal_id, 'pending', 0, :created_at,
                            :created_at, :updated_at)"""
                ),
                {
                    "module_run_id": run.module_run_id,
                    "conversation_id": run.conversation_id,
                    "user_id": run.user_id,
                    "proposal_id": proposal.proposal_id,
                    "created_at": now,
                    "updated_at": now,
                },
            )
        return run

    async def claim_module_start(self, *, module_run_id: str) -> bool:
        """Claim a pending/recoverable module startup."""
        now = datetime.now(UTC)
        async with postgres_write_connection(self.engine) as connection:
            result = await connection.execute(
                text(
                    """UPDATE conversation_module_start_outbox
                    SET status = 'processing',
                        attempt_count = attempt_count + 1,
                        lease_owner = :lease_owner,
                        lease_expires_at = :lease_expires_at,
                        updated_at = :updated_at
                    WHERE module_run_id = :module_run_id
                      AND (
                        status = 'pending'
                        OR (
                          status = 'processing'
                          AND lease_expires_at <= :updated_at
                        )
                      )"""
                ),
                {
                    "module_run_id": module_run_id,
                    "lease_owner": f"request:{uuid4().hex}",
                    "lease_expires_at": now + timedelta(seconds=60),
                    "updated_at": now,
                },
            )
        return result.rowcount == 1

    async def claim_due_module_starts(
        self,
        *,
        worker_id: str,
        limit: int,
        lease_seconds: int,
    ) -> list[ModuleStartJob]:
        """Lease due jobs with SKIP LOCKED for safe multi-replica workers."""
        now = datetime.now(UTC)
        async with self.engine.begin() as connection:
            rows = (
                await connection.execute(
                    text(
                        """WITH due AS (
                            SELECT module_run_id
                            FROM conversation_module_start_outbox
                            WHERE (
                              (status = 'pending' AND next_attempt_at <= :now)
                              OR (
                                status = 'processing'
                                AND lease_expires_at <= :now
                              )
                            )
                            ORDER BY next_attempt_at, created_at
                            FOR UPDATE SKIP LOCKED
                            LIMIT :limit
                        )
                        UPDATE conversation_module_start_outbox AS outbox
                        SET status = 'processing',
                            attempt_count = outbox.attempt_count + 1,
                            lease_owner = :worker_id,
                            lease_expires_at = :lease_expires_at,
                            updated_at = :now
                        FROM due
                        WHERE outbox.module_run_id = due.module_run_id
                        RETURNING outbox.module_run_id, outbox.conversation_id,
                                  outbox.user_id, outbox.proposal_id,
                                  outbox.attempt_count, outbox.max_attempts"""
                    ),
                    {
                        "now": now,
                        "limit": max(1, min(limit, 100)),
                        "worker_id": worker_id,
                        "lease_expires_at": now
                        + timedelta(seconds=max(1, lease_seconds)),
                    },
                )
            ).mappings().all()
        return [
            ModuleStartJob(
                module_run_id=row["module_run_id"],
                conversation_id=row["conversation_id"],
                user_id=row["user_id"],
                proposal_id=row["proposal_id"],
                attempt_count=int(row["attempt_count"]),
                max_attempts=int(row["max_attempts"]),
                lease_owner=worker_id,
            )
            for row in rows
        ]

    async def complete_module_start(self, *, module_run_id: str) -> None:
        """Mark a module startup side effect reconciled."""
        async with self.engine.begin() as connection:
            await connection.execute(
                text(
                    """UPDATE conversation_module_start_outbox
                    SET status = 'completed', last_error_code = NULL,
                        lease_owner = NULL, lease_expires_at = NULL,
                        completed_at = :updated_at,
                        updated_at = :updated_at
                    WHERE module_run_id = :module_run_id"""
                ),
                {"module_run_id": module_run_id, "updated_at": datetime.now(UTC)},
            )

    async def retry_module_start(
        self, *, module_run_id: str, error_code: str
    ) -> None:
        """Return a failed startup to the replay queue without storing details."""
        async with self.engine.begin() as connection:
            row = (
                await connection.execute(
                    text(
                        """SELECT attempt_count, max_attempts
                        FROM conversation_module_start_outbox
                        WHERE module_run_id = :module_run_id
                        FOR UPDATE"""
                    ),
                    {"module_run_id": module_run_id},
                )
            ).mappings().first()
            if row is None:
                return
            now = datetime.now(UTC)
            dead_letter = int(row["attempt_count"]) >= int(row["max_attempts"])
            await connection.execute(
                text(
                    """UPDATE conversation_module_start_outbox
                    SET status = :status, last_error_code = :error_code,
                        next_attempt_at = :next_attempt_at,
                        lease_owner = NULL, lease_expires_at = NULL,
                        updated_at = :updated_at
                    WHERE module_run_id = :module_run_id"""
                ),
                {
                    "module_run_id": module_run_id,
                    "status": "dead_letter" if dead_letter else "pending",
                    "error_code": error_code[:64],
                    "next_attempt_at": now
                    + timedelta(
                        seconds=min(
                            300,
                            2 ** max(0, int(row["attempt_count"]) - 1),
                        )
                    ),
                    "updated_at": now,
                },
            )

    async def list_module_stack(
        self,
        *,
        conversation_id: str,
        user_id: str,
    ) -> list[ModuleRun]:
        """Return nonterminal module frames in depth order."""
        async with self.engine.connect() as connection:
            rows = (await connection.execute(
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
            )).mappings().all()
        return [ModuleRun.model_validate(row["payload"]) for row in rows]

    async def get_module_run_for_user(
        self,
        *,
        module_run_id: str,
        conversation_id: str,
        user_id: str,
    ) -> ModuleRun | None:
        """Return one module run only inside its complete owner scope."""
        async with postgres_read_connection(self.engine) as connection:
            row = (await connection.execute(
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
            )).mappings().first()
        return ModuleRun.model_validate(row["payload"]) if row else None

    async def list_all_module_runs(
        self,
        *,
        conversation_id: str,
        user_id: str,
    ) -> list[ModuleRun]:
        """Return all active and terminal module runs for export/deletion."""
        async with self.engine.connect() as connection:
            rows = (await connection.execute(
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
            )).mappings().all()
        return [ModuleRun.model_validate(row["payload"]) for row in rows]

    async def get_conversation_for_domain_session(
        self,
        *,
        user_id: str,
        module_type: ModuleType,
        domain_session_id: str,
    ) -> Conversation | None:
        """Return the conversation already owning one domain session."""
        async with self.engine.connect() as connection:
            row = (await connection.execute(
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
            )).mappings().first()
        return _conversation_from_row(row) if row else None

    async def list_proposals(
        self,
        *,
        conversation_id: str,
        user_id: str,
    ) -> list[ModuleProposal]:
        """Return all owner-scoped proposals for export."""
        async with self.engine.connect() as connection:
            rows = (await connection.execute(
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
            )).mappings().all()
        return [ModuleProposal.model_validate(row["payload"]) for row in rows]

    async def delete_for_user(
        self,
        *,
        conversation_id: str,
        user_id: str,
    ) -> dict[str, int] | None:
        """Delete one conversation and its directly attributable records."""
        async with self.engine.begin() as connection:
            receipt = (await connection.execute(
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
            )).mappings().first()
            if receipt is not None:
                return {
                    key: int(value)
                    for key, value in receipt["deleted_counts"].items()
                }
            owner = (await connection.execute(
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
            )).first()
            if owner is None:
                return None
            event_ids = list(
                (await connection.execute(
                    text(
                        """SELECT event_id FROM conversation_events
                        WHERE conversation_id = :conversation_id
                          AND user_id = :user_id"""
                    ),
                    {
                        "conversation_id": conversation_id,
                        "user_id": user_id,
                    },
                )).scalars()
            )
            run_rows = (await connection.execute(
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
            )).mappings().all()
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
                    (await connection.execute(
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
                    )).scalar_one()
                ),
                "compact_summaries": int(
                    (await connection.execute(
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
                    )).scalar_one()
                ),
                "commands": int(
                    (await connection.execute(
                        text(
                            """SELECT COUNT(*)
                            FROM conversation_commands
                            WHERE conversation_id = :conversation_id
                              AND user_id = :user_id"""
                        ),
                        {
                            "conversation_id": conversation_id,
                            "user_id": user_id,
                        },
                    )).scalar_one()
                ),
                "episodic_memories": 0,
                "memory_proposals": 0,
                "domain_sessions": 0,
            }
            if source_ids:
                memory_ids = list(
                    (await connection.execute(
                        _expanding_text(
                            """SELECT memory_id FROM episodic_memories
                            WHERE user_id = :user_id
                              AND source_id IN :source_ids""",
                            "source_ids",
                        ),
                        {"user_id": user_id, "source_ids": source_ids},
                    )).scalars()
                )
                proposal_ids = list(
                    (await connection.execute(
                        _expanding_text(
                            """SELECT proposal_id FROM memory_proposals
                            WHERE user_id = :user_id
                              AND source_id IN :source_ids""",
                            "source_ids",
                        ),
                        {"user_id": user_id, "source_ids": source_ids},
                    )).scalars()
                )
                subject_ids = [*memory_ids, *proposal_ids]
                if subject_ids:
                    (await connection.execute(
                        _expanding_text(
                            """DELETE FROM memory_events
                            WHERE user_id = :user_id
                              AND subject_id IN :subject_ids""",
                            "subject_ids",
                        ),
                        {"user_id": user_id, "subject_ids": subject_ids},
                    ))
                counts["memory_proposals"] = int(
                    (await connection.execute(
                        _expanding_text(
                            """DELETE FROM memory_proposals
                            WHERE user_id = :user_id
                              AND source_id IN :source_ids""",
                            "source_ids",
                        ),
                        {"user_id": user_id, "source_ids": source_ids},
                    )).rowcount
                    or 0
                )
                counts["episodic_memories"] = int(
                    (await connection.execute(
                        _expanding_text(
                            """DELETE FROM episodic_memories
                            WHERE user_id = :user_id
                              AND source_id IN :source_ids""",
                            "source_ids",
                        ),
                        {"user_id": user_id, "source_ids": source_ids},
                    )).rowcount
                    or 0
                )
            counts["domain_sessions"] = await _delete_postgres_domain_sessions(
                connection,
                user_id=user_id,
                run_rows=run_rows,
            )
            (await connection.execute(
                text(
                    """DELETE FROM conversations
                    WHERE conversation_id = :conversation_id
                      AND user_id = :user_id"""
                ),
                {
                    "conversation_id": conversation_id,
                    "user_id": user_id,
                },
            ))
            counts["conversations"] = 1
            (await connection.execute(
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
            ))
        return counts

    async def delete_all_for_user(self, *, user_id: str) -> dict[str, int]:
        """Delete every user conversation through the scoped delete path."""
        totals: dict[str, int] = {}
        while True:
            page = await self.list_for_user(user_id, limit=100)
            if not page.items:
                break
            for conversation in page.items:
                counts = await self.delete_for_user(
                    conversation_id=conversation.conversation_id,
                    user_id=user_id,
                )
                for key, value in (counts or {}).items():
                    totals[key] = totals.get(key, 0) + value
        return totals

    async def update_module_domain_session(
        self,
        *,
        module_run_id: str,
        conversation_id: str,
        user_id: str,
        expected_version: int,
        domain_session_id: str,
    ) -> ModuleRun:
        """Attach a lazily created domain session with optimistic locking."""
        async with postgres_write_connection(self.engine) as connection:
            row = (await connection.execute(
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
            )).mappings().first()
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
            (await connection.execute(
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
            ))
        return updated

    async def advance_module_run_version(
        self,
        *,
        module_run_id: str,
        conversation_id: str,
        user_id: str,
        expected_version: int,
    ) -> ModuleRun:
        """Advance the durable overlay watermark after one module action."""
        async with self.engine.begin() as connection:
            row = (await connection.execute(
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
            )).mappings().first()
            if row is None:
                raise LookupError("module run not found")
            current = ModuleRun.model_validate(row["payload"])
            if current.version != expected_version:
                raise ConversationConcurrencyError("module run state changed")
            updated = current.model_copy(
                update={"version": current.version + 1}
            )
            (await connection.execute(
                text(
                    """UPDATE conversation_module_runs
                    SET version = :version, payload = CAST(:payload AS jsonb)
                    WHERE module_run_id = :module_run_id"""
                ),
                {
                    "version": updated.version,
                    "payload": updated.model_dump_json(),
                    "module_run_id": module_run_id,
                },
            ))
        return updated

    async def transition_module_run(
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
        async with self.engine.begin() as connection:
            row = (await connection.execute(
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
            )).mappings().first()
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
            (await connection.execute(
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
            ))
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


async def _delete_postgres_domain_sessions(
    connection: AsyncConnection,
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
            (await connection.execute(
                _expanding_text(
                    f"""DELETE FROM {table}
                    WHERE user_id = :user_id
                      AND {id_column} IN :identifiers""",
                    "identifiers",
                ),
                {"user_id": user_id, "identifiers": identifiers},
            )).rowcount
            or 0
        )
    exposure_ids = grouped.get("exposure", [])
    if exposure_ids:
        (await connection.execute(
            _expanding_text(
                """DELETE FROM exposure_attempts
                WHERE user_id = :user_id AND plan_id IN :identifiers""",
                "identifiers",
            ),
            {"user_id": user_id, "identifiers": exposure_ids},
        ))
        deleted += int(
            (await connection.execute(
                _expanding_text(
                    """DELETE FROM exposure_plans
                    WHERE user_id = :user_id AND plan_id IN :identifiers""",
                    "identifiers",
                ),
                {"user_id": user_id, "identifiers": exposure_ids},
            )).rowcount
            or 0
        )
    return deleted
