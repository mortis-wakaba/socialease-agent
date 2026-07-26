"""Role-play service shared by API routes and harness skills."""

import re

from app.agents.roleplay import RoleplayAgent
from app.db.factory import repository_factory
from app.knowledge.service import KnowledgeService
from app.llm.factory import create_llm_client
from app.memory.roleplay_compactor import RoleplayCompactor
from app.memory.roleplay_context_manager import RoleplayContextManager
from app.memory.roleplay_store import RoleplaySessionStore
from app.memory.session_context_settings import roleplay_session_context_settings
from app.memory.session_context_store import (
    DisabledSessionContextStore,
    RedisSessionContextStore,
)
from app.memory.thread_checkpoint_service import ThreadCheckpointService
from app.memory.token_estimator import create_token_estimator
from app.models import RiskLevel
from app.models_knowledge import KnowledgeBaseType
from app.models_long_term_memory import PracticeThreadStatus
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
from app.models_session_context import context_diagnostics_payload
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
        context_manager: RoleplayContextManager | None = None,
        checkpoint_service: ThreadCheckpointService | None = None,
    ) -> None:
        self.agent = agent or RoleplayAgent(llm_client=create_llm_client())
        self.store = store or RoleplaySessionStore(
            repository=repository_factory().roleplay_repository()
        )
        self.knowledge = knowledge or KnowledgeService()
        self.safety_classifier = safety_classifier or create_safety_classifier()
        settings = roleplay_session_context_settings()
        token_estimator = create_token_estimator(
            backend=settings.tokenizer_backend,
            model_name=settings.tokenizer_model,
        )
        if context_manager is None:
            context_store = (
                RedisSessionContextStore(
                    redis_url=settings.redis_url,
                    socket_timeout_seconds=settings.redis_socket_timeout_seconds,
                )
                if settings.redis_url is not None
                else DisabledSessionContextStore()
            )
            context_manager = RoleplayContextManager(
                store=context_store,
                settings=settings,
                compactor=RoleplayCompactor(
                    llm_client=self.agent.llm_client,
                    target_tokens=settings.compact_target_tokens,
                    token_estimator=token_estimator,
                ),
                token_estimator=token_estimator,
            )
        self.context_manager = context_manager
        self.checkpoint_service = checkpoint_service or ThreadCheckpointService(
            repository=repository_factory().long_term_memory_repository(),
            settings_repository=repository_factory().user_memory_settings_repository(),
            token_estimator=token_estimator,
            active_memory_token_budget=settings.active_checkpoint_max_tokens,
        )

    async def start_session(self, request: RoleplayStartRequest) -> RoleplayStartResponse:
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
        await self.context_manager.initialize(
            user_id=request.user_id,
            session_id=session.session_id,
            opening_message=persisted_opening_message,
        )
        self.checkpoint_service.record_roleplay(
            user_id=request.user_id,
            thread_id=session.session_id,
            scenario=session.scenario,
            current_stage="roleplay_started",
            status=PracticeThreadStatus.ACTIVE,
            reason_code="roleplay_started",
            unresolved_next_step="发送一轮练习回复。",
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
            await self.context_manager.delete(
                user_id=request.user_id,
                session_id=request.session_id,
            )
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
            self.checkpoint_service.record_roleplay(
                user_id=request.user_id,
                thread_id=request.session_id,
                scenario=session.scenario,
                current_stage="paused",
                status=PracticeThreadStatus.PAUSED,
                reason_code="safety_pause",
                unresolved_next_step=None,
            )
            return RoleplayMessageResponse(
                session=updated_session,
                response=persisted_crisis_response,
                safety_result=safety_result,
                blocked=True,
            )

        message_features = derive_roleplay_message_features(request.message)
        session = self.store.append_message(
            session_id=request.session_id,
            user_id=request.user_id,
            role=RoleplayMessageRole.USER,
            content=persistence_gate.persist_text(
                user_id=request.user_id,
                kind=PersistenceKind.ROLEPLAY_MESSAGE,
                text=request.message,
            ).persisted_text,
            features=message_features,
        )
        if session is None:
            raise ServiceNotFoundError("Role-play session not found")
        await self.context_manager.append(
            user_id=request.user_id,
            session_id=request.session_id,
            role=RoleplayMessageRole.USER,
            content=request.message[:8000],
        )

        guidance = (
            session.retrieved_guidance.answer
            if not session.retrieved_guidance.no_guidance_found
            else "No specific guidance found; use general safe role-play scaffolding."
        )
        durable_checkpoint = self.checkpoint_service.restore_roleplay_context(
            user_id=request.user_id,
            thread_id=request.session_id,
            expected_scenario=session.scenario,
        )
        prompt_context = await self.context_manager.build_prompt_context(
            user_id=request.user_id,
            session_id=request.session_id,
            scenario=session.scenario.value,
            difficulty=session.difficulty,
            guidance=guidance,
            current_user_message=request.message,
            fallback_recent_messages=[
                f"{message.role.value}: {message.content}"
                for message in session.messages
            ],
            durable_checkpoint=durable_checkpoint,
        )
        agent_response, llm_usage = await self.agent.next_turn(
            session=session,
            user_message=request.message,
            prompt_context=prompt_context,
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
        await self.context_manager.append(
            user_id=request.user_id,
            session_id=request.session_id,
            role=RoleplayMessageRole.AGENT,
            content=persisted_agent_response[:8000],
        )
        self.checkpoint_service.record_roleplay(
            user_id=request.user_id,
            thread_id=request.session_id,
            scenario=session.scenario,
            current_stage="practice_turn_completed",
            status=PracticeThreadStatus.ACTIVE,
            reason_code=(
                "redis_compaction"
                if prompt_context.diagnostics.compaction_triggered
                else "practice_turn_completed"
            ),
            helpful_strategy_codes=_strategy_codes(message_features),
            unresolved_next_step="继续当前练习，或在准备好后获取反馈。",
            touch_if_unchanged=True,
        )

        return RoleplayMessageResponse(
            session=session,
            response=persisted_agent_response,
            safety_result=safety_result,
            blocked=False,
            llm_usage=llm_usage,
            context_diagnostics=context_diagnostics_payload(
                prompt_context.diagnostics
            ),
        )

    async def get_feedback(self, request: RoleplayFeedbackRequest) -> RoleplayFeedbackResponse:
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
            await self.context_manager.delete(
                user_id=request.user_id,
                session_id=request.session_id,
            )
            self.checkpoint_service.record_roleplay(
                user_id=request.user_id,
                thread_id=request.session_id,
                scenario=session.scenario,
                current_stage="feedback_completed",
                status=PracticeThreadStatus.COMPLETED,
                reason_code="feedback_completed",
                unresolved_next_step=None,
            )
            return RoleplayFeedbackResponse(session=session, feedback=feedback)
        updated_session = self.store.update_status(
            session_id=request.session_id,
            user_id=request.user_id,
            status=RoleplaySessionStatus.COMPLETED,
        )
        if updated_session is None:
            raise ServiceNotFoundError("Role-play session not found")
        await self.context_manager.delete(
            user_id=request.user_id,
            session_id=request.session_id,
        )
        self.checkpoint_service.record_roleplay(
            user_id=request.user_id,
            thread_id=request.session_id,
            scenario=updated_session.scenario,
            current_stage="feedback_completed",
            status=PracticeThreadStatus.COMPLETED,
            reason_code="feedback_completed",
            unresolved_next_step=None,
        )
        return RoleplayFeedbackResponse(session=updated_session, feedback=feedback)

    async def pause_session(self, request: RoleplayPauseRequest) -> RoleplayPauseResponse:
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
            await self.context_manager.pause(
                user_id=request.user_id,
                session_id=request.session_id,
            )
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
        await self.context_manager.pause(
            user_id=request.user_id,
            session_id=request.session_id,
        )
        self.checkpoint_service.record_roleplay(
            user_id=request.user_id,
            thread_id=request.session_id,
            scenario=session.scenario,
            current_stage="paused",
            status=PracticeThreadStatus.PAUSED,
            reason_code="practice_paused",
            unresolved_next_step="恢复后继续当前角色扮演练习。",
        )
        return RoleplayPauseResponse(
            session=session,
            message="已保存角色扮演暂停状态。你可以稍后从历史记录继续查看。",
        )

    async def resume_session(self, request: RoleplayResumeRequest) -> RoleplayResumeResponse:
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
            await self.context_manager.resume(
                user_id=request.user_id,
                session_id=request.session_id,
            )
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
        await self.context_manager.resume(
            user_id=request.user_id,
            session_id=request.session_id,
        )
        self.checkpoint_service.record_roleplay(
            user_id=request.user_id,
            thread_id=request.session_id,
            scenario=session.scenario,
            current_stage="resumed",
            status=PracticeThreadStatus.ACTIVE,
            reason_code="practice_resumed",
            unresolved_next_step="发送一轮练习回复。",
        )
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

    async def delete_user_context(self, user_id: str) -> int:
        """Delete all TTL-bound role-play context for one user."""
        return await self.context_manager.delete_user(user_id=user_id)

    async def context_health(self) -> bool:
        """Return whether the configured session-context backend responds."""
        return await self.context_manager.ping()

    async def close(self) -> None:
        """Close the shared Redis client during application shutdown."""
        await self.context_manager.close()


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


def _strategy_codes(features: RoleplayMessageFeatures) -> list[str]:
    """Map derived non-verbatim practice signals to controlled checkpoint codes."""
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
