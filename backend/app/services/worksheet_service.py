"""Worksheet service shared by API routes and harness skills."""

from datetime import datetime, timezone
import re
from dataclasses import dataclass

from app.agents.worksheet import WorksheetAgent
from app.db.factory import repository_factory
from app.knowledge.service import KnowledgeService
from app.llm.factory import create_llm_client
from app.memory.worksheet_store import WorksheetStore
from app.memory.redis_settings import redis_task_state_settings
from app.memory.task_session_settings import worksheet_draft_ttl_seconds
from app.memory.task_state_store import (
    DisabledTaskStateStore,
    RedisTaskStateStore,
    TaskStateStore,
    TaskStateStoreUnavailable,
)
from app.models import RiskLevel
from app.models import SafetyResult
from app.models_knowledge import Citation
from app.models_llm import LLMUsage
from app.models_knowledge import KnowledgeBaseType
from app.models_worksheet import (
    WORKSHEET_DISCLAIMER,
    WorksheetCreateRequest,
    WorksheetCreateResponse,
    WorksheetDraftContext,
    WorksheetFields,
    WorksheetRecord,
    WorksheetSupplementRequest,
)
from app.models_conversation_context import ConversationPromptContext
from app.privacy.persistence_gate import persistence_gate
from app.privacy.policy import PersistenceKind
from app.safety.classifier import BaseSafetyClassifier, create_safety_classifier
from app.safety.crisis import crisis_escalation_response
from app.services.errors import ServiceNotFoundError


WORKSHEET_CRISIS_RESPONSE = crisis_escalation_response(paused_activity="自助练习")


@dataclass(frozen=True)
class PreparedWorksheetCreate:
    """Validated worksheet content prepared without durable state changes."""

    request: WorksheetCreateRequest
    safety_result: SafetyResult
    blocked: bool
    source_message: str | None
    fields: WorksheetFields | None
    citations: list[Citation]
    missing_fields: list[str]
    followup_questions: list[str]
    llm_usage: LLMUsage


class WorksheetService:
    """Coordinate worksheet safety, extraction, grounding, and persistence."""

    def __init__(
        self,
        agent: WorksheetAgent | None = None,
        store: WorksheetStore | None = None,
        knowledge: KnowledgeService | None = None,
        safety_classifier: BaseSafetyClassifier | None = None,
        draft_store: TaskStateStore[WorksheetDraftContext] | None = None,
        draft_ttl_seconds: int | None = None,
    ) -> None:
        self.agent = agent or WorksheetAgent(llm_client=create_llm_client())
        self.store = store or WorksheetStore(repository=repository_factory().worksheet_repository())
        self.knowledge = knowledge or KnowledgeService()
        self.safety_classifier = safety_classifier or create_safety_classifier()
        settings = redis_task_state_settings()
        self.draft_store = draft_store or (
            RedisTaskStateStore(
                redis_url=settings.redis_url,
                namespace="worksheet-draft",
                model_type=WorksheetDraftContext,
                socket_timeout_seconds=settings.socket_timeout_seconds,
            )
            if settings.redis_url
            else DisabledTaskStateStore()
        )
        self.draft_ttl_seconds = draft_ttl_seconds or worksheet_draft_ttl_seconds()

    async def create_worksheet(
        self,
        request: WorksheetCreateRequest,
        *,
        conversation_context: ConversationPromptContext | None = None,
        worksheet_id: str | None = None,
    ) -> WorksheetCreateResponse:
        """Create a non-medical self-reflection worksheet from a message."""
        prepared = await self.prepare_worksheet(
            request,
            conversation_context=conversation_context,
        )
        if prepared.blocked:
            return WorksheetCreateResponse(
                worksheet=None,
                safety_result=prepared.safety_result,
                missing_fields=[],
                gentle_followup_questions=[],
                disclaimer=WORKSHEET_DISCLAIMER,
                blocked=True,
                response=WORKSHEET_CRISIS_RESPONSE,
            )
        response = await self.persist_prepared_worksheet(
            prepared,
            worksheet_id=worksheet_id,
        )
        if response.worksheet is not None:
            await self.after_worksheet_commit(response.worksheet)
        return response

    async def prepare_worksheet(
        self,
        request: WorksheetCreateRequest,
        *,
        conversation_context: ConversationPromptContext | None = None,
    ) -> PreparedWorksheetCreate:
        """Run safety, extraction, retrieval, and redaction before a DB transaction."""
        safety_result = await self.safety_classifier.classify(request.message)
        if safety_result.risk_level == RiskLevel.CRISIS:
            return PreparedWorksheetCreate(
                request=request,
                safety_result=safety_result,
                blocked=True,
                source_message=None,
                fields=None,
                citations=[],
                missing_fields=[],
                followup_questions=[],
                llm_usage=safety_result.llm_usage,
            )

        fields, missing_fields, followup_questions, llm_usage = await self.agent.create_fields(
            request.message,
            conversation_context=conversation_context,
        )
        rag_response = self.knowledge.query(
            query="CBT 风格反思 情境 自动想法 情绪 强度 证据 替代想法 下一步",
            kb_type=KnowledgeBaseType.SOCIAL_SKILLS,
        )
        return PreparedWorksheetCreate(
            request=request,
            safety_result=safety_result,
            blocked=False,
            source_message=(
                None
                if request.source_event_id
                else (await persistence_gate.persist_text(
                    user_id=request.user_id,
                    kind=PersistenceKind.WORKSHEET_SOURCE_MESSAGE,
                    text=request.message,
                )).persisted_text
            ),
            fields=await _persist_worksheet_fields(request.user_id, fields),
            citations=rag_response.citations,
            missing_fields=missing_fields,
            followup_questions=followup_questions,
            llm_usage=llm_usage,
        )

    async def persist_prepared_worksheet(
        self,
        prepared: PreparedWorksheetCreate,
        *,
        worksheet_id: str | None = None,
    ) -> WorksheetCreateResponse:
        """Persist prepared worksheet state in the caller's DB transaction."""
        if prepared.blocked or prepared.fields is None:
            raise ValueError("blocked worksheet cannot be persisted")
        worksheet = await self.store.create(
            user_id=prepared.request.user_id,
            source_message=prepared.source_message,
            source_event_id=prepared.request.source_event_id,
            fields=prepared.fields,
            citations=prepared.citations,
            missing_fields=prepared.missing_fields,
            gentle_followup_questions=prepared.followup_questions,
            worksheet_id=worksheet_id,
        )
        response = "已生成 CBT 风格自助反思练习。你可以把它当作整理社交压力想法的结构化草稿。"
        if prepared.missing_fields:
            response = "已先保存草稿，但还有一些信息可以继续补充。"

        return WorksheetCreateResponse(
            worksheet=worksheet,
            safety_result=prepared.safety_result,
            missing_fields=prepared.missing_fields,
            gentle_followup_questions=prepared.followup_questions,
            disclaimer=WORKSHEET_DISCLAIMER,
            blocked=False,
            response=response,
            llm_usage=prepared.llm_usage,
        )

    async def after_worksheet_commit(
        self,
        worksheet: WorksheetRecord,
    ) -> None:
        """Publish the optional Redis draft after the durable worksheet commits."""
        if not worksheet.completed:
            await self._save_draft(worksheet)

    async def get_worksheet(self, worksheet_id: str) -> WorksheetRecord:
        """Return a saved worksheet by id."""
        worksheet = await self.store.get(worksheet_id)
        if worksheet is None:
            raise ServiceNotFoundError("Worksheet not found")
        return worksheet

    async def supplement_worksheet(
        self,
        request: WorksheetSupplementRequest,
        *,
        conversation_context: ConversationPromptContext | None = None,
    ) -> WorksheetCreateResponse:
        """Merge one bounded clarification into a user-owned worksheet draft."""
        worksheet = await self.store.get_for_user(
            request.worksheet_id, request.user_id
        )
        if worksheet is None:
            raise ServiceNotFoundError("Worksheet not found")
        safety_result = await self.safety_classifier.classify(request.message)
        if safety_result.risk_level == RiskLevel.CRISIS:
            await self.draft_store.delete(user_id=request.user_id, task_id=request.worksheet_id)
            return WorksheetCreateResponse(
                worksheet=None,
                safety_result=safety_result,
                blocked=True,
                response=WORKSHEET_CRISIS_RESPONSE,
            )

        patch, _, _, llm_usage = await self.agent.create_fields(
            request.message,
            conversation_context=conversation_context,
        )
        correction_fields = (
            _explicit_worksheet_fields(request.message, self.agent.field_labels)
            if re.search(r"(?:更正|改成|写错|不是.+是|纠正)", request.message)
            else set()
        )
        merged = _merge_worksheet_fields(
            worksheet.fields,
            patch,
            correction_fields=correction_fields,
        )
        missing = [
            field
            for field in self.agent.required_fields
            if getattr(merged, field) in (None, "")
        ]
        questions = [self.agent.followup_questions[field] for field in missing[:4]]
        updated = worksheet.model_copy(
            update={
                "fields": await _persist_worksheet_fields(request.user_id, merged),
                "missing_fields": missing,
                "gentle_followup_questions": questions,
                "updated_at": datetime.now(timezone.utc),
                "completed": not missing,
            },
            deep=True,
        )
        updated = await self.store.save(updated)
        if updated.completed:
            await self.draft_store.delete(user_id=request.user_id, task_id=request.worksheet_id)
        else:
            await self._save_draft(updated)
        return WorksheetCreateResponse(
            worksheet=updated,
            safety_result=safety_result,
            missing_fields=missing,
            gentle_followup_questions=questions,
            blocked=False,
            response=(
                "已更新并完成这份结构化反思草稿。"
                if updated.completed
                else "已把补充内容合并到原反思表，还可以继续补充缺失信息。"
            ),
            llm_usage=llm_usage,
        )

    async def delete_user_context(self, user_id: str) -> int:
        return await self.draft_store.delete_user(user_id=user_id)

    async def close(self) -> None:
        await self.draft_store.close()

    async def context_health(self) -> bool:
        """Return whether the configured worksheet task-state backend responds."""
        return await self.draft_store.ping()

    async def _save_draft(
        self,
        worksheet: WorksheetRecord,
    ) -> None:
        for attempt in range(3):
            try:
                current = await self.draft_store.get(
                    user_id=worksheet.user_id,
                    task_id=worksheet.worksheet_id,
                )
            except TaskStateStoreUnavailable:
                return None
            state = WorksheetDraftContext(
                user_id=worksheet.user_id,
                worksheet_id=worksheet.worksheet_id,
                fields=worksheet.fields,
                missing_fields=worksheet.missing_fields,
                last_question=(
                    worksheet.gentle_followup_questions[0]
                    if worksheet.gentle_followup_questions
                    else None
                ),
                version=(current.version + 1 if current else 1),
                updated_at=datetime.now(timezone.utc),
            )
            try:
                if current is None:
                    await self.draft_store.put(
                        user_id=worksheet.user_id,
                        task_id=worksheet.worksheet_id,
                        state=state,
                        ttl_seconds=self.draft_ttl_seconds,
                    )
                    return None
                saved = await self.draft_store.compare_and_set(
                    user_id=worksheet.user_id,
                    task_id=worksheet.worksheet_id,
                    state=state,
                    expected_version=current.version,
                    ttl_seconds=self.draft_ttl_seconds,
                )
                if saved:
                    return None
            except TaskStateStoreUnavailable:
                return None
        return None

worksheet_service = WorksheetService()


async def _persist_worksheet_fields(
    user_id: str, fields: WorksheetFields
) -> WorksheetFields:
    """Redact sensitive identifiers from derived worksheet text fields."""

    async def safe_text(value: str | None) -> str | None:
        if value is None:
            return None
        return (await persistence_gate.persist_text(
            user_id=user_id,
            kind=PersistenceKind.WORKSHEET_FIELD,
            text=value,
        )).persisted_text

    return fields.model_copy(
        update={
            "situation": await safe_text(fields.situation),
            "automatic_thought": await safe_text(fields.automatic_thought),
            "emotion": await safe_text(fields.emotion),
            "evidence_for": await safe_text(fields.evidence_for),
            "evidence_against": await safe_text(fields.evidence_against),
            "alternative_thought": await safe_text(fields.alternative_thought),
            "next_action": await safe_text(fields.next_action),
        }
    )


def _merge_worksheet_fields(
    current: WorksheetFields,
    patch: WorksheetFields,
    *,
    correction_fields: set[str],
) -> WorksheetFields:
    """Fill missing fields, or overwrite only after an explicit correction signal."""
    values = current.model_dump(mode="python")
    for field, value in patch.model_dump(mode="python").items():
        if value in (None, ""):
            continue
        if values.get(field) in (None, "") or field in correction_fields:
            values[field] = value
    return WorksheetFields.model_validate(values)


def _explicit_worksheet_fields(
    message: str,
    field_labels: dict[str, tuple[str, ...]],
) -> set[str]:
    """Return fields explicitly named by the user in a correction message."""
    return {
        field
        for field, labels in field_labels.items()
        if any(re.search(rf"{re.escape(label)}\s*[:：]", message, re.IGNORECASE) for label in labels)
    }
