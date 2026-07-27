"""Role-play domain state driven by the unified conversation timeline."""

import re
from datetime import datetime, timezone

from app.agents.roleplay import RoleplayAgent
from app.db.factory import repository_factory
from app.knowledge.service import KnowledgeService
from app.llm.factory import create_llm_client
from app.memory.roleplay_store import RoleplaySessionStore
from app.memory.thread_checkpoint_service import ThreadCheckpointService
from app.models import RiskLevel
from app.models_conversation_context import ConversationWorkingContext
from app.models_knowledge import KnowledgeBaseType
from app.models_long_term_memory import PracticeThreadStatus
from app.models_module_overlay import ModuleOverlay, RoleplayOverlay
from app.models_roleplay import (
    RoleplayFeedbackRequest,
    RoleplayFeedbackResponse,
    RoleplayGuidance,
    RoleplayMessageFeatures,
    RoleplayMessageRequest,
    RoleplayMessageResponse,
    RoleplayMessageRole,
    RoleplayPauseRequest,
    RoleplayResumeRequest,
    RoleplaySession,
    RoleplaySessionListResponse,
    RoleplaySessionStatus,
    RoleplayStartRequest,
    RoleplayStartResponse,
)
from app.models_scenario import ScenarioSpec
from app.models_session_context import (
    RoleplayCompactState,
    RoleplayPromptContext,
)
from app.privacy.persistence_gate import persistence_gate
from app.privacy.policy import PersistenceKind
from app.privacy.redaction import detect_sensitive_categories
from app.safety.classifier import BaseSafetyClassifier, create_safety_classifier
from app.safety.crisis import crisis_escalation_response
from app.services.errors import ServiceNotFoundError, ServiceStateError
from app.services.legacy_scenario_migration import project_legacy_scenario
from app.services.scenario_interpreter import ScenarioInterpreter


ROLEPLAY_CRISIS_RESPONSE = (
    "这个输入包含危机风险表达，角色扮演会先暂停。"
    + crisis_escalation_response(paused_activity="角色扮演").split("。", 1)[1]
)


class RoleplayService:
    """Own role-play metadata and derived features, never conversation text."""

    def __init__(
        self,
        agent: RoleplayAgent | None = None,
        store: RoleplaySessionStore | None = None,
        knowledge: KnowledgeService | None = None,
        safety_classifier: BaseSafetyClassifier | None = None,
        checkpoint_service: ThreadCheckpointService | None = None,
        scenario_interpreter: ScenarioInterpreter | None = None,
    ) -> None:
        self.agent = agent or RoleplayAgent(llm_client=create_llm_client())
        self.store = store or RoleplaySessionStore(
            repository=repository_factory().roleplay_repository()
        )
        self.knowledge = knowledge or KnowledgeService()
        self.safety_classifier = safety_classifier or create_safety_classifier()
        if checkpoint_service is None:
            factory = repository_factory()
            checkpoint_service = ThreadCheckpointService(
                repository=factory.long_term_memory_repository(),
                settings_repository=(
                    factory.user_memory_settings_repository()
                ),
            )
        self.checkpoint_service = checkpoint_service
        self.scenario_interpreter = (
            scenario_interpreter or ScenarioInterpreter()
        )

    async def start_conversation_session(
        self,
        request: RoleplayStartRequest,
    ) -> RoleplayStartResponse:
        """Create role-play metadata while the timeline owns the opening turn."""
        safety_result = await self.safety_classifier.classify(
            f"{request.scenario_description}\n{request.practice_goal or ''}"
        )
        if safety_result.risk_level == RiskLevel.CRISIS:
            raise ServiceStateError(ROLEPLAY_CRISIS_RESPONSE)
        scenario = self.scenario_interpreter.interpret(
            description=request.scenario_description,
            practice_goal=request.practice_goal,
        )
        guidance_query = self.agent.guidance_query(scenario)
        rag_response = self.knowledge.query(
            query=guidance_query,
            kb_type=KnowledgeBaseType.SOCIAL_SKILLS,
        )
        guidance = RoleplayGuidance(
            query=guidance_query,
            answer=rag_response.answer,
            citations=rag_response.citations,
            unknown=rag_response.unknown,
            confidence=rag_response.confidence,
            no_guidance_found=rag_response.unknown,
        )
        opening = _persist_roleplay_agent_message(
            request.user_id,
            self.agent.opening(
                scenario=scenario,
                difficulty=request.difficulty,
                guidance=guidance,
            ),
        )
        session = self.store.create(
            user_id=request.user_id,
            scenario_spec=scenario,
            difficulty=request.difficulty,
            retrieved_guidance=guidance,
        )
        self.checkpoint_service.record_roleplay(
            user_id=request.user_id,
            thread_id=session.session_id,
            scenario=scenario,
            current_stage="roleplay_started",
            status=PracticeThreadStatus.ACTIVE,
            reason_code="roleplay_started",
            unresolved_next_step="发送一轮练习回复。",
        )
        return RoleplayStartResponse(
            session=session,
            opening_message=opening,
        )

    async def send_conversation_message(
        self,
        request: RoleplayMessageRequest,
        *,
        context: ConversationWorkingContext,
        overlay: ModuleOverlay,
    ) -> RoleplayMessageResponse:
        """Generate from the shared timeline and persist only derived features."""
        session = self._active_session(request.session_id, request.user_id)
        if not isinstance(overlay.payload, RoleplayOverlay):
            raise ValueError("role-play overlay payload is invalid")
        safety_result = await self.safety_classifier.classify(request.message)
        if safety_result.risk_level == RiskLevel.CRISIS:
            return RoleplayMessageResponse(
                session=session,
                response=ROLEPLAY_CRISIS_RESPONSE,
                safety_result=safety_result,
                blocked=True,
            )

        features = derive_roleplay_message_features(request.message)
        session = self.store.record_features(
            session_id=request.session_id,
            user_id=request.user_id,
            features=features,
        )
        if session is None:
            raise ServiceNotFoundError("Role-play session not found")
        response, llm_usage = await self.agent.next_turn(
            session=session,
            user_message=request.message,
            prompt_context=_shared_roleplay_prompt_context(
                context=context,
                overlay=overlay.payload,
            ),
        )
        response = _persist_roleplay_agent_message(request.user_id, response)
        self.checkpoint_service.record_roleplay(
            user_id=request.user_id,
            thread_id=request.session_id,
            scenario=_session_scenario_spec(session),
            current_stage="practice_turn_completed",
            status=PracticeThreadStatus.ACTIVE,
            reason_code="unified_conversation_timeline",
            helpful_strategy_codes=_strategy_codes(features),
            unresolved_next_step="继续当前练习，或手动结束模块。",
            touch_if_unchanged=True,
        )
        return RoleplayMessageResponse(
            session=session,
            response=response,
            safety_result=safety_result,
            llm_usage=llm_usage,
            context_diagnostics={
                **context.diagnostics.model_dump(mode="json"),
                "context_source": "unified_conversation_timeline",
            },
        )

    async def get_feedback(
        self,
        request: RoleplayFeedbackRequest,
    ) -> RoleplayFeedbackResponse:
        """Return feature-based feedback for an owned role-play session."""
        session = self.store.get_for_user(request.session_id, request.user_id)
        if session is None:
            raise ServiceNotFoundError("Role-play session not found")
        if session.status != RoleplaySessionStatus.COMPLETED:
            raise ServiceStateError(
                "End the role-play module before requesting feedback."
            )
        if not _has_user_turn(session):
            raise ServiceStateError(
                "Role-play feedback requires at least one user practice message."
            )
        feedback = self.agent.feedback(session)
        return RoleplayFeedbackResponse(session=session, feedback=feedback)

    def pause_conversation_session(
        self,
        request: RoleplayPauseRequest,
    ) -> RoleplaySession:
        """Pause role-play metadata without touching conversation context."""
        session = self.store.get_for_user(request.session_id, request.user_id)
        if session is None:
            raise ServiceNotFoundError("Role-play session not found")
        if session.status == RoleplaySessionStatus.COMPLETED:
            raise ServiceStateError("Completed role-play sessions cannot be paused.")
        if session.status != RoleplaySessionStatus.PAUSED:
            updated = self.store.update_status(
                session_id=request.session_id,
                user_id=request.user_id,
                status=RoleplaySessionStatus.PAUSED,
            )
            if updated is None:
                raise ServiceNotFoundError("Role-play session not found")
            session = updated
        return session

    def resume_conversation_session(
        self,
        request: RoleplayResumeRequest,
    ) -> RoleplaySession:
        """Resume role-play metadata without a second context store."""
        session = self.store.get_for_user(request.session_id, request.user_id)
        if session is None:
            raise ServiceNotFoundError("Role-play session not found")
        if session.status == RoleplaySessionStatus.COMPLETED:
            raise ServiceStateError("Completed role-play sessions cannot be resumed.")
        if session.status != RoleplaySessionStatus.ACTIVE:
            updated = self.store.update_status(
                session_id=request.session_id,
                user_id=request.user_id,
                status=RoleplaySessionStatus.ACTIVE,
            )
            if updated is None:
                raise ServiceNotFoundError("Role-play session not found")
            session = updated
        return session

    def complete_conversation_session(
        self,
        *,
        session_id: str,
        user_id: str,
    ) -> RoleplaySession:
        """Mark domain metadata complete when the user ends the module."""
        session = self.store.get_for_user(session_id, user_id)
        if session is None:
            raise ServiceNotFoundError("Role-play session not found")
        if session.status != RoleplaySessionStatus.COMPLETED:
            updated = self.store.update_status(
                session_id=session_id,
                user_id=user_id,
                status=RoleplaySessionStatus.COMPLETED,
            )
            if updated is None:
                raise ServiceNotFoundError("Role-play session not found")
            session = updated
        self._record_terminal_checkpoint(session)
        return session

    def get_session(
        self,
        session_id: str,
        user_id: str,
    ) -> RoleplayStartResponse:
        """Return owner-scoped domain metadata for history views."""
        session = self.store.get_for_user(session_id, user_id)
        if session is None:
            raise ServiceNotFoundError("Role-play session not found")
        opening = session.messages[0].content if session.messages else ""
        return RoleplayStartResponse(session=session, opening_message=opening)

    def list_sessions(
        self,
        user_id: str,
        limit: int = 20,
        offset: int = 0,
    ) -> RoleplaySessionListResponse:
        """Return recent owner-scoped domain sessions."""
        return RoleplaySessionListResponse(
            user_id=user_id,
            sessions=self.store.list_for_user(
                user_id=user_id,
                limit=limit,
                offset=offset,
            ),
        )

    def _active_session(
        self,
        session_id: str,
        user_id: str,
    ) -> RoleplaySession:
        session = self.store.get_for_user(session_id, user_id)
        if session is None:
            raise ServiceNotFoundError("Role-play session not found")
        if session.status != RoleplaySessionStatus.ACTIVE:
            raise ServiceStateError("Role-play session is not active.")
        return session

    def _record_terminal_checkpoint(self, session: RoleplaySession) -> None:
        self.checkpoint_service.record_roleplay(
            user_id=session.user_id,
            thread_id=session.session_id,
            scenario=_session_scenario_spec(session),
            current_stage="feedback_completed",
            status=PracticeThreadStatus.COMPLETED,
            reason_code="feedback_completed",
            unresolved_next_step=None,
        )


roleplay_service = RoleplayService()


def _shared_roleplay_prompt_context(
    *,
    context: ConversationWorkingContext,
    overlay: RoleplayOverlay,
) -> RoleplayPromptContext:
    """Project the one shared allocator result into the role-play prompt."""
    return RoleplayPromptContext(
        recent_messages=[
            f"{event.role.value}: {event.content}"
            for event in context.recent_events
        ][-20:],
        compact_state=RoleplayCompactState(
            user_goal=overlay.practice_goal,
            current_topic=overlay.scenario_summary,
            attempted_phrases=overlay.attempted_phrases,
            counterpart_position=overlay.counterpart_position,
            unresolved_question=overlay.unresolved_question,
            updated_at=datetime.now(timezone.utc),
        ),
        shared_summary=context.compact_summary,
        parent_resume_projections=context.parent_resume_projections,
        retrieved_memories=context.selected_agent_memory[:3],
        diagnostics=context.diagnostics,
    )


def _persist_roleplay_agent_message(user_id: str, message: str) -> str:
    """Redact sensitive identifiers from a generated role-play turn."""
    return persistence_gate.persist_text(
        user_id=user_id,
        kind=PersistenceKind.ROLEPLAY_AGENT_MESSAGE,
        text=message,
    ).persisted_text


def _has_user_turn(session: RoleplaySession) -> bool:
    return bool(session.practice_features) or any(
        message.role == RoleplayMessageRole.USER
        for message in session.messages
    )


def _strategy_codes(features: RoleplayMessageFeatures) -> list[str]:
    candidates = (
        ("reason_given", features.has_reason),
        ("clear_request", features.has_request),
        ("boundary_statement", features.has_boundary_statement),
        ("empathy_marker", features.has_empathy_marker),
        ("specific_anchor", features.has_specific_time_or_place),
        ("polite_opening", features.has_polite_opening),
        ("collaborative_offer", features.has_collaborative_offer),
        ("repair_acknowledgement", features.has_repair_or_acknowledgement),
    )
    return [code for code, present in candidates if present][:8]


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
        has_boundary_statement=any(
            term in normalized for term in boundary_terms
        ),
        has_empathy_marker=any(term in normalized for term in empathy_terms),
        has_specific_time_or_place=any(
            term in normalized for term in specificity_terms
        ),
        has_polite_opening=normalized.startswith(
            ("请", "谢谢", "不好意思", "抱歉", "麻烦")
        ),
        has_collaborative_offer=any(
            term in normalized for term in collaborative_terms
        ),
        has_repair_or_acknowledgement=any(
            term in normalized for term in repair_terms
        ),
        sensitive_detected=detect_sensitive_categories(message),
    )


def _session_scenario_spec(session: RoleplaySession) -> ScenarioSpec:
    if session.scenario_spec is not None:
        return session.scenario_spec
    if session.scenario is None:
        raise ServiceStateError("Role-play session has no scenario contract.")
    return ScenarioInterpreter().interpret(
        description=project_legacy_scenario(session.scenario)
    )


def _count_terms(text: str, terms: tuple[str, ...]) -> int:
    return sum(text.count(term) for term in terms)


def _count_sentences(text: str) -> int:
    parts = [
        part
        for part in re.split(r"[。！？!?；;\n]+", text)
        if part.strip()
    ]
    return len(parts)
