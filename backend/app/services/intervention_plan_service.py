"""Service for creating session-level intervention plans."""

from datetime import datetime, timezone
from uuid import uuid4

from app.models_intervention import InterventionPlan, InterventionPlanView, InterventionStep, InterventionStepView
from app.memory.intervention_plan_store import InterventionPlanRepository
from app.db.factory import repository_factory
from app.safety.actions import HarnessAction


class InterventionPlanService:
    """Create small, explicit plans for lead harness actions."""

    def __init__(self, store: InterventionPlanRepository | None = None) -> None:
        self.store = store or repository_factory().intervention_plan_repository()

    async def create_for_action(
        self,
        *,
        user_id: str,
        session_id: str,
        harness_action: HarnessAction,
        selected_skill: str,
        requires_consent: bool,
        intensity: int | None = None,
        protocol_id: str | None = None,
    ) -> InterventionPlan:
        """Create and persist a session-level plan for one action."""
        steps = self._steps_for_action(
            harness_action=harness_action,
            selected_skill=selected_skill,
            requires_consent=requires_consent,
            intensity=intensity,
            protocol_id=protocol_id,
        )
        status = "pending_consent" if requires_consent else "completed"
        return await self.store.create(
            user_id=user_id,
            session_id=session_id,
            steps=steps,
            status=status,
            protocol_id=protocol_id,
        )

    async def create_for_exposure_plan(
        self,
        *,
        user_id: str,
        exposure_plan_id: str,
        intensity: int | None,
    ) -> InterventionPlan:
        """Create a traceable account-level plan for direct exposure ladders."""
        existing = await self.store.get_for_session(exposure_plan_id, user_id)
        if existing is not None:
            return existing
        steps = self._steps_for_action(
            harness_action=HarnessAction.CREATE_EXPOSURE_PLAN,
            selected_skill="exposure_planning_skill",
            requires_consent=False,
            intensity=intensity,
            protocol_id=None,
        )
        return await self.store.create(
            user_id=user_id,
            session_id=exposure_plan_id,
            steps=steps,
            status="active",
            protocol_id=None,
        )

    async def get_for_session(self, *, user_id: str, session_id: str) -> InterventionPlan | None:
        """Return an existing intervention plan for a session."""
        return await self.store.get_for_session(session_id, user_id)

    async def get_by_id(self, *, user_id: str, plan_id: str) -> InterventionPlan | None:
        """Return one intervention plan if it belongs to the user."""
        return await self.store.get_by_id_for_user(plan_id, user_id)

    async def get_view_by_id(self, *, user_id: str, plan_id: str) -> InterventionPlanView | None:
        """Return a display-friendly plan view if it belongs to the user."""
        plan = await self.get_by_id(user_id=user_id, plan_id=plan_id)
        return _plan_view(plan) if plan is not None else None

    async def list_views_for_user(self, *, user_id: str, limit: int = 20) -> list[InterventionPlanView]:
        """Return recent display-friendly intervention plan views."""
        return [_plan_view(plan) for plan in await self.store.list_for_user(user_id, limit=limit)]

    async def mark_consent_approved(self, *, user_id: str, plan_id: str) -> InterventionPlan | None:
        """Mark consent-related steps approved while action execution is still pending."""
        plan = await self.store.get_by_id_for_user(plan_id, user_id)
        if plan is None:
            return None
        updated_steps = [
            step.model_copy(update={"status": "completed", "result_summary": "Consent approved."})
            if step.requires_consent
            else step
            for step in plan.steps
        ]
        return await self.store.save(
            plan.model_copy(update={"status": "active", "steps": updated_steps})
        )

    async def mark_consent_rejected(self, *, user_id: str, plan_id: str) -> InterventionPlan | None:
        """Cancel a pending plan after the user rejects consent."""
        plan = await self.store.get_by_id_for_user(plan_id, user_id)
        if plan is None:
            return None
        updated_steps = []
        for step in plan.steps:
            if step.status in {"in_progress", "pending"}:
                updated_steps.append(
                    step.model_copy(
                        update={
                            "status": "cancelled",
                            "result_summary": "Consent rejected; practice was not started.",
                        }
                    )
                )
            else:
                updated_steps.append(step)
        return await self.store.save(
            plan.model_copy(update={"status": "cancelled", "steps": updated_steps})
        )

    async def mark_action_completed(
        self,
        *,
        user_id: str,
        plan_id: str,
        result_session_id: str | None,
        result_summary: str,
    ) -> InterventionPlan | None:
        """Mark the action step completed after an approved skill executes."""
        plan = await self.store.get_by_id_for_user(plan_id, user_id)
        if plan is None:
            return None
        updated_steps = []
        action_step_updated = False
        for step in plan.steps:
            if step.requires_consent:
                updated_steps.append(
                    step.model_copy(update={"status": "completed", "result_summary": "Consent approved."})
                )
                continue
            if step.status == "pending" and not action_step_updated:
                updated_steps.append(
                    step.model_copy(
                        update={
                            "status": "completed",
                            "result_summary": result_summary,
                        }
                    )
                )
                action_step_updated = True
                continue
            updated_steps.append(step)
        return await self.store.save(
            plan.model_copy(
                update={
                    "status": "completed",
                    "session_id": result_session_id or plan.session_id,
                    "steps": updated_steps,
                }
            )
        )

    async def pause_plan(
        self,
        *,
        user_id: str,
        plan_id: str,
        reason: str = "User paused practice.",
    ) -> InterventionPlan | None:
        """Mark an intervention plan as paused without deleting progress."""
        plan = await self.store.get_by_id_for_user(plan_id, user_id)
        if plan is None:
            return None
        updated_steps = []
        for step in plan.steps:
            if step.status in {"pending", "in_progress"}:
                updated_steps.append(
                    step.model_copy(
                        update={
                            "status": "cancelled",
                            "result_summary": reason,
                        }
                    )
                )
            else:
                updated_steps.append(step)
        return await self.store.save(
            plan.model_copy(
                update={
                    "status": "paused",
                    "steps": updated_steps,
                    "updated_at": datetime.now(timezone.utc),
                }
            )
        )

    def _steps_for_action(
        self,
        *,
        harness_action: HarnessAction,
        selected_skill: str,
        requires_consent: bool,
        intensity: int | None,
        protocol_id: str | None,
    ) -> list[InterventionStep]:
        """Build a minimal plan template for a harness action."""
        if harness_action == HarnessAction.START_ROLEPLAY:
            return [
                _step("clarify_scenario", "确认练习场景", "lead_harness", status="completed"),
                _step(
                    "ask_consent",
                    "确认是否开始低强度角色扮演",
                    "lead_harness",
                    status="completed" if not requires_consent else "in_progress",
                    requires_consent=True,
                    protocol_id=protocol_id,
                    stop_condition="用户不同意或想暂停时停止练习。",
                ),
                _step(
                    "start_roleplay",
                    "开始低强度角色扮演",
                    selected_skill,
                    status="pending" if requires_consent else "completed",
                    intensity=intensity,
                    stop_condition="用户表达不适、风险升高或要求停止时暂停。",
                ),
                _step("summarize_next_step", "总结一个下一步练习建议", "lead_harness"),
            ]
        if harness_action == HarnessAction.CREATE_EXPOSURE_PLAN:
            return [
                _step("clarify_target", "确认目标社交场景", "lead_harness", status="completed"),
                _step(
                    "ask_consent",
                    "确认是否生成社交练习计划",
                    "lead_harness",
                    status="completed" if not requires_consent else "in_progress",
                    requires_consent=True,
                    protocol_id=protocol_id,
                    stop_condition="用户不同意或想暂停时停止计划生成。",
                ),
                _step(
                    "create_ladder",
                    "生成由易到难的社交练习阶梯",
                    selected_skill,
                    status="pending" if requires_consent else "completed",
                    intensity=intensity,
                    stop_condition="任何步骤都必须允许 fallback 和暂停。",
                ),
            ]
        return [
            _step("route_request", "识别用户请求", "lead_harness", status="completed"),
            _step("execute_skill", "执行对应安全能力", selected_skill, status="completed"),
        ]


def _step(
    step_id: str,
    title: str,
    skill: str,
    *,
    status: str = "pending",
    intensity: int | None = None,
    requires_consent: bool = False,
    protocol_id: str | None = None,
    stop_condition: str | None = None,
) -> InterventionStep:
    return InterventionStep(
        step_id=f"{step_id}_{uuid4().hex[:8]}",
        title=title,
        skill=skill,
        status=status,  # type: ignore[arg-type]
        intensity=intensity,
        requires_consent=requires_consent,
        protocol_id=protocol_id,
        stop_condition=stop_condition,
    )


def _plan_view(plan: InterventionPlan) -> InterventionPlanView:
    """Convert a persisted plan into a traceable timeline view."""
    current_step_id = _current_step_id(plan)
    timeline = [
        InterventionStepView(
            order=index + 1,
            step_id=step.step_id,
            title=step.title,
            status=step.status,
            skill=step.skill,
            intensity=step.intensity,
            requires_consent=step.requires_consent,
            protocol_id=step.protocol_id,
            stop_condition=step.stop_condition,
            result_summary=step.result_summary,
            is_current=step.step_id == current_step_id,
        )
        for index, step in enumerate(plan.steps)
    ]
    completed_steps = sum(1 for step in plan.steps if step.status == "completed")
    total_steps = len(plan.steps)
    progress_ratio = completed_steps / total_steps if total_steps else 0.0
    return InterventionPlanView(
        plan_id=plan.plan_id,
        user_id=plan.user_id,
        session_id=plan.session_id,
        status=plan.status,
        protocol_id=plan.protocol_id,
        current_step_id=current_step_id,
        completed_steps=completed_steps,
        total_steps=total_steps,
        progress_ratio=round(progress_ratio, 3),
        timeline=timeline,
        created_at=plan.created_at,
        updated_at=plan.updated_at,
    )


def _current_step_id(plan: InterventionPlan) -> str | None:
    """Return the first actionable step in a plan timeline."""
    for step in plan.steps:
        if step.status == "in_progress":
            return step.step_id
    for step in plan.steps:
        if step.status == "pending":
            return step.step_id
    return None


intervention_plan_service = InterventionPlanService()
