"""FastAPI routes for CBT-style self-reflection worksheets."""

from fastapi import APIRouter, HTTPException

from app.agents.worksheet import WorksheetAgent
from app.knowledge.service import KnowledgeService
from app.memory.worksheet_store import worksheet_store
from app.models import RiskLevel
from app.models_knowledge import KnowledgeBaseType
from app.models_worksheet import (
    WORKSHEET_DISCLAIMER,
    WorksheetCreateRequest,
    WorksheetCreateResponse,
    WorksheetRecord,
)
from app.safety.classifier import SafetyClassifier

router = APIRouter(prefix="/worksheet", tags=["worksheet"])
worksheet_agent = WorksheetAgent()
safety_classifier = SafetyClassifier()
knowledge_service = KnowledgeService()


@router.post("/create", response_model=WorksheetCreateResponse)
async def create_worksheet(request: WorksheetCreateRequest) -> WorksheetCreateResponse:
    """Create a CBT-style worksheet from a user message."""
    safety_result = safety_classifier.classify(request.message)
    if safety_result.risk_level == RiskLevel.CRISIS:
        return WorksheetCreateResponse(
            worksheet=None,
            safety_result=safety_result,
            missing_fields=[],
            gentle_followup_questions=[],
            disclaimer=WORKSHEET_DISCLAIMER,
            blocked=True,
            response=(
                "这个输入包含危机风险表达，系统会先暂停自助练习。"
                "请立刻联系可信任的人、学校心理中心或当地紧急服务；如果你可能马上伤害自己或他人，"
                "请不要独处，并尽快寻求现场帮助。"
            ),
        )

    fields, missing_fields, followup_questions = worksheet_agent.create_fields(
        request.message
    )
    rag_response = knowledge_service.query(
        query="CBT 风格反思 情境 自动想法 情绪 强度 证据 替代想法 下一步",
        kb_type=KnowledgeBaseType.SOCIAL_SKILLS,
    )
    worksheet = worksheet_store.create(
        user_id=request.user_id,
        source_message=request.message,
        fields=fields,
        citations=rag_response.citations,
        missing_fields=missing_fields,
        gentle_followup_questions=followup_questions,
    )
    response = (
        "已生成 CBT 风格自助反思练习。你可以把它当作整理社交压力想法的结构化草稿。"
    )
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
    )


@router.get("/{worksheet_id}", response_model=WorksheetRecord)
async def get_worksheet(worksheet_id: str) -> WorksheetRecord:
    """Return a saved worksheet by id."""
    worksheet = worksheet_store.get(worksheet_id)
    if worksheet is None:
        raise HTTPException(status_code=404, detail="Worksheet not found")
    return worksheet
