"""FastAPI routes for role-play practice sessions."""

from fastapi import APIRouter, HTTPException

from app.agents.roleplay import RoleplayAgent
from app.knowledge.service import KnowledgeService
from app.memory.roleplay_store import roleplay_session_store
from app.models import RiskLevel
from app.models_knowledge import KnowledgeBaseType
from app.models_roleplay import (
    RoleplayFeedbackRequest,
    RoleplayFeedbackResponse,
    RoleplayGuidance,
    RoleplayMessageRequest,
    RoleplayMessageResponse,
    RoleplayMessageRole,
    RoleplayStartRequest,
    RoleplayStartResponse,
)
from app.safety.classifier import SafetyClassifier

router = APIRouter(prefix="/roleplay", tags=["roleplay"])
roleplay_agent = RoleplayAgent()
safety_classifier = SafetyClassifier()
knowledge_service = KnowledgeService()


@router.post("/start", response_model=RoleplayStartResponse)
async def start_roleplay(request: RoleplayStartRequest) -> RoleplayStartResponse:
    """Create a role-play session for one supported social scenario."""
    guidance_query = roleplay_agent.guidance_query(request.scenario)
    rag_response = knowledge_service.query(
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
    opening_message = roleplay_agent.opening(
        scenario=request.scenario,
        difficulty=request.difficulty,
        guidance=retrieved_guidance,
    )
    session = roleplay_session_store.create(
        user_id=request.user_id,
        scenario=request.scenario,
        difficulty=request.difficulty,
        opening_message=opening_message,
        retrieved_guidance=retrieved_guidance,
    )
    return RoleplayStartResponse(
        session=session,
        opening_message=opening_message,
    )


@router.post("/message", response_model=RoleplayMessageResponse)
async def send_roleplay_message(
    request: RoleplayMessageRequest,
) -> RoleplayMessageResponse:
    """Append a user message and return the next role-play turn."""
    session = roleplay_session_store.get_for_user(
        session_id=request.session_id,
        user_id=request.user_id,
    )
    if session is None:
        raise HTTPException(status_code=404, detail="Role-play session not found")

    safety_result = safety_classifier.classify(request.message)
    if safety_result.risk_level == RiskLevel.CRISIS:
        crisis_response = (
            "我很担心你现在的安全。角色扮演会先暂停，因为这类表达需要现实支持。"
            "请立刻联系可信任的人、学校心理中心或当地紧急服务；如果你可能马上伤害自己或他人，"
            "请不要独处，并尽快寻求现场帮助。"
        )
        updated_session = roleplay_session_store.append_message(
            session_id=request.session_id,
            user_id=request.user_id,
            role=RoleplayMessageRole.SYSTEM,
            content=crisis_response,
        )
        if updated_session is None:
            raise HTTPException(status_code=404, detail="Role-play session not found")
        return RoleplayMessageResponse(
            session=updated_session,
            response=crisis_response,
            safety_result=safety_result,
            blocked=True,
        )

    session = roleplay_session_store.append_message(
        session_id=request.session_id,
        user_id=request.user_id,
        role=RoleplayMessageRole.USER,
        content=request.message,
    )
    if session is None:
        raise HTTPException(status_code=404, detail="Role-play session not found")

    agent_response = roleplay_agent.next_turn(session=session, user_message=request.message)
    session = roleplay_session_store.append_message(
        session_id=request.session_id,
        user_id=request.user_id,
        role=RoleplayMessageRole.AGENT,
        content=agent_response,
    )
    if session is None:
        raise HTTPException(status_code=404, detail="Role-play session not found")

    return RoleplayMessageResponse(
        session=session,
        response=agent_response,
        safety_result=safety_result,
        blocked=False,
    )


@router.post("/feedback", response_model=RoleplayFeedbackResponse)
async def get_roleplay_feedback(
    request: RoleplayFeedbackRequest,
) -> RoleplayFeedbackResponse:
    """Return structured feedback for a role-play session."""
    session = roleplay_session_store.get_for_user(
        session_id=request.session_id,
        user_id=request.user_id,
    )
    if session is None:
        raise HTTPException(status_code=404, detail="Role-play session not found")

    feedback = roleplay_agent.feedback(session)
    return RoleplayFeedbackResponse(session=session, feedback=feedback)
