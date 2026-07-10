"""Worksheet service shared by API routes and harness skills."""

from app.agents.worksheet import WorksheetAgent
from app.db.factory import repository_factory
from app.knowledge.service import KnowledgeService
from app.llm.factory import create_llm_client
from app.memory.worksheet_store import WorksheetStore
from app.models import RiskLevel
from app.models_knowledge import KnowledgeBaseType
from app.models_worksheet import (
    WORKSHEET_DISCLAIMER,
    WorksheetCreateRequest,
    WorksheetCreateResponse,
    WorksheetFields,
    WorksheetRecord,
)
from app.privacy.persistence_gate import persistence_gate
from app.privacy.policy import PersistenceKind
from app.safety.classifier import BaseSafetyClassifier, create_safety_classifier
from app.safety.crisis import crisis_escalation_response
from app.services.errors import ServiceNotFoundError


WORKSHEET_CRISIS_RESPONSE = crisis_escalation_response(paused_activity="自助练习")


class WorksheetService:
    """Coordinate worksheet safety, extraction, grounding, and persistence."""

    def __init__(
        self,
        agent: WorksheetAgent | None = None,
        store: WorksheetStore | None = None,
        knowledge: KnowledgeService | None = None,
        safety_classifier: BaseSafetyClassifier | None = None,
    ) -> None:
        self.agent = agent or WorksheetAgent(llm_client=create_llm_client())
        self.store = store or WorksheetStore(repository=repository_factory().worksheet_repository())
        self.knowledge = knowledge or KnowledgeService()
        self.safety_classifier = safety_classifier or create_safety_classifier()

    async def create_worksheet(self, request: WorksheetCreateRequest) -> WorksheetCreateResponse:
        """Create a non-medical self-reflection worksheet from a message."""
        safety_result = await self.safety_classifier.classify(request.message)
        if safety_result.risk_level == RiskLevel.CRISIS:
            return WorksheetCreateResponse(
                worksheet=None,
                safety_result=safety_result,
                missing_fields=[],
                gentle_followup_questions=[],
                disclaimer=WORKSHEET_DISCLAIMER,
                blocked=True,
                response=WORKSHEET_CRISIS_RESPONSE,
            )

        fields, missing_fields, followup_questions, llm_usage = await self.agent.create_fields(
            request.message
        )
        rag_response = self.knowledge.query(
            query="CBT 风格反思 情境 自动想法 情绪 强度 证据 替代想法 下一步",
            kb_type=KnowledgeBaseType.SOCIAL_SKILLS,
        )
        worksheet = self.store.create(
            user_id=request.user_id,
            source_message=persistence_gate.persist_text(
                user_id=request.user_id,
                kind=PersistenceKind.WORKSHEET_SOURCE_MESSAGE,
                text=request.message,
            ).persisted_text,
            fields=_persist_worksheet_fields(request.user_id, fields),
            citations=rag_response.citations,
            missing_fields=missing_fields,
            gentle_followup_questions=followup_questions,
        )
        response = "已生成 CBT 风格自助反思练习。你可以把它当作整理社交压力想法的结构化草稿。"
        if missing_fields:
            response = "已先保存草稿，但还有一些信息可以继续补充。"

        return WorksheetCreateResponse(
            worksheet=worksheet,
            safety_result=safety_result,
            missing_fields=missing_fields,
            gentle_followup_questions=followup_questions,
            disclaimer=WORKSHEET_DISCLAIMER,
            blocked=False,
            response=response,
            llm_usage=llm_usage,
        )

    def get_worksheet(self, worksheet_id: str) -> WorksheetRecord:
        """Return a saved worksheet by id."""
        worksheet = self.store.get(worksheet_id)
        if worksheet is None:
            raise ServiceNotFoundError("Worksheet not found")
        return worksheet


worksheet_service = WorksheetService()


def _persist_worksheet_fields(user_id: str, fields: WorksheetFields) -> WorksheetFields:
    """Redact sensitive identifiers from derived worksheet text fields."""

    def safe_text(value: str | None) -> str | None:
        if value is None:
            return None
        return persistence_gate.persist_text(
            user_id=user_id,
            kind=PersistenceKind.WORKSHEET_FIELD,
            text=value,
        ).persisted_text

    return fields.model_copy(
        update={
            "situation": safe_text(fields.situation),
            "automatic_thought": safe_text(fields.automatic_thought),
            "emotion": safe_text(fields.emotion),
            "evidence_for": safe_text(fields.evidence_for),
            "evidence_against": safe_text(fields.evidence_against),
            "alternative_thought": safe_text(fields.alternative_thought),
            "next_action": safe_text(fields.next_action),
        }
    )
