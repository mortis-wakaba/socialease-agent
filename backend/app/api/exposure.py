"""FastAPI routes for graded exposure planning."""

from fastapi import APIRouter, HTTPException

from app.agents.exposure import ExposurePlanner
from app.knowledge.service import KnowledgeService
from app.memory.exposure_store import exposure_store
from app.models import RiskLevel
from app.models_knowledge import KnowledgeBaseType
from app.models_exposure import (
    ExposureCompleteRequest,
    ExposureCompleteResponse,
    ExposurePlanRequest,
    ExposurePlanResponse,
    UserExposureResponse,
)
from app.safety.classifier import SafetyClassifier

router = APIRouter(tags=["exposure"])
exposure_planner = ExposurePlanner()
knowledge_service = KnowledgeService()
safety_classifier = SafetyClassifier()


@router.post("/exposure/plan", response_model=ExposurePlanResponse)
async def create_exposure_plan(
    request: ExposurePlanRequest,
) -> ExposurePlanResponse:
    """Create a graded exposure practice plan."""
    safety_text = " ".join(
        [
            request.target_scenario,
            *request.previous_attempts,
        ]
    )
    safety_result = safety_classifier.classify(safety_text)
    if safety_result.risk_level == RiskLevel.CRISIS:
        return ExposurePlanResponse(
            plan=None,
            safety_result=safety_result,
            blocked=True,
            response=(
                "这个输入包含危机风险表达，系统会先暂停社交练习计划。"
                "请立刻联系可信任的人、学校心理中心或当地紧急服务；如果你可能马上伤害自己或他人，"
                "请不要独处，并尽快寻求现场帮助。"
            ),
        )

    rag_response = knowledge_service.query(
        query="分级暴露 社交练习 阶梯 anxiety_before anxiety_after 降低难度 提高难度",
        kb_type=KnowledgeBaseType.SOCIAL_SKILLS,
    )
    tasks = exposure_planner.create_tasks(
        target_scenario=request.target_scenario,
        current_anxiety_level=request.current_anxiety_level,
        previous_attempts=request.previous_attempts,
        citations=rag_response.citations,
    )
    plan = exposure_store.create_plan(
        user_id=request.user_id,
        target_scenario=request.target_scenario,
        current_anxiety_level=request.current_anxiety_level,
        previous_attempts=request.previous_attempts,
        tasks=tasks,
    )
    return ExposurePlanResponse(
        plan=plan,
        safety_result=safety_result,
        blocked=False,
        response=(
            "已生成社交练习计划。请把它当作可调整的小步骤安排，"
            "不是诊断或效果承诺；如果某一步太难，可以使用 fallback_task。"
        ),
    )


@router.post("/exposure/complete", response_model=ExposureCompleteResponse)
async def complete_exposure_task(
    request: ExposureCompleteRequest,
) -> ExposureCompleteResponse:
    """Record task feedback and update the recommended next task."""
    plan = exposure_store.get_for_user(request.user_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="Exposure plan not found")

    next_task, reason = exposure_planner.choose_next_task(
        plan=plan,
        task_id=request.task_id,
        status=request.status,
        anxiety_before=request.anxiety_before,
        anxiety_after=request.anxiety_after,
    )
    if reason.startswith("Task not found"):
        raise HTTPException(status_code=404, detail="Exposure task not found")

    attempt = exposure_planner.create_attempt(
        task_id=request.task_id,
        status=request.status,
        anxiety_before=request.anxiety_before,
        anxiety_after=request.anxiety_after,
        reflection=request.reflection,
    )
    updated_plan = exposure_store.update_after_attempt(
        user_id=request.user_id,
        attempt=attempt,
        recommended_next_task_id=next_task.task_id if next_task else None,
    )
    if updated_plan is None:
        raise HTTPException(status_code=404, detail="Exposure plan not found")

    return ExposureCompleteResponse(
        plan=updated_plan,
        next_task=next_task,
        adjustment_reason=reason,
    )


@router.get("/users/{user_id}/exposure", response_model=UserExposureResponse)
async def get_user_exposure(user_id: str) -> UserExposureResponse:
    """Return the user's active exposure plan and progress."""
    return UserExposureResponse(
        user_id=user_id,
        plan=exposure_store.get_for_user(user_id),
    )
