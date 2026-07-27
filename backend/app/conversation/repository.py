"""Repository contract and SQLite adapter for unified conversations."""

from __future__ import annotations

from base64 import urlsafe_b64decode, urlsafe_b64encode
from datetime import UTC, datetime
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
)


class ConversationConcurrencyError(RuntimeError):
    """Raised when an optimistic version or expected state no longer matches."""


class ConversationIdempotencyError(RuntimeError):
    """Raised when an idempotency key is reused for different content."""


class ConversationRepository(Protocol):
    """Persistence contract for owner-scoped conversation timelines."""

    def create(
        self,
        *,
        user_id: str,
        title: str,
        history_notice_version: str = HISTORY_NOTICE_VERSION,
    ) -> Conversation: ...

    def get_for_user(
        self,
        conversation_id: str,
        user_id: str,
    ) -> Conversation | None: ...

    def list_for_user(
        self,
        user_id: str,
        *,
        cursor: str | None = None,
        limit: int = 20,
    ) -> ConversationPage: ...

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
    ) -> ConversationEvent: ...

    def list_events(
        self,
        *,
        conversation_id: str,
        user_id: str,
        cursor: str | None = None,
        limit: int = 50,
    ) -> ConversationEventPage: ...


class SQLiteConversationRepository:
    """SQLite timeline adapter with transactional ordering and idempotency."""

    def __init__(
        self,
        *,
        protector: ConversationContentProtector | None = None,
    ) -> None:
        initialize_database()
        self._protector = protector or configured_content_protector()

    def create(
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

    def get_for_user(
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

    def list_for_user(
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
            current = self.get_for_user(conversation_id, user_id)
            if current is None:
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

    def list_events(
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

    def save_proposal(self, proposal: ModuleProposal) -> ModuleProposal:
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
                existing = self.get_proposal_by_request(
                    conversation_id=proposal.conversation_id,
                    user_id=proposal.user_id,
                    request_hash=proposal.request_hash,
                )
                if existing is not None:
                    return existing
                raise
        saved = self.get_proposal_for_user(
            proposal_id=proposal.proposal_id,
            conversation_id=proposal.conversation_id,
            user_id=proposal.user_id,
        )
        if saved is None:
            raise LookupError("active conversation not found")
        return saved

    def get_proposal_for_user(
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

    def get_proposal_by_request(
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

    def transition_proposal(
        self,
        *,
        proposal_id: str,
        conversation_id: str,
        user_id: str,
        expected_status: ModuleProposalStatus,
        target_status: ModuleProposalStatus,
    ) -> ModuleProposal | None:
        """Atomically consume a pending proposal decision."""
        current = self.get_proposal_for_user(
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

    def create_module_run(self, run: ModuleRun) -> ModuleRun:
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

    def list_module_stack(
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
