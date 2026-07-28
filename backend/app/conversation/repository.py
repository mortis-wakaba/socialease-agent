"""Repository contract and SQLite adapter for unified conversations."""

from __future__ import annotations

from base64 import urlsafe_b64decode, urlsafe_b64encode
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import json
import sqlite3
from typing import Protocol
from uuid import uuid4

from app.conversation.content_protector import (
    ConversationContentProtector,
    ProtectedContent,
    configured_content_protector,
)
from app.db.engine import connect
from app.db.session import initialize_database
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


class ConversationConcurrencyError(RuntimeError):
    """Raised when an optimistic version or expected state no longer matches."""


class ConversationIdempotencyError(RuntimeError):
    """Raised when an idempotency key is reused for different content."""


@dataclass(frozen=True)
class ConversationCommandClaim:
    """Result of atomically claiming one conversation write command."""

    acquired: bool
    completed_result: str | None = None


@dataclass(frozen=True)
class ModuleStartJob:
    """Lease-owned module startup reconciliation job."""

    module_run_id: str
    conversation_id: str
    user_id: str
    proposal_id: str
    attempt_count: int
    max_attempts: int
    lease_owner: str


class ConversationRepository(Protocol):
    """Persistence contract for owner-scoped conversation timelines."""

    async def create(
        self,
        *,
        user_id: str,
        title: str,
        history_notice_version: str = HISTORY_NOTICE_VERSION,
    ) -> Conversation: ...

    async def get_for_user(
        self,
        conversation_id: str,
        user_id: str,
    ) -> Conversation | None: ...

    async def list_for_user(
        self,
        user_id: str,
        *,
        cursor: str | None = None,
        limit: int = 20,
    ) -> ConversationPage: ...

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
    ) -> ConversationEvent: ...

    async def advance_module_run_version(
        self,
        *,
        module_run_id: str,
        conversation_id: str,
        user_id: str,
        expected_version: int,
    ) -> ModuleRun: ...

    async def list_events(
        self,
        *,
        conversation_id: str,
        user_id: str,
        cursor: str | None = None,
        limit: int = 50,
    ) -> ConversationEventPage: ...

    async def list_recent_events(
        self,
        *,
        conversation_id: str,
        user_id: str,
        limit: int = 64,
    ) -> list[ConversationEvent]: ...

    async def get_event_by_idempotency(
        self,
        *,
        conversation_id: str,
        user_id: str,
        idempotency_key: str,
    ) -> ConversationEvent | None: ...

    async def claim_command(
        self,
        *,
        conversation_id: str,
        user_id: str,
        idempotency_key: str,
        request_hash: str,
    ) -> ConversationCommandClaim: ...

    async def complete_command(
        self,
        *,
        conversation_id: str,
        user_id: str,
        idempotency_key: str,
        result: str,
    ) -> None: ...

    async def get_compact_summary(
        self,
        *,
        conversation_id: str,
        user_id: str,
    ) -> ConversationCompactSummary | None: ...

    async def save_compact_summary(
        self,
        summary: ConversationCompactSummary,
        *,
        expected_version: int | None,
    ) -> ConversationCompactSummary: ...

    async def list_module_stack(
        self,
        *,
        conversation_id: str,
        user_id: str,
    ) -> list[ModuleRun]: ...

    def module_start_transaction(self): ...

    async def begin_module_start(
        self,
        *,
        proposal: ModuleProposal,
        run: ModuleRun,
        parent: ModuleRun | None,
    ) -> ModuleRun: ...

    async def claim_module_start(self, *, module_run_id: str) -> bool: ...

    async def complete_module_start(self, *, module_run_id: str) -> None: ...

    async def retry_module_start(
        self, *, module_run_id: str, error_code: str
    ) -> None: ...

    async def claim_due_module_starts(
        self,
        *,
        worker_id: str,
        limit: int,
        lease_seconds: int,
    ) -> list[ModuleStartJob]: ...

    async def get_module_run_for_user(
        self,
        *,
        module_run_id: str,
        conversation_id: str,
        user_id: str,
    ) -> ModuleRun | None: ...

    async def list_all_module_runs(
        self,
        *,
        conversation_id: str,
        user_id: str,
    ) -> list[ModuleRun]: ...

    async def get_conversation_for_domain_session(
        self,
        *,
        user_id: str,
        module_type: ModuleType,
        domain_session_id: str,
    ) -> Conversation | None: ...

    async def list_proposals(
        self,
        *,
        conversation_id: str,
        user_id: str,
    ) -> list[ModuleProposal]: ...

    async def delete_for_user(
        self,
        *,
        conversation_id: str,
        user_id: str,
    ) -> dict[str, int] | None: ...


class SQLiteConversationRepository:
    """SQLite timeline adapter with transactional ordering and idempotency."""

    def __init__(
        self,
        *,
        protector: ConversationContentProtector | None = None,
    ) -> None:
        initialize_database()
        self._protector = protector or configured_content_protector()

    @asynccontextmanager
    async def module_start_transaction(self):
        """Keep the common coordinator contract for the local demo adapter."""
        yield

    async def create(
        self,
        *,
        user_id: str,
        title: str,
        history_notice_version: str = HISTORY_NOTICE_VERSION,
    ) -> Conversation:
        """Create one persistent user-owned conversation."""
        now = datetime.now(UTC)
        conversation = Conversation(
            conversation_id=uuid4().hex,
            user_id=user_id,
            title=title,
            history_notice_version=history_notice_version,
            created_at=now,
            updated_at=now,
        )
        with connect() as connection:
            connection.execute(
                """INSERT INTO conversations
                (conversation_id, user_id, title, status, active_module_depth,
                 version, history_notice_version, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                _conversation_values(conversation),
            )
        return conversation

    async def get_for_user(
        self,
        conversation_id: str,
        user_id: str,
    ) -> Conversation | None:
        """Return an undeleted conversation only to its owner."""
        with connect() as connection:
            row = connection.execute(
                """SELECT * FROM conversations
                WHERE conversation_id = ? AND user_id = ? AND status != ?""",
                (
                    conversation_id,
                    user_id,
                    ConversationStatus.DELETED.value,
                ),
            ).fetchone()
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
        now = datetime.now(UTC).isoformat()
        connection = connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            owner = connection.execute(
                """SELECT 1 FROM conversations
                WHERE conversation_id = ? AND user_id = ? AND status != ?""",
                (
                    conversation_id,
                    user_id,
                    ConversationStatus.DELETED.value,
                ),
            ).fetchone()
            if owner is None:
                raise LookupError("conversation not found")
            inserted = connection.execute(
                """INSERT OR IGNORE INTO conversation_commands
                (conversation_id, user_id, idempotency_key, request_hash,
                 status, created_at)
                VALUES (?, ?, ?, ?, 'processing', ?)""",
                (
                    conversation_id,
                    user_id,
                    idempotency_key,
                    request_hash,
                    now,
                ),
            )
            if inserted.rowcount == 1:
                connection.commit()
                return ConversationCommandClaim(acquired=True)
            row = connection.execute(
                """SELECT * FROM conversation_commands
                WHERE conversation_id = ? AND idempotency_key = ?""",
                (conversation_id, idempotency_key),
            ).fetchone()
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
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
        with connect() as connection:
            updated = connection.execute(
                """UPDATE conversation_commands
                SET status = 'completed', result_plaintext = ?,
                    result_ciphertext = ?, result_nonce = ?,
                    result_key_version = ?, completed_at = ?
                WHERE conversation_id = ? AND user_id = ?
                  AND idempotency_key = ? AND status = 'processing'""",
                (
                    protected.plaintext,
                    protected.ciphertext,
                    protected.nonce,
                    protected.key_version,
                    datetime.now(UTC).isoformat(),
                    conversation_id,
                    user_id,
                    idempotency_key,
                ),
            )
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
        """List owned conversations with an opaque stable cursor."""
        limit = _validated_limit(limit, maximum=100)
        cursor_values = _decode_conversation_cursor(cursor) if cursor else None
        parameters: list[object] = [user_id, ConversationStatus.DELETED.value]
        cursor_clause = ""
        if cursor_values:
            cursor_clause = (
                "AND (updated_at < ? OR "
                "(updated_at = ? AND conversation_id < ?))"
            )
            parameters.extend(
                [
                    cursor_values[0],
                    cursor_values[0],
                    cursor_values[1],
                ]
            )
        parameters.append(limit + 1)
        with connect() as connection:
            rows = connection.execute(
                f"""SELECT * FROM conversations
                WHERE user_id = ? AND status != ?
                {cursor_clause}
                ORDER BY updated_at DESC, conversation_id DESC
                LIMIT ?""",
                parameters,
            ).fetchall()
        has_more = len(rows) > limit
        page_rows = rows[:limit]
        items = [_conversation_from_row(row) for row in page_rows]
        next_cursor = None
        if has_more and page_rows:
            last = page_rows[-1]
            next_cursor = _encode_conversation_cursor(
                last["updated_at"],
                last["conversation_id"],
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
        updates: list[str] = ["version = version + 1", "updated_at = ?"]
        parameters: list[object] = [datetime.now(UTC).isoformat()]
        for column, value in (
            ("title", title),
            ("status", status.value if status else None),
            ("active_module_depth", active_module_depth),
        ):
            if value is not None:
                updates.append(f"{column} = ?")
                parameters.append(value)
        parameters.extend([conversation_id, user_id, expected_version])
        with connect() as connection:
            result = connection.execute(
                f"""UPDATE conversations SET {", ".join(updates)}
                WHERE conversation_id = ? AND user_id = ? AND version = ?
                  AND status != ?""",
                [*parameters, ConversationStatus.DELETED.value],
            )
        if result.rowcount == 0:
            current = await self.get_for_user(conversation_id, user_id)
            if current is None:
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
        """Append one event with a transactionally allocated sequence number."""
        connection = connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing_row = connection.execute(
                """SELECT * FROM conversation_events
                WHERE conversation_id = ? AND user_id = ?
                  AND idempotency_key = ?""",
                (conversation_id, user_id, idempotency_key),
            ).fetchone()
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
                connection.commit()
                return existing

            owner_row = connection.execute(
                """SELECT conversation_id FROM conversations
                WHERE conversation_id = ? AND user_id = ? AND status = ?""",
                (
                    conversation_id,
                    user_id,
                    ConversationStatus.ACTIVE.value,
                ),
            ).fetchone()
            if owner_row is None:
                raise LookupError("active conversation not found")
            sequence_no = connection.execute(
                """SELECT COALESCE(MAX(sequence_no), 0) + 1 AS next_sequence
                FROM conversation_events WHERE conversation_id = ?""",
                (conversation_id,),
            ).fetchone()["next_sequence"]
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
                """INSERT INTO conversation_events
                (event_id, conversation_id, user_id, sequence_no, event_type,
                 role, content_plaintext, content_ciphertext, content_nonce,
                 content_key_version, structured_payload, module_run_id,
                 parent_module_run_id, idempotency_key, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                _event_values(event, protected),
            )
            connection.execute(
                """UPDATE conversations
                SET version = version + 1, updated_at = ?
                WHERE conversation_id = ? AND user_id = ?""",
                (now.isoformat(), conversation_id, user_id),
            )
            connection.commit()
            return event
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    async def list_events(
        self,
        *,
        conversation_id: str,
        user_id: str,
        cursor: str | None = None,
        limit: int = 50,
    ) -> ConversationEventPage:
        """List an owner's events in timeline order using sequence cursors."""
        limit = _validated_limit(limit, maximum=200)
        after_sequence = _decode_event_cursor(cursor) if cursor else 0
        with connect() as connection:
            owner = connection.execute(
                """SELECT 1 FROM conversations
                WHERE conversation_id = ? AND user_id = ? AND status != ?""",
                (
                    conversation_id,
                    user_id,
                    ConversationStatus.DELETED.value,
                ),
            ).fetchone()
            if owner is None:
                return ConversationEventPage()
            rows = connection.execute(
                """SELECT * FROM conversation_events
                WHERE conversation_id = ? AND user_id = ? AND sequence_no > ?
                ORDER BY sequence_no ASC
                LIMIT ?""",
                (conversation_id, user_id, after_sequence, limit + 1),
            ).fetchall()
        has_more = len(rows) > limit
        page_rows = rows[:limit]
        items = [self._event_from_row(row) for row in page_rows]
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
        with connect() as connection:
            rows = connection.execute(
                """SELECT events.* FROM conversation_events AS events
                JOIN conversations AS conversations
                  ON conversations.conversation_id = events.conversation_id
                WHERE events.conversation_id = ? AND events.user_id = ?
                  AND conversations.user_id = ? AND conversations.status != ?
                ORDER BY events.sequence_no DESC LIMIT ?""",
                (
                    conversation_id,
                    user_id,
                    user_id,
                    ConversationStatus.DELETED.value,
                    limit,
                ),
            ).fetchall()
        return [self._event_from_row(row) for row in reversed(rows)]

    async def get_event_by_idempotency(
        self,
        *,
        conversation_id: str,
        user_id: str,
        idempotency_key: str,
    ) -> ConversationEvent | None:
        """Return one idempotent event inside its complete owner scope."""
        with connect() as connection:
            row = connection.execute(
                """SELECT events.* FROM conversation_events AS events
                JOIN conversations AS conversations
                  ON conversations.conversation_id = events.conversation_id
                WHERE events.conversation_id = ? AND events.user_id = ?
                  AND events.idempotency_key = ?
                  AND conversations.user_id = ? AND conversations.status != ?""",
                (
                    conversation_id,
                    user_id,
                    idempotency_key,
                    user_id,
                    ConversationStatus.DELETED.value,
                ),
            ).fetchone()
        return self._event_from_row(row) if row else None

    async def get_compact_summary(
        self,
        *,
        conversation_id: str,
        user_id: str,
    ) -> ConversationCompactSummary | None:
        """Return the durable summary only to its owner."""
        with connect() as connection:
            row = connection.execute(
                """SELECT summaries.payload
                FROM conversation_context_summaries AS summaries
                JOIN conversations AS conversations
                  ON conversations.conversation_id = summaries.conversation_id
                WHERE summaries.conversation_id = ? AND summaries.user_id = ?
                  AND conversations.user_id = ? AND conversations.status != ?""",
                (
                    conversation_id,
                    user_id,
                    user_id,
                    ConversationStatus.DELETED.value,
                ),
            ).fetchone()
        return (
            ConversationCompactSummary.model_validate_json(row["payload"])
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
        with connect() as connection:
            if expected_version is None:
                try:
                    result = connection.execute(
                        """INSERT INTO conversation_context_summaries
                        (conversation_id, user_id, compacted_through_sequence,
                         version, payload, updated_at)
                        SELECT ?, ?, ?, ?, ?, ?
                        WHERE EXISTS (
                            SELECT 1 FROM conversations
                            WHERE conversation_id = ? AND user_id = ?
                              AND status != ?
                        )""",
                        (
                            summary.conversation_id,
                            summary.user_id,
                            summary.compacted_through_sequence,
                            summary.version,
                            summary.model_dump_json(),
                            summary.updated_at.isoformat(),
                            summary.conversation_id,
                            summary.user_id,
                            ConversationStatus.DELETED.value,
                        ),
                    )
                except sqlite3.IntegrityError as exc:
                    raise ConversationConcurrencyError(
                        "conversation summary already exists"
                    ) from exc
            else:
                result = connection.execute(
                    """UPDATE conversation_context_summaries
                    SET compacted_through_sequence = ?, version = ?,
                        payload = ?, updated_at = ?
                    WHERE conversation_id = ? AND user_id = ? AND version = ?""",
                    (
                        summary.compacted_through_sequence,
                        summary.version,
                        summary.model_dump_json(),
                        summary.updated_at.isoformat(),
                        summary.conversation_id,
                        summary.user_id,
                        expected_version,
                    ),
                )
        if result.rowcount == 0:
            raise ConversationConcurrencyError("conversation summary state changed")
        return summary

    async def save_proposal(self, proposal: ModuleProposal) -> ModuleProposal:
        """Persist a validated proposal, deduplicated by request hash."""
        with connect() as connection:
            try:
                connection.execute(
                    """INSERT INTO conversation_module_proposals
                    (proposal_id, conversation_id, user_id, proposed_module,
                     reason_code, status, request_hash, payload, expires_at,
                     created_at)
                    SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    WHERE EXISTS (
                        SELECT 1 FROM conversations
                        WHERE conversation_id = ? AND user_id = ? AND status = ?
                    )""",
                    (
                        proposal.proposal_id,
                        proposal.conversation_id,
                        proposal.user_id,
                        proposal.proposed_module.value,
                        proposal.reason_code.value,
                        proposal.status.value,
                        proposal.request_hash,
                        proposal.model_dump_json(),
                        proposal.expires_at.isoformat(),
                        proposal.created_at.isoformat(),
                        proposal.conversation_id,
                        proposal.user_id,
                        ConversationStatus.ACTIVE.value,
                    ),
                )
            except sqlite3.IntegrityError:
                existing = await self.get_proposal_by_request(
                    conversation_id=proposal.conversation_id,
                    user_id=proposal.user_id,
                    request_hash=proposal.request_hash,
                )
                if existing is not None:
                    return existing
                raise
        saved = await self.get_proposal_for_user(
            proposal_id=proposal.proposal_id,
            conversation_id=proposal.conversation_id,
            user_id=proposal.user_id,
        )
        if saved is None:
            raise LookupError("active conversation not found")
        return saved

    async def get_proposal_for_user(
        self,
        *,
        proposal_id: str,
        conversation_id: str,
        user_id: str,
    ) -> ModuleProposal | None:
        """Return one proposal only inside its complete owner scope."""
        with connect() as connection:
            row = connection.execute(
                """SELECT payload FROM conversation_module_proposals
                WHERE proposal_id = ? AND conversation_id = ? AND user_id = ?""",
                (proposal_id, conversation_id, user_id),
            ).fetchone()
        return ModuleProposal.model_validate_json(row["payload"]) if row else None

    async def get_proposal_by_request(
        self,
        *,
        conversation_id: str,
        user_id: str,
        request_hash: str,
    ) -> ModuleProposal | None:
        """Return a deduplicated proposal by its scoped request hash."""
        with connect() as connection:
            row = connection.execute(
                """SELECT payload FROM conversation_module_proposals
                WHERE conversation_id = ? AND user_id = ? AND request_hash = ?""",
                (conversation_id, user_id, request_hash),
            ).fetchone()
        return ModuleProposal.model_validate_json(row["payload"]) if row else None

    async def transition_proposal(
        self,
        *,
        proposal_id: str,
        conversation_id: str,
        user_id: str,
        expected_status: ModuleProposalStatus,
        target_status: ModuleProposalStatus,
    ) -> ModuleProposal | None:
        """Atomically consume a pending proposal decision."""
        current = await self.get_proposal_for_user(
            proposal_id=proposal_id,
            conversation_id=conversation_id,
            user_id=user_id,
        )
        if current is None:
            return None
        updated = current.model_copy(update={"status": target_status})
        with connect() as connection:
            result = connection.execute(
                """UPDATE conversation_module_proposals
                SET status = ?, payload = ?
                WHERE proposal_id = ? AND conversation_id = ? AND user_id = ?
                  AND status = ?""",
                (
                    target_status.value,
                    updated.model_dump_json(),
                    proposal_id,
                    conversation_id,
                    user_id,
                    expected_status.value,
                ),
            )
        if result.rowcount == 0:
            raise ConversationConcurrencyError("module proposal state changed")
        return updated

    async def create_module_run(self, run: ModuleRun) -> ModuleRun:
        """Persist a new module frame inside its owner conversation."""
        with connect() as connection:
            result = connection.execute(
                """INSERT INTO conversation_module_runs
                (module_run_id, conversation_id, user_id, module_type,
                 parent_module_run_id, depth, status, domain_session_id,
                 version, payload, started_at, ended_at)
                SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                WHERE EXISTS (
                    SELECT 1 FROM conversations
                    WHERE conversation_id = ? AND user_id = ? AND status = ?
                )""",
                (
                    run.module_run_id,
                    run.conversation_id,
                    run.user_id,
                    run.module_type.value,
                    run.parent_module_run_id,
                    run.depth,
                    run.status.value,
                    run.domain_session_id,
                    run.version,
                    run.model_dump_json(),
                    run.started_at.isoformat(),
                    run.ended_at.isoformat() if run.ended_at else None,
                    run.conversation_id,
                    run.user_id,
                    ConversationStatus.ACTIVE.value,
                ),
            )
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
        with connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            proposal_result = connection.execute(
                """UPDATE conversation_module_proposals
                SET status = ?, payload = ?
                WHERE proposal_id = ? AND conversation_id = ? AND user_id = ?
                  AND status = ?""",
                (
                    ModuleProposalStatus.ACCEPTED.value,
                    accepted.model_dump_json(),
                    proposal.proposal_id,
                    proposal.conversation_id,
                    proposal.user_id,
                    ModuleProposalStatus.PENDING.value,
                ),
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
                parent_result = connection.execute(
                    """UPDATE conversation_module_runs
                    SET status = ?, version = ?, payload = ?
                    WHERE module_run_id = ? AND conversation_id = ? AND user_id = ?
                      AND status = ? AND version = ?""",
                    (
                        ModuleRunStatus.SUSPENDED.value,
                        suspended.version,
                        suspended.model_dump_json(),
                        parent.module_run_id,
                        parent.conversation_id,
                        parent.user_id,
                        ModuleRunStatus.ACTIVE.value,
                        parent.version,
                    ),
                )
                if parent_result.rowcount == 0:
                    raise ConversationConcurrencyError("parent module state changed")
            connection.execute(
                """INSERT INTO conversation_module_runs
                (module_run_id, conversation_id, user_id, module_type,
                 parent_module_run_id, depth, status, domain_session_id,
                 version, payload, started_at, ended_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run.module_run_id,
                    run.conversation_id,
                    run.user_id,
                    run.module_type.value,
                    run.parent_module_run_id,
                    run.depth,
                    run.status.value,
                    run.domain_session_id,
                    run.version,
                    run.model_dump_json(),
                    run.started_at.isoformat(),
                    None,
                ),
            )
            conversation_result = connection.execute(
                """UPDATE conversations
                SET active_module_depth = ?, version = version + 1, updated_at = ?
                WHERE conversation_id = ? AND user_id = ? AND status = ?""",
                (
                    run.depth,
                    now.isoformat(),
                    run.conversation_id,
                    run.user_id,
                    ConversationStatus.ACTIVE.value,
                ),
            )
            if conversation_result.rowcount == 0:
                raise LookupError("active conversation not found")
            connection.execute(
                """INSERT INTO conversation_module_start_outbox
                (module_run_id, conversation_id, user_id, proposal_id, status,
                 attempt_count, next_attempt_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'pending', 0, ?, ?, ?)""",
                (
                    run.module_run_id,
                    run.conversation_id,
                    run.user_id,
                    proposal.proposal_id,
                    now.isoformat(),
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
        return run

    async def claim_module_start(self, *, module_run_id: str) -> bool:
        """Claim a pending/recoverable module startup."""
        now = datetime.now(UTC)
        with connect() as connection:
            result = connection.execute(
                """UPDATE conversation_module_start_outbox
                SET status = 'processing', attempt_count = attempt_count + 1,
                    lease_owner = ?, lease_expires_at = ?, updated_at = ?
                WHERE module_run_id = ?
                  AND (
                    status = 'pending'
                    OR (status = 'processing' AND lease_expires_at <= ?)
                  )""",
                (
                    f"request:{uuid4().hex}",
                    (now + timedelta(seconds=60)).isoformat(),
                    now.isoformat(),
                    module_run_id,
                    now.isoformat(),
                ),
            )
        return result.rowcount == 1

    async def claim_due_module_starts(
        self,
        *,
        worker_id: str,
        limit: int,
        lease_seconds: int,
    ) -> list[ModuleStartJob]:
        """Lease due reconciliation jobs in the local demo database."""
        now = datetime.now(UTC)
        lease_until = now + timedelta(seconds=max(1, lease_seconds))
        jobs: list[ModuleStartJob] = []
        with connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """SELECT module_run_id, conversation_id, user_id, proposal_id,
                          attempt_count, max_attempts
                FROM conversation_module_start_outbox
                WHERE (
                    (status = 'pending' AND
                     COALESCE(next_attempt_at, updated_at) <= ?)
                    OR (status = 'processing' AND lease_expires_at <= ?)
                )
                ORDER BY COALESCE(next_attempt_at, updated_at), created_at
                LIMIT ?""",
                (now.isoformat(), now.isoformat(), max(1, min(limit, 100))),
            ).fetchall()
            for row in rows:
                connection.execute(
                    """UPDATE conversation_module_start_outbox
                    SET status = 'processing', attempt_count = attempt_count + 1,
                        lease_owner = ?, lease_expires_at = ?, updated_at = ?
                    WHERE module_run_id = ?""",
                    (
                        worker_id,
                        lease_until.isoformat(),
                        now.isoformat(),
                        row["module_run_id"],
                    ),
                )
                if row["proposal_id"]:
                    jobs.append(
                        ModuleStartJob(
                            module_run_id=row["module_run_id"],
                            conversation_id=row["conversation_id"],
                            user_id=row["user_id"],
                            proposal_id=row["proposal_id"],
                            attempt_count=int(row["attempt_count"]) + 1,
                            max_attempts=int(row["max_attempts"]),
                            lease_owner=worker_id,
                        )
                    )
        return jobs

    async def complete_module_start(self, *, module_run_id: str) -> None:
        """Mark a module startup side effect reconciled."""
        with connect() as connection:
            connection.execute(
                """UPDATE conversation_module_start_outbox
                SET status = 'completed', last_error_code = NULL,
                    lease_owner = NULL, lease_expires_at = NULL,
                    completed_at = ?, updated_at = ?
                WHERE module_run_id = ?""",
                (
                    datetime.now(UTC).isoformat(),
                    datetime.now(UTC).isoformat(),
                    module_run_id,
                ),
            )

    async def retry_module_start(
        self, *, module_run_id: str, error_code: str
    ) -> None:
        """Return a failed startup to the replay queue without storing details."""
        with connect() as connection:
            row = connection.execute(
                """SELECT attempt_count FROM conversation_module_start_outbox
                WHERE module_run_id = ?""",
                (module_run_id,),
            ).fetchone()
            if row is None:
                return
            attempt = int(row["attempt_count"])
            now = datetime.now(UTC)
            connection.execute(
                """UPDATE conversation_module_start_outbox
                SET status = 'pending', last_error_code = ?,
                    next_attempt_at = ?, lease_owner = NULL,
                    lease_expires_at = NULL, updated_at = ?
                WHERE module_run_id = ?""",
                (
                    error_code[:64],
                    (now + timedelta(seconds=min(300, 2 ** max(0, attempt - 1)))).isoformat(),
                    now.isoformat(),
                    module_run_id,
                ),
            )

    async def list_module_stack(
        self,
        *,
        conversation_id: str,
        user_id: str,
    ) -> list[ModuleRun]:
        """Return active and suspended frames in parent-to-child order."""
        with connect() as connection:
            rows = connection.execute(
                """SELECT payload FROM conversation_module_runs
                WHERE conversation_id = ? AND user_id = ?
                  AND status IN (?, ?)
                ORDER BY depth ASC""",
                (
                    conversation_id,
                    user_id,
                    ModuleRunStatus.ACTIVE.value,
                    ModuleRunStatus.SUSPENDED.value,
                ),
            ).fetchall()
        return [ModuleRun.model_validate_json(row["payload"]) for row in rows]

    async def get_module_run_for_user(
        self,
        *,
        module_run_id: str,
        conversation_id: str,
        user_id: str,
    ) -> ModuleRun | None:
        """Return one module run only inside its complete owner scope."""
        with connect() as connection:
            row = connection.execute(
                """SELECT payload FROM conversation_module_runs
                WHERE module_run_id = ? AND conversation_id = ? AND user_id = ?""",
                (module_run_id, conversation_id, user_id),
            ).fetchone()
        return ModuleRun.model_validate_json(row["payload"]) if row else None

    async def list_all_module_runs(
        self,
        *,
        conversation_id: str,
        user_id: str,
    ) -> list[ModuleRun]:
        """Return every active and terminal module run for export/deletion."""
        with connect() as connection:
            rows = connection.execute(
                """SELECT payload FROM conversation_module_runs
                WHERE conversation_id = ? AND user_id = ?
                ORDER BY depth ASC, started_at ASC""",
                (conversation_id, user_id),
            ).fetchall()
        return [ModuleRun.model_validate_json(row["payload"]) for row in rows]

    async def get_conversation_for_domain_session(
        self,
        *,
        user_id: str,
        module_type: ModuleType,
        domain_session_id: str,
    ) -> Conversation | None:
        """Return the conversation already owning one domain session."""
        with connect() as connection:
            row = connection.execute(
                """SELECT c.* FROM conversations AS c
                JOIN conversation_module_runs AS r
                  ON r.conversation_id = c.conversation_id
                WHERE r.user_id = ? AND r.module_type = ?
                  AND r.domain_session_id = ?
                LIMIT 1""",
                (user_id, module_type.value, domain_session_id),
            ).fetchone()
        return _conversation_from_row(row) if row else None

    async def list_proposals(
        self,
        *,
        conversation_id: str,
        user_id: str,
    ) -> list[ModuleProposal]:
        """Return all owner-scoped module proposals for export."""
        with connect() as connection:
            rows = connection.execute(
                """SELECT payload FROM conversation_module_proposals
                WHERE conversation_id = ? AND user_id = ?
                ORDER BY created_at ASC""",
                (conversation_id, user_id),
            ).fetchall()
        return [
            ModuleProposal.model_validate_json(row["payload"]) for row in rows
        ]

    async def delete_for_user(
        self,
        *,
        conversation_id: str,
        user_id: str,
    ) -> dict[str, int] | None:
        """Delete one conversation and directly attributable durable data."""
        connection = connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            receipt = connection.execute(
                """SELECT deleted_counts
                FROM conversation_deletion_receipts
                WHERE conversation_id = ? AND user_id = ?""",
                (conversation_id, user_id),
            ).fetchone()
            if receipt is not None:
                connection.commit()
                return {
                    key: int(value)
                    for key, value in json.loads(
                        receipt["deleted_counts"]
                    ).items()
                }
            owner = connection.execute(
                """SELECT 1 FROM conversations
                WHERE conversation_id = ? AND user_id = ?""",
                (conversation_id, user_id),
            ).fetchone()
            if owner is None:
                connection.rollback()
                return None
            event_rows = connection.execute(
                """SELECT event_id FROM conversation_events
                WHERE conversation_id = ? AND user_id = ?""",
                (conversation_id, user_id),
            ).fetchall()
            run_rows = connection.execute(
                """SELECT module_type, domain_session_id
                FROM conversation_module_runs
                WHERE conversation_id = ? AND user_id = ?""",
                (conversation_id, user_id),
            ).fetchall()
            event_ids = [row["event_id"] for row in event_rows]
            domain_ids = [
                row["domain_session_id"]
                for row in run_rows
                if row["domain_session_id"]
            ]
            source_ids = [*event_ids, *domain_ids]
            counts = {
                "events": len(event_ids),
                "module_runs": len(run_rows),
                "module_proposals": connection.execute(
                    """SELECT COUNT(*) AS count
                    FROM conversation_module_proposals
                    WHERE conversation_id = ? AND user_id = ?""",
                    (conversation_id, user_id),
                ).fetchone()["count"],
                "compact_summaries": connection.execute(
                    """SELECT COUNT(*) AS count
                    FROM conversation_context_summaries
                    WHERE conversation_id = ? AND user_id = ?""",
                    (conversation_id, user_id),
                ).fetchone()["count"],
                "commands": connection.execute(
                    """SELECT COUNT(*) AS count
                    FROM conversation_commands
                    WHERE conversation_id = ? AND user_id = ?""",
                    (conversation_id, user_id),
                ).fetchone()["count"],
                "episodic_memories": 0,
                "memory_proposals": 0,
                "domain_sessions": 0,
            }
            if source_ids:
                placeholders = ",".join("?" for _ in source_ids)
                memory_ids = [
                    row["memory_id"]
                    for row in connection.execute(
                        f"""SELECT memory_id FROM episodic_memories
                        WHERE user_id = ? AND source_id IN ({placeholders})""",
                        [user_id, *source_ids],
                    ).fetchall()
                ]
                memory_proposal_ids = [
                    row["proposal_id"]
                    for row in connection.execute(
                        f"""SELECT proposal_id FROM memory_proposals
                        WHERE user_id = ? AND source_id IN ({placeholders})""",
                        [user_id, *source_ids],
                    ).fetchall()
                ]
                subject_ids = [*memory_ids, *memory_proposal_ids]
                if subject_ids:
                    subject_placeholders = ",".join("?" for _ in subject_ids)
                    connection.execute(
                        f"""DELETE FROM memory_events
                        WHERE user_id = ? AND subject_id IN
                        ({subject_placeholders})""",
                        [user_id, *subject_ids],
                    )
                counts["memory_proposals"] = connection.execute(
                    f"""DELETE FROM memory_proposals
                    WHERE user_id = ? AND source_id IN ({placeholders})""",
                    [user_id, *source_ids],
                ).rowcount
                counts["episodic_memories"] = connection.execute(
                    f"""DELETE FROM episodic_memories
                    WHERE user_id = ? AND source_id IN ({placeholders})""",
                    [user_id, *source_ids],
                ).rowcount
            counts["domain_sessions"] = _delete_sqlite_domain_sessions(
                connection,
                user_id=user_id,
                run_rows=run_rows,
            )
            connection.execute(
                """DELETE FROM conversations
                WHERE conversation_id = ? AND user_id = ?""",
                (conversation_id, user_id),
            )
            counts["conversations"] = 1
            normalized_counts = {
                key: int(value) for key, value in counts.items()
            }
            connection.execute(
                """INSERT INTO conversation_deletion_receipts
                (conversation_id, user_id, deleted_counts, deleted_at)
                VALUES (?, ?, ?, ?)""",
                (
                    conversation_id,
                    user_id,
                    json.dumps(normalized_counts, separators=(",", ":")),
                    datetime.now(UTC).isoformat(),
                ),
            )
            connection.commit()
            return normalized_counts
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    async def delete_all_for_user(self, *, user_id: str) -> dict[str, int]:
        """Delete all conversations by repeatedly applying scoped deletion."""
        page = await self.list_for_user(user_id, limit=100)
        totals: dict[str, int] = {}
        while True:
            for conversation in page.items:
                counts = await self.delete_for_user(
                    conversation_id=conversation.conversation_id,
                    user_id=user_id,
                )
                for key, value in (counts or {}).items():
                    totals[key] = totals.get(key, 0) + value
            if page.next_cursor is None:
                break
            page = await self.list_for_user(
                user_id,
                cursor=page.next_cursor,
                limit=100,
            )
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
        current = await self.get_module_run_for_user(
            module_run_id=module_run_id,
            conversation_id=conversation_id,
            user_id=user_id,
        )
        if current is None:
            raise LookupError("module run not found")
        updated = current.model_copy(
            update={
                "domain_session_id": domain_session_id,
                "version": current.version + 1,
            }
        )
        with connect() as connection:
            result = connection.execute(
                """UPDATE conversation_module_runs
                SET domain_session_id = ?, version = ?, payload = ?
                WHERE module_run_id = ? AND conversation_id = ? AND user_id = ?
                  AND version = ?""",
                (
                    domain_session_id,
                    updated.version,
                    updated.model_dump_json(),
                    module_run_id,
                    conversation_id,
                    user_id,
                    expected_version,
                ),
            )
        if result.rowcount == 0:
            raise ConversationConcurrencyError("module run state changed")
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
        current = await self.get_module_run_for_user(
            module_run_id=module_run_id,
            conversation_id=conversation_id,
            user_id=user_id,
        )
        if current is None:
            raise LookupError("module run not found")
        updated = current.model_copy(
            update={"version": current.version + 1}
        )
        with connect() as connection:
            result = connection.execute(
                """UPDATE conversation_module_runs
                SET version = ?, payload = ?
                WHERE module_run_id = ? AND conversation_id = ? AND user_id = ?
                  AND version = ?""",
                (
                    updated.version,
                    updated.model_dump_json(),
                    module_run_id,
                    conversation_id,
                    user_id,
                    expected_version,
                ),
            )
        if result.rowcount == 0:
            raise ConversationConcurrencyError("module run state changed")
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
        """Optimistically transition a module frame."""
        with connect() as connection:
            row = connection.execute(
                """SELECT payload FROM conversation_module_runs
                WHERE module_run_id = ? AND conversation_id = ? AND user_id = ?""",
                (module_run_id, conversation_id, user_id),
            ).fetchone()
            if row is None:
                return None
            current = ModuleRun.model_validate_json(row["payload"])
            updated = current.model_copy(
                update={
                    "status": target_status,
                    "ended_at": ended_at,
                    "version": current.version + 1,
                }
            )
            result = connection.execute(
                """UPDATE conversation_module_runs
                SET status = ?, version = ?, ended_at = ?, payload = ?
                WHERE module_run_id = ? AND conversation_id = ? AND user_id = ?
                  AND status = ? AND version = ?""",
                (
                    target_status.value,
                    updated.version,
                    ended_at.isoformat() if ended_at else None,
                    updated.model_dump_json(),
                    module_run_id,
                    conversation_id,
                    user_id,
                    expected_status.value,
                    expected_version,
                ),
            )
        if result.rowcount == 0:
            raise ConversationConcurrencyError("module run state changed")
        return updated

    def _event_from_row(self, row: sqlite3.Row) -> ConversationEvent:
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
        payload = (
            json.loads(row["structured_payload"])
            if row["structured_payload"]
            else None
        )
        return ConversationEvent(
            event_id=row["event_id"],
            conversation_id=row["conversation_id"],
            user_id=row["user_id"],
            sequence_no=row["sequence_no"],
            event_type=row["event_type"],
            role=row["role"],
            content=content,
            structured_payload=payload,
            module_run_id=row["module_run_id"],
            parent_module_run_id=row["parent_module_run_id"],
            idempotency_key=row["idempotency_key"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )


def _conversation_values(conversation: Conversation) -> tuple[object, ...]:
    return (
        conversation.conversation_id,
        conversation.user_id,
        conversation.title,
        conversation.status.value,
        conversation.active_module_depth,
        conversation.version,
        conversation.history_notice_version,
        conversation.created_at.isoformat(),
        conversation.updated_at.isoformat(),
    )


def _conversation_from_row(row: sqlite3.Row) -> Conversation:
    return Conversation(
        conversation_id=row["conversation_id"],
        user_id=row["user_id"],
        title=row["title"],
        status=row["status"],
        active_module_depth=row["active_module_depth"],
        version=row["version"],
        history_notice_version=row["history_notice_version"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def _event_values(
    event: ConversationEvent,
    protected: ProtectedContent,
) -> tuple[object, ...]:
    return (
        event.event_id,
        event.conversation_id,
        event.user_id,
        event.sequence_no,
        event.event_type.value,
        event.role.value,
        protected.plaintext,
        protected.ciphertext,
        protected.nonce,
        protected.key_version,
        (
            event.structured_payload.model_dump_json()
            if event.structured_payload
            else None
        ),
        event.module_run_id,
        event.parent_module_run_id,
        event.idempotency_key,
        event.created_at.isoformat(),
    )


def _event_associated_data(
    event_id: str,
    conversation_id: str,
    user_id: str,
    sequence_no: int,
) -> bytes:
    return (
        f"{event_id}:{conversation_id}:{user_id}:{sequence_no}".encode("utf-8")
    )


def _validate_idempotent_event(
    *,
    existing: ConversationEvent,
    event_type: ConversationEventType,
    role: ConversationEventRole,
    content: str,
    structured_payload: ConversationEventPayload | None,
    module_run_id: str | None,
    parent_module_run_id: str | None,
) -> None:
    proposed_payload = (
        structured_payload.model_dump(mode="json") if structured_payload else None
    )
    existing_payload = (
        existing.structured_payload.model_dump(mode="json")
        if existing.structured_payload
        else None
    )
    if (
        existing.event_type != event_type
        or existing.role != role
        or existing.content != content
        or existing_payload != proposed_payload
        or existing.module_run_id != module_run_id
        or existing.parent_module_run_id != parent_module_run_id
    ):
        raise ConversationIdempotencyError(
            "idempotency key was already used for a different event"
        )


def _validated_limit(limit: int, *, maximum: int) -> int:
    if limit < 1 or limit > maximum:
        raise ValueError(f"limit must be between 1 and {maximum}")
    return limit


def _command_associated_data(
    conversation_id: str,
    user_id: str,
    idempotency_key: str,
) -> bytes:
    """Bind an encrypted command result to its owner and logical command."""
    return (
        f"conversation-command:{conversation_id}:{user_id}:{idempotency_key}"
    ).encode("utf-8")


def _encode_conversation_cursor(updated_at: str, conversation_id: str) -> str:
    raw = json.dumps([updated_at, conversation_id], separators=(",", ":"))
    return urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def _decode_conversation_cursor(cursor: str) -> tuple[str, str]:
    try:
        decoded = json.loads(urlsafe_b64decode(cursor.encode("ascii")))
    except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid conversation cursor") from exc
    if (
        not isinstance(decoded, list)
        or len(decoded) != 2
        or not all(isinstance(value, str) for value in decoded)
    ):
        raise ValueError("invalid conversation cursor")
    return decoded[0], decoded[1]


def _encode_event_cursor(sequence_no: int) -> str:
    return urlsafe_b64encode(str(sequence_no).encode("ascii")).decode("ascii")


def _decode_event_cursor(cursor: str) -> int:
    try:
        sequence_no = int(urlsafe_b64decode(cursor.encode("ascii")).decode("ascii"))
    except (ValueError, UnicodeError) as exc:
        raise ValueError("invalid event cursor") from exc
    if sequence_no < 0:
        raise ValueError("invalid event cursor")
    return sequence_no


def _delete_sqlite_domain_sessions(
    connection: sqlite3.Connection,
    *,
    user_id: str,
    run_rows: list[sqlite3.Row],
) -> int:
    """Delete durable domain rows referenced only by this conversation."""
    grouped: dict[str, list[str]] = {}
    for row in run_rows:
        domain_session_id = row["domain_session_id"]
        if domain_session_id:
            grouped.setdefault(row["module_type"], []).append(domain_session_id)
    deleted = 0
    table_specs = {
        "roleplay": ("roleplay_sessions", "session_id"),
        "worksheet": ("worksheets", "worksheet_id"),
    }
    for module_type, (table, id_column) in table_specs.items():
        identifiers = grouped.get(module_type, [])
        if not identifiers:
            continue
        placeholders = ",".join("?" for _ in identifiers)
        deleted += connection.execute(
            f"""DELETE FROM {table}
            WHERE user_id = ? AND {id_column} IN ({placeholders})""",
            [user_id, *identifiers],
        ).rowcount
    exposure_ids = grouped.get("exposure", [])
    if exposure_ids:
        placeholders = ",".join("?" for _ in exposure_ids)
        connection.execute(
            f"""DELETE FROM exposure_attempts
            WHERE user_id = ? AND plan_id IN ({placeholders})""",
            [user_id, *exposure_ids],
        )
        deleted += connection.execute(
            f"""DELETE FROM exposure_plans
            WHERE user_id = ? AND plan_id IN ({placeholders})""",
            [user_id, *exposure_ids],
        ).rowcount
    return deleted
