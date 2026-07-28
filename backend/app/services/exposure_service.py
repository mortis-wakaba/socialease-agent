"""Exposure planning service shared by API routes and harness skills."""

from dataclasses import dataclass

from app.agents.exposure import ExposurePlanner
from app.db.factory import repository_factory
from app.knowledge.service import KnowledgeService
from app.memory.exposure_store import ExposureStore
from app.db.repositories import SessionReviewRepository
from app.models import RiskLevel, SafetyResult
from app.models_knowledge import KnowledgeBaseType
from app.models_exposure import (
    ExposureCompleteRequest,
    ExposureCompleteResponse,
    ExposurePlanRequest,
    ExposurePlanResponse,
    ExposureTask,
    UserExposureResponse,
)
from app.privacy.persistence_gate import persistence_gate
from app.privacy.policy import PersistenceKind
from app.privacy.redaction import redact_sensitive_identifiers
from app.safety.classifier import BaseSafetyClassifier, create_safety_classifier
from app.safety.crisis import crisis_escalation_response
from app.services.errors import ServiceNotFoundError
from app.services.intervention_plan_service import (
    InterventionPlanService,
    intervention_plan_service,
)


EXPOSURE_CRISIS_RESPONSE = crisis_escalation_response(paused_activity="社交练习计划")


@dataclass(frozen=True)
class PreparedExposurePlan:
    """Validated exposure plan inputs prepared without durable writes."""

    request: ExposurePlanRequest
    safety_result: SafetyResult
    blocked: bool
    target_scenario: str | None
    previous_attempts: list[str]
    tasks: list[ExposureTask]
    used_review_summaries: bool


class ExposureService:
    """Coordinate exposure-plan safety, grounding, persistence, and feedback."""

    def __init__(
        self,
        planner: ExposurePlanner | None = None,
        store: ExposureStore | None = None,
        knowledge: KnowledgeService | None = None,
        safety_classifier: BaseSafetyClassifier | None = None,
        session_review_repository: SessionReviewRepository | None = None,
        intervention_service: InterventionPlanService | None = None,
    ) -> None:
        self.planner = planner or ExposurePlanner()
        self.store = store or ExposureStore(repository=repository_factory().exposure_repository())
        self.knowledge = knowledge or KnowledgeService()
        self.safety_classifier = safety_classifier or create_safety_classifier()
        self.session_review_repository = (
            session_review_repository
            or repository_factory().session_review_repository()
        )
        self.intervention_service = intervention_service or intervention_plan_service

    async def create_plan(
        self,
        request: ExposurePlanRequest,
        *,
        plan_id: str | None = None,
    ) -> ExposurePlanResponse:
        """Create a graded, stoppable social-practice plan."""
        prepared = await self.prepare_plan(request)
        if prepared.blocked:
            return ExposurePlanResponse(
                plan=None,
                safety_result=prepared.safety_result,
                blocked=True,
                response=EXPOSURE_CRISIS_RESPONSE,
            )
        return await self.persist_prepared_plan(prepared, plan_id=plan_id)

    async def prepare_plan(
        self,
        request: ExposurePlanRequest,
    ) -> PreparedExposurePlan:
        """Run safety, retrieval, redaction, and planning outside a DB transaction."""
        safety_text = " ".join([request.target_scenario, *request.previous_attempts])
        safety_result = await self.safety_classifier.classify(safety_text)
        if safety_result.risk_level == RiskLevel.CRISIS:
            return PreparedExposurePlan(
                request=request,
                safety_result=safety_result,
                blocked=True,
                target_scenario=None,
                previous_attempts=[],
                tasks=[],
                used_review_summaries=False,
            )

        rag_response = self.knowledge.query(
            query="分级暴露 社交练习 阶梯 anxiety_before anxiety_after 降低难度 提高难度",
            kb_type=KnowledgeBaseType.SOCIAL_SKILLS,
        )
        persisted_target_scenario = (await persistence_gate.persist_text(
            user_id=request.user_id,
            kind=PersistenceKind.EXPOSURE_TARGET_SCENARIO,
            text=request.target_scenario,
        )).persisted_text
        persisted_previous_attempts = await persistence_gate.persist_texts(
            user_id=request.user_id,
            kind=PersistenceKind.EXPOSURE_PREVIOUS_ATTEMPT,
            texts=request.previous_attempts,
        )
        review_summaries = await self._recent_review_summaries(request.user_id)
        tasks = self.planner.create_tasks(
            target_scenario=persisted_target_scenario,
            current_anxiety_level=request.current_anxiety_level,
            previous_attempts=[*persisted_previous_attempts, *review_summaries],
            citations=rag_response.citations,
        )
        return PreparedExposurePlan(
            request=request,
            safety_result=safety_result,
            blocked=False,
            target_scenario=persisted_target_scenario,
            previous_attempts=persisted_previous_attempts,
            tasks=tasks,
            used_review_summaries=bool(review_summaries),
        )

    async def persist_prepared_plan(
        self,
        prepared: PreparedExposurePlan,
        *,
        plan_id: str | None = None,
    ) -> ExposurePlanResponse:
        """Persist the exposure and linked intervention plans atomically."""
        if prepared.blocked or prepared.target_scenario is None:
            raise ValueError("blocked exposure plan cannot be persisted")
        plan = await self.store.create_plan(
            user_id=prepared.request.user_id,
            target_scenario=prepared.target_scenario,
            current_anxiety_level=prepared.request.current_anxiety_level,
            previous_attempts=prepared.previous_attempts,
            tasks=prepared.tasks,
            plan_id=plan_id,
        )
        intervention_plan = await self.intervention_service.create_for_exposure_plan(
            user_id=prepared.request.user_id,
            exposure_plan_id=plan.plan_id,
            intensity=prepared.request.current_anxiety_level,
        )
        intervention_view = await self.intervention_service.get_view_by_id(
            user_id=prepared.request.user_id,
            plan_id=intervention_plan.plan_id,
        )
        return ExposurePlanResponse(
            plan=plan,
            intervention_plan_id=intervention_plan.plan_id,
            intervention_plan=intervention_view,
            safety_result=prepared.safety_result,
            blocked=False,
            response=(
                "已生成社交练习计划。"
                + (
                    "已参考最近复盘中的下一步偏好。"
                    if prepared.used_review_summaries
                    else ""
                )
                + "请把它当作可调整的小步骤安排，"
                "不是诊断或效果承诺；如果某一步太难，可以使用 fallback_task。"
            ),
        )

    async def complete_task(self, request: ExposureCompleteRequest) -> ExposureCompleteResponse:
        """Record task feedback and update the recommended next task."""
        plan = await self.store.get_for_user(request.user_id)
        if plan is None:
            raise ServiceNotFoundError("Exposure plan not found")

        safety_result = await self.safety_classifier.classify(request.reflection)
        if safety_result.risk_level == RiskLevel.CRISIS:
            return ExposureCompleteResponse(
                plan=plan,
                next_task=None,
                adjustment_reason=(
                    "Crisis-risk reflection paused exposure progression; no attempt was saved."
                ),
                safety_result=safety_result,
                blocked=True,
                response=EXPOSURE_CRISIS_RESPONSE,
            )

        next_task, reason = self.planner.choose_next_task(
            plan=plan,
            task_id=request.task_id,
            status=request.status,
            anxiety_before=request.anxiety_before,
            anxiety_after=request.anxiety_after,
            permission_to_increase=request.permission_to_increase,
        )
        if reason.startswith("Task not found"):
            raise ServiceNotFoundError("Exposure task not found")

        attempt = self.planner.create_attempt(
            task_id=request.task_id,
            status=request.status,
            anxiety_before=request.anxiety_before,
            anxiety_after=request.anxiety_after,
            reflection=(await persistence_gate.persist_text(
                user_id=request.user_id,
                kind=PersistenceKind.EXPOSURE_REFLECTION,
                text=request.reflection,
            )).persisted_text,
        )
        updated_plan = await self.store.update_after_attempt(
            user_id=request.user_id,
            attempt=attempt,
            recommended_next_task_id=next_task.task_id if next_task else None,
        )
        if updated_plan is None:
            raise ServiceNotFoundError("Exposure plan not found")

        return ExposureCompleteResponse(
            plan=updated_plan,
            next_task=next_task,
            adjustment_reason=reason,
            safety_result=safety_result,
            blocked=False,
            response="已记录这次社交练习反馈，并根据你的反馈调整下一步建议。",
        )

    async def get_user_plan(self, user_id: str) -> UserExposureResponse:
        """Return one user's active exposure plan and progress."""
        plan = await self.store.get_for_user(user_id)
        return UserExposureResponse(
            user_id=user_id,
            plan=plan,
            **await self._linked_intervention_payload(
                user_id=user_id, plan_id=plan.plan_id if plan else None
            ),
        )

    async def get_plan_by_id(
        self, plan_id: str, user_id: str
    ) -> UserExposureResponse:
        """Return one exposure plan by id if it belongs to the user."""
        plan = await self.store.get_by_id_for_user(
            plan_id=plan_id, user_id=user_id
        )
        if plan is None:
            raise ServiceNotFoundError("Exposure plan not found")
        return UserExposureResponse(
            user_id=user_id,
            plan=plan,
            **await self._linked_intervention_payload(
                user_id=user_id, plan_id=plan.plan_id
            ),
        )

    async def _recent_review_summaries(self, user_id: str) -> list[str]:
        """Return privacy-safe summaries from recent session reviews."""
        reviews = await self.session_review_repository.list_for_user(user_id, limit=3)
        summaries: list[str] = []
        for review in reviews:
            safe_next_step, _ = redact_sensitive_identifiers(review.next_step_summary)
            summaries.append(
                "最近复盘："
                f"{review.completed}，焦虑 {review.anxiety_before}->{review.anxiety_after}，"
                f"下一步 {safe_next_step}"
            )
        return summaries

    async def _linked_intervention_payload(
        self,
        *,
        user_id: str,
        plan_id: str | None,
    ) -> dict[str, object | None]:
        """Return the intervention plan linked to one direct exposure plan."""
        if plan_id is None:
            return {"intervention_plan_id": None, "intervention_plan": None}
        intervention = await self.intervention_service.get_for_session(
            user_id=user_id,
            session_id=plan_id,
        )
        if intervention is None:
            return {"intervention_plan_id": None, "intervention_plan": None}
        view = await self.intervention_service.get_view_by_id(
            user_id=user_id,
            plan_id=intervention.plan_id,
        )
        return {
            "intervention_plan_id": intervention.plan_id,
            "intervention_plan": view,
        }


exposure_service = ExposureService()
