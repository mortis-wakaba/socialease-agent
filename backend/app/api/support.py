"""FastAPI routes for public support-resource navigation."""

from fastapi import APIRouter

from app.knowledge.service import KnowledgeService
from app.models import RiskLevel
from app.models_knowledge import KnowledgeBaseType
from app.models_support import SupportQueryRequest, SupportQueryResponse
from app.safety.classifier import create_safety_classifier

router = APIRouter(prefix="/support", tags=["support"])
knowledge_service = KnowledgeService()
safety_classifier = create_safety_classifier()


@router.post("/query", response_model=SupportQueryResponse)
async def query_support_resources(
    request: SupportQueryRequest,
) -> SupportQueryResponse:
    """Query verified public support resources unless crisis escalation is required."""
    safety_result = await safety_classifier.classify(request.query)
    if safety_result.risk_level == RiskLevel.CRISIS:
        return SupportQueryResponse(
            answer=(
                "这个输入包含危机风险表达，系统会先暂停普通资源检索。"
                "请立刻联系可信任的人、学校心理中心或当地紧急服务；如果你可能马上伤害自己或他人，"
                "请不要独处，并尽快寻求现场帮助。"
            ),
            citations=[],
            unknown=False,
            confidence=1.0,
            safety_result=safety_result,
            blocked=True,
        )

    response = knowledge_service.query(
        query=request.query,
        kb_type=KnowledgeBaseType.SUPPORT_RESOURCES,
    )
    return SupportQueryResponse(
        answer=response.answer,
        citations=response.citations,
        unknown=response.unknown,
        confidence=response.confidence,
        safety_result=safety_result,
        blocked=False,
    )
