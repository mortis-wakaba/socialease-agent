"""Role-play service shared by API routes and harness skills."""

import re

from app.agents.roleplay import RoleplayAgent
from app.db.factory import repository_factory
from app.knowledge.service import KnowledgeService
from app.llm.factory import create_llm_client
from app.memory.roleplay_store import RoleplaySessionStore
from app.models import RiskLevel
from app.models_knowledge import KnowledgeBaseType
from app.models_roleplay import (
    RoleplayFeedbackRequest,
    RoleplayFeedbackResponse,
    RoleplayGuidance,
    RoleplayMessageFeatures,
    RoleplayMessageRequest,
    RoleplayMessageResponse,
    RoleplayMessageRole,
    RoleplayPauseRequest,
    RoleplayPauseResponse,
    RoleplayResumeRequest,
    RoleplayResumeResponse,
    RoleplaySessionListResponse,
    RoleplayStartRequest,
    RoleplayStartResponse,
    RoleplaySessionStatus,
)
from app.privacy.persistence_gate import persistence_gate
from app.privacy.redaction import detect_sensitive_categories
from app.privacy.policy import PersistenceKind
from app.safety.classifier import BaseSafetyClassifier, create_safety_classifier
from app.safety.crisis import crisis_escalation_response
from app.services.errors import ServiceNotFoundError, ServiceStateError


ROLEPLAY_CRISIS_RESPONSE = (
    "这个输入包含危机风险表达，角色扮演会先暂停。"
    + crisis_escalation_response(paused_activity="角色扮演").split("。", 1)[1]
)


class RoleplayService:
    """Coordinate role-play RAG, safety checks, agent turns, and persistence."""

    def __init__(
        self,
        agent: RoleplayAgent | None = None,
        store: RoleplaySessionStore | None = None,
        knowledge: KnowledgeService | None = None,
        safety_classifier: BaseSafetyClassifier | None = None,
    ) -> None:
        self.agent = agent or RoleplayAgent(llm_client=create_llm_client())
        self.store = store or RoleplaySessionStore(
            repository=repository_factory().roleplay_repository()
        )
        self.knowledge = knowledge or KnowledgeService()
        self.safety_classifier = safety_classifier or create_safety_classifier()

    def start_session(self, request: RoleplayStartRequest) -> RoleplayStartResponse:
        """Create a grounded role-play session for one supported scenario."""
        guidance_query = self.agent.guidance_query(request.scenario)
        rag_response = self.knowledge.query(
            query=guidance_query,
            kb_type=KnowledgeBaseType.SOCIAL_SKILLS,
        )
        retrieved_guidance = RoleplayGuidance(
            query=guidance_query,
            answer=rag_response.answer,
            citations=rag_response.citations,
            unknown=rag_response.unknown,
            confidence=rag_response.confidence,
            no_guidance_found=rag_response.unknown,
        )
        opening_message = self.agent.opening(
            scenario=request.scenario,
            difficulty=request.difficulty,
            guidance=retrieved_guidance,
        )
        persisted_opening_message = _persist_roleplay_agent_message(
            request.user_id,
            opening_message,
        )
        session = self.store.create(
            user_id=request.user_id,
            scenario=request.scenario,
            difficulty=request.difficulty,
            opening_message=persisted_opening_message,
            retrieved_guidance=retrieved_guidance,
        )
        return RoleplayStartResponse(
            session=session,
            opening_message=persisted_opening_message,
        )

    async def send_message(self, request: RoleplayMessageRequest) -> RoleplayMessageResponse:
        """Append a user message and return the next role-play turn."""
        session = self.store.get_for_user(
            session_id=request.session_id,
            user_id=request.user_id,
        )
        if session is None:
            raise ServiceNotFoundError("Role-play session not found")
        if session.status != RoleplaySessionStatus.ACTIVE:
            raise ServiceStateError("Role-play session is not active.")

        safety_result = await self.safety_classifier.classify(request.message)
        if safety_result.risk_level == RiskLevel.CRISIS:
            persisted_crisis_response = _persist_roleplay_agent_message(
                request.user_id,
                ROLEPLAY_CRISIS_RESPONSE,
            )
            updated_session = self.store.append_message(
                session_id=request.session_id,
                user_id=request.user_id,
                role=RoleplayMessageRole.SYSTEM,
                content=persisted_crisis_response,
            )
            if updated_session is None:
                raise ServiceNotFoundError("Role-play session not found")
            updated_session = self.store.update_status(
                session_id=request.session_id,
                user_id=request.user_id,
                status=RoleplaySessionStatus.PAUSED,
            )
            if updated_session is None:
                raise ServiceNotFoundError("Role-play session not found")
            return RoleplayMessageResponse(
                session=updated_session,
                response=persisted_crisis_response,
                safety_result=safety_result,
                blocked=True,
            )

        session = self.store.append_message(
            session_id=request.session_id,
            user_id=request.user_id,
            role=RoleplayMessageRole.USER,
            content=persistence_gate.persist_text(
                user_id=request.user_id,
                kind=PersistenceKind.ROLEPLAY_MESSAGE,
                text=request.message,
            ).persisted_text,
            features=derive_roleplay_message_features(request.message),
        )
        if session is None:
            raise ServiceNotFoundError("Role-play session not found")

        agent_response, llm_usage = await self.agent.next_turn(
            session=session,
            user_message=request.message,
        )
        persisted_agent_response = _persist_roleplay_agent_message(
            request.user_id,
            agent_response,
        )
        session = self.store.append_message(
            session_id=request.session_id,
            user_id=request.user_id,
            role=RoleplayMessageRole.AGENT,
            content=persisted_agent_response,
        )
        if session is None:
            raise ServiceNotFoundError("Role-play session not found")

        return RoleplayMessageResponse(
            session=session,
            response=persisted_agent_response,
            safety_result=safety_result,
            blocked=False,
            llm_usage=llm_usage,
        )

    def get_feedback(self, request: RoleplayFeedbackRequest) -> RoleplayFeedbackResponse:
        """Return structured feedback for a role-play session."""
        session = self.store.get_for_user(
            session_id=request.session_id,
            user_id=request.user_id,
        )
        if session is None:
            raise ServiceNotFoundError("Role-play session not found")
        if session.status == RoleplaySessionStatus.PAUSED:
            raise ServiceStateError(
                "Role-play session is paused; resume with a new active session before requesting feedback."
            )
        if not _has_user_turn(session):
            raise ServiceStateError(
                "Role-play feedback requires at least one user practice message."
            )
        feedback = self.agent.feedback(session)
        if session.status == RoleplaySessionStatus.COMPLETED:
            return RoleplayFeedbackResponse(session=session, feedback=feedback)
        updated_session = self.store.update_status(
            session_id=request.session_id,
            user_id=request.user_id,
            status=RoleplaySessionStatus.COMPLETED,
        )
        if updated_session is None:
            raise ServiceNotFoundError("Role-play session not found")
        return RoleplayFeedbackResponse(session=updated_session, feedback=feedback)

    def pause_session(self, request: RoleplayPauseRequest) -> RoleplayPauseResponse:
        """Pause a role-play session without deleting its messages."""
        session = self.store.get_for_user(
            session_id=request.session_id,
            user_id=request.user_id,
        )
        if session is None:
            raise ServiceNotFoundError("Role-play session not found")
        if session.status == RoleplaySessionStatus.COMPLETED:
            raise ServiceStateError("Completed role-play sessions cannot be paused.")
        if session.status == RoleplaySessionStatus.PAUSED:
            return RoleplayPauseResponse(
                session=session,
                message="角色扮演已经处于暂停状态。",
            )
        session = self.store.update_status(
            session_id=request.session_id,
            user_id=request.user_id,
            status=RoleplaySessionStatus.PAUSED,
        )
        if session is None:
            raise ServiceNotFoundError("Role-play session not found")
        return RoleplayPauseResponse(
            session=session,
            message="已保存角色扮演暂停状态。你可以稍后从历史记录继续查看。",
        )

    def resume_session(self, request: RoleplayResumeRequest) -> RoleplayResumeResponse:
        """Resume a paused role-play session so the user can continue practice."""
        session = self.store.get_for_user(
            session_id=request.session_id,
            user_id=request.user_id,
        )
        if session is None:
            raise ServiceNotFoundError("Role-play session not found")
        if session.status == RoleplaySessionStatus.COMPLETED:
            raise ServiceStateError("Completed role-play sessions cannot be resumed.")
        if session.status == RoleplaySessionStatus.ACTIVE:
            return RoleplayResumeResponse(
                session=session,
                message="角色扮演已经处于可继续练习状态。",
            )
        session = self.store.update_status(
            session_id=request.session_id,
            user_id=request.user_id,
            status=RoleplaySessionStatus.ACTIVE,
        )
        if session is None:
            raise ServiceNotFoundError("Role-play session not found")
        return RoleplayResumeResponse(
            session=session,
            message="已恢复角色扮演。请先发送一轮练习回复，再获取反馈。",
        )

    def get_session(self, session_id: str, user_id: str) -> RoleplayStartResponse:
        """Return an existing role-play session for page restoration."""
        session = self.store.get_for_user(session_id=session_id, user_id=user_id)
        if session is None:
            raise ServiceNotFoundError("Role-play session not found")
        opening_message = session.messages[0].content if session.messages else ""
        return RoleplayStartResponse(session=session, opening_message=opening_message)

    def list_sessions(self, user_id: str, limit: int = 20) -> RoleplaySessionListResponse:
        """Return recent role-play sessions for history views."""
        return RoleplaySessionListResponse(
            user_id=user_id,
            sessions=self.store.list_for_user(user_id=user_id, limit=limit),
        )


roleplay_service = RoleplayService()


def _persist_roleplay_agent_message(user_id: str, message: str) -> str:
    """Redact sensitive identifiers from persisted role-play agent/system turns."""
    return persistence_gate.persist_text(
        user_id=user_id,
        kind=PersistenceKind.ROLEPLAY_AGENT_MESSAGE,
        text=message,
    ).persisted_text


def _has_user_turn(session) -> bool:
    """Return whether a session has at least one user practice message."""
    return any(message.role == RoleplayMessageRole.USER for message in session.messages)


def derive_roleplay_message_features(message: str) -> RoleplayMessageFeatures:
    """Return privacy-safe, non-verbatim features for role-play feedback."""
    normalized = message.casefold()
    reason_terms = ("因为", "原因", "理由", "所以", "because")
    request_terms = ("请", "可以", "能否", "希望", "麻烦", "would you")
    boundary_terms = (
        "我希望",
        "我不能",
        "我不方便",
        "我暂时不能",
        "我今晚不能",
        "不能帮",
        "不太方便",
        "我更适合",
        "边界",
    )
    empathy_terms = ("理解", "谢谢", "辛苦", "不好意思", "抱歉", "感谢")
    politeness_terms = ("请", "谢谢", "麻烦", "不好意思", "抱歉", "感谢", "可以吗")
    specificity_terms = (
        "今天",
        "明天",
        "今晚",
        "周一",
        "周二",
        "周三",
        "周四",
        "周五",
        "周六",
        "周日",
        "上午",
        "下午",
        "晚上",
        "教室",
        "食堂",
        "宿舍",
        "办公室",
        "点",
    )
    collaborative_terms = ("一起", "我们", "下次", "再", "换个", "商量", "讨论", "约")
    repair_terms = ("我理解", "我知道", "我意识到", "刚才", "抱歉", "不好意思")

    return RoleplayMessageFeatures(
        char_count=len(message),
        sentence_count=_count_sentences(message),
        question_count=message.count("?") + message.count("？"),
        first_person_count=_count_terms(normalized, ("我", "我的", "咱们", "我们")),
        reason_marker_count=_count_terms(normalized, reason_terms),
        request_marker_count=_count_terms(normalized, request_terms),
        boundary_marker_count=_count_terms(normalized, boundary_terms),
        empathy_marker_count=_count_terms(normalized, empathy_terms),
        politeness_marker_count=_count_terms(normalized, politeness_terms),
        specificity_marker_count=_count_terms(normalized, specificity_terms),
        collaborative_marker_count=_count_terms(normalized, collaborative_terms),
        repair_marker_count=_count_terms(normalized, repair_terms),
        has_reason=any(term in normalized for term in reason_terms),
        has_request=any(term in normalized for term in request_terms),
        has_boundary_statement=any(term in normalized for term in boundary_terms),
        has_empathy_marker=any(term in normalized for term in empathy_terms),
        has_specific_time_or_place=any(term in normalized for term in specificity_terms),
        has_polite_opening=normalized.startswith(("请", "谢谢", "不好意思", "抱歉", "麻烦")),
        has_collaborative_offer=any(term in normalized for term in collaborative_terms),
        has_repair_or_acknowledgement=any(term in normalized for term in repair_terms),
        sensitive_detected=detect_sensitive_categories(message),
    )


def _count_terms(text: str, terms: tuple[str, ...]) -> int:
    """Count non-verbatim marker hits without exposing the underlying text."""
    return sum(text.count(term) for term in terms)


def _count_sentences(text: str) -> int:
    """Estimate sentence count from punctuation for rubric-level features."""
    parts = [part for part in re.split(r"[。！？!?；;\n]+", text) if part.strip()]
    return len(parts)
