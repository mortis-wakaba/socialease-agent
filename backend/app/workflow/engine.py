"""Agent harness connecting safety, routing, skills, memory, and tracing."""

from collections.abc import Iterable
from datetime import datetime, timezone
import hashlib
import inspect
import json
import logging
from time import perf_counter
from uuid import uuid4

from app.llm.factory import create_llm_client
from app.guardrails.output import (
    GroundingMetadata,
    OutputGuardrail,
    OutputGuardrailAction,
    OutputGuardrailResult,
    create_output_guardrail,
)
from app.db.factory import repository_factory
from app.db.repositories import UserProfileRepository
from app.memory.settings_store import UserMemorySettingsRepository
from app.memory.active_memory_assembler import ActiveMemoryAssembler
from app.memory.policy_engine import MemoryPolicyEngine
from app.memory.proposal_extractor import MemoryProposalExtractor
from app.memory.write_pipeline import MemoryWritePipeline
from app.models_long_term_memory import MemorySourceType
from app.models_scenario import ScenarioSpec
from app.models import (
    ChatRequest,
    ChatResponse,
    Intent,
    IntentResult,
    RiskLevel,
    SafetyResult,
    TraceFieldPolicy,
    TracePrivacySummary,
    TraceRecord,
)
from app.models_conversation import ConversationEventRole
from app.models_conversation_context import ConversationPromptContext
from app.models_protocols import ProtocolStatus
from app.models_intervention import InterventionPlan
from app.safety.classifier import BaseSafetyClassifier, create_safety_classifier
from app.safety.actions import HarnessAction
from app.safety.permissions import PermissionAction, PermissionDecision, SafetyPermissionGate
from app.skills import SkillContext, SkillRegistry, SkillResult
from app.tracing.logger import TraceLogger
from app.tracing.versions import build_execution_version_info
from app.memory.context_builder import build_memory_context
from app.memory.context_selector import select_skill_context
from app.protocols.service import protocol_service
from app.privacy.persistence_gate import persistence_gate
from app.privacy.policy import PersistenceKind
from app.request_context import get_request_id
from app.services.intervention_plan_service import intervention_plan_service
from app.workflow.context import RunContext
from app.workflow.events import (
    WorkflowEventSink,
    WorkflowProgressEvent,
    WorkflowStage,
)
from app.workflow.hooks import AgentHarnessHook, HookDecision
from app.workflow.recovery import (
    ErrorCategory,
    categorize_error,
    format_observability_error,
    format_trace_error,
    skill_failure_result,
)
from app.workflow.router import BaseIntentRouter, IntentRouter, LlmIntentRouter
from app.workflow.response_constraints import extract_response_constraints


logger = logging.getLogger(__name__)


class AgentHarness:
    """Safety-aware runtime harness for one SocialEase agent run."""

    def __init__(
        self,
        trace_logger: TraceLogger,
        safety_classifier: BaseSafetyClassifier | None = None,
        intent_router: BaseIntentRouter | None = None,
        skill_registry: SkillRegistry | None = None,
        permission_gate: SafetyPermissionGate | None = None,
        hooks: Iterable[AgentHarnessHook] | None = None,
        user_profile_repository: UserProfileRepository | None = None,
        memory_settings_repository: UserMemorySettingsRepository | None = None,
        output_guardrail: OutputGuardrail | None = None,
        memory_write_pipeline: MemoryWritePipeline | None = None,
        active_memory_assembler: ActiveMemoryAssembler | None = None,
    ) -> None:
        self.trace_logger = trace_logger
        self.safety_classifier = safety_classifier or create_safety_classifier()
        self.intent_router = intent_router or self._default_intent_router()
        self.skill_registry = skill_registry or SkillRegistry()
        self.permission_gate = permission_gate or SafetyPermissionGate()
        self.output_guardrail = output_guardrail or create_output_guardrail()
        self.hooks = tuple(hooks or ())
        factory = repository_factory()
        self.user_profile_repository = user_profile_repository or factory.user_profile_repository()
        self.memory_settings_repository = (
            memory_settings_repository or factory.user_memory_settings_repository()
        )
        self.active_memory_assembler = (
            active_memory_assembler or ActiveMemoryAssembler()
        )
        self.memory_write_pipeline = memory_write_pipeline or MemoryWritePipeline(
            extractor=MemoryProposalExtractor(create_llm_client()),
            policy_engine=MemoryPolicyEngine(),
            memory_repository=factory.long_term_memory_repository(),
            proposal_repository=factory.memory_proposal_repository(),
            settings_repository=self.memory_settings_repository,
        )

    async def run(
        self,
        request: ChatRequest,
        event_sink: WorkflowEventSink | None = None,
        *,
        trusted_safety_result: SafetyResult | None = None,
        trusted_intent_result: IntentResult | None = None,
        trusted_conversation_context: ConversationPromptContext | None = None,
    ) -> ChatResponse:
        """Execute one run, optionally reusing application-owned routing results."""
        started = perf_counter()
        run_id = str(uuid4())
        _emit_progress(
            event_sink,
            WorkflowProgressEvent(
                type="run_started",
                run_id=run_id,
                elapsed_ms=0.0,
            ),
        )
        stage_started = perf_counter()
        errors: list[str] = []
        request_id = _optional_string(request.context.get("request_id")) or get_request_id()
        user_profile = await self.user_profile_repository.get_summary(request.user_id)
        memory_context = build_memory_context(
            practice_summary=user_profile,
            memory_settings=await self.memory_settings_repository.get(
                request.user_id
            ),
        )
        run_context = RunContext(
            run_id=run_id,
            user_id=request.user_id,
            session_id=_optional_string(request.context.get("session_id")),
            message=request.message,
            request_context=request.context,
            conversation_context=trusted_conversation_context,
            response_constraints=extract_response_constraints(request.message),
        )

        for hook in self.hooks:
            method = getattr(hook, "before_safety", None)
            if method is not None:
                method(request)

        if trusted_safety_result is not None:
            safety_result = trusted_safety_result
        else:
            try:
                safety_result = await self.safety_classifier.classify(request.message)
            except Exception as exc:
                safety_result = SafetyResult(
                    risk_level=RiskLevel.HIGH,
                    reason=(
                        "Safety classifier failed; using conservative high-risk "
                        "fallback instead of ordinary low-risk handling."
                    ),
                )
                errors.append(
                    format_trace_error(
                        ErrorCategory.SAFETY_CLASSIFIER_FAILURE,
                        exc,
                    )
                )
        run_context.safety_result = safety_result
        for hook in self.hooks:
            method = getattr(hook, "after_safety", None)
            if method is not None:
                method(request, safety_result)
        _emit_stage_completed(
            event_sink,
            run_id=run_id,
            stage=WorkflowStage.SAFETY,
            stage_started=stage_started,
            run_started=started,
        )
        stage_started = perf_counter()

        crisis_decision = self.permission_gate.decide(
            safety_result,
            HarnessAction.CRISIS_ESCALATION,
        )
        if crisis_decision.action == PermissionAction.ESCALATE:
            intent_result = IntentResult(
                intent=Intent.CRISIS,
                confidence=1.0,
                reason="Safety permission gate required crisis escalation.",
            )
        elif trusted_intent_result is not None:
            intent_result = trusted_intent_result
        else:
            intent_result = await self.intent_router.route(request.message, safety_result)
        run_context.intent_result = intent_result

        for hook in self.hooks:
            method = getattr(hook, "after_routing", None)
            if method is not None:
                method(request, intent_result)
        _emit_stage_completed(
            event_sink,
            run_id=run_id,
            stage=WorkflowStage.ROUTING,
            stage_started=stage_started,
            run_started=started,
        )
        stage_started = perf_counter()

        harness_action = _action_for_intent(intent_result.intent)
        permission_decision = self.permission_gate.decide(safety_result, harness_action)
        approved_protocol_id = _optional_string(request.context.get("protocol_id"))
        protocol_request_hash = _protocol_request_hash(
            harness_action=harness_action,
            message=request.message,
            request_context=request.context,
        )
        protocol_to_consume = None
        if (
            permission_decision.action == PermissionAction.ASK_CONSENT
            and await protocol_service.claim_for_action(
                protocol_id=approved_protocol_id,
                user_id=request.user_id,
                harness_action=harness_action,
                request_hash=protocol_request_hash,
                session_id=run_context.session_id,
            ) is not None
        ):
            permission_decision = PermissionDecision(
                action=PermissionAction.ALLOW,
                reason="Approved consent protocol allowed the requested action.",
                required_protocol=None,
                allowed=True,
                requires_consent=False,
                intensity_adjustment=permission_decision.intensity_adjustment,
            )
            protocol_to_consume = approved_protocol_id
        if permission_decision.intensity_adjustment is not None:
            _apply_intensity_adjustment(
                run_context=run_context,
                harness_action=harness_action,
                permission_decision=permission_decision,
            )

        skill = self.skill_registry.resolve_for_chat(intent_result.intent, safety_result)
        run_context.skill_context = select_skill_context(
            skill_name=skill.descriptor.name,
            request_context=run_context.request_context,
            memory_context=memory_context,
        )
        run_context.active_memory = self.active_memory_assembler.assemble(
            user_id=request.user_id,
            skill_context=run_context.skill_context,
            current_request=request.message,
        )
        run_context.skill_context = run_context.active_memory.stable_memory
        hook_decision = None
        if permission_decision.action != PermissionAction.ESCALATE:
            hook_decision = _run_before_action_hooks(
                hooks=self.hooks,
                run_context=run_context,
                harness_action=harness_action,
                permission_decision=permission_decision,
            )
        if hook_decision is not None and not hook_decision.allow:
            errors.append(f"before_action_blocked:{hook_decision.reason}")
            skill_result = _hook_limited_result(
                hook_decision=hook_decision,
                harness_action=harness_action,
            )
        elif permission_decision.action in {
            PermissionAction.ASK_CONSENT,
            PermissionAction.BLOCK,
        }:
            skill_result = await _permission_limited_result(
                decision=permission_decision,
                harness_action=harness_action,
                user_id=request.user_id,
                session_id=run_context.session_id,
                request_hash=protocol_request_hash,
            )
        else:
            try:
                skill_result = await skill.run(SkillContext(run=run_context))
                _annotate_intensity_adjustment(
                    run_context=run_context,
                    skill_result_data=skill_result.structured_data,
                )
                if protocol_to_consume is not None:
                    skill_result.structured_data["protocol_status"] = (
                        ProtocolStatus.CONSUMED.value
                    )
            except Exception as exc:
                category = categorize_error(exc)
                if category == ErrorCategory.UNKNOWN_FAILURE:
                    category = ErrorCategory.TOOL_OR_SKILL_FAILURE
                errors.append(format_trace_error(category, exc))
                skill_result = skill_failure_result(
                    harness_action=harness_action,
                    category=category,
                )

        _emit_stage_completed(
            event_sink,
            run_id=run_id,
            stage=WorkflowStage.SKILL,
            stage_started=stage_started,
            run_started=started,
        )
        stage_started = perf_counter()

        output_guardrail_result = await self.output_guardrail.evaluate(
            user_message=request.message,
            response=skill_result.response,
            intent=intent_result.intent,
            risk_level=safety_result.risk_level,
            selected_skill=skill.descriptor.name,
            selected_agent=skill_result.selected_agent,
            grounding_metadata=_grounding_metadata(skill_result.structured_data),
            historical_user_messages=(
                [
                    event.content
                    for event in run_context.conversation_context.recent_events
                    if event.role == ConversationEventRole.USER
                ]
                if run_context.conversation_context is not None
                else None
            ),
        )
        skill_result = _apply_output_guardrail_result(
            skill_result=skill_result,
            result=output_guardrail_result,
        )
        if output_guardrail_result.semantic_check_failed:
            semantic_error = (
                output_guardrail_result.semantic_error_type.value
                if output_guardrail_result.semantic_error_type is not None
                else "unknown"
            )
            if output_guardrail_result.semantic_schema_error_code is not None:
                semantic_error = (
                    f"{semantic_error}:"
                    f"{output_guardrail_result.semantic_schema_error_code.value}"
                )
            if output_guardrail_result.semantic_schema_error_field is not None:
                semantic_error = (
                    f"{semantic_error}:"
                    f"{output_guardrail_result.semantic_schema_error_field}"
                )
            errors.append(f"OUTPUT_GUARDRAIL_SEMANTIC_FAILURE:{semantic_error}")
        _emit_stage_completed(
            event_sink,
            run_id=run_id,
            stage=WorkflowStage.OUTPUT_GUARDRAIL,
            stage_started=stage_started,
            run_started=started,
        )
        stage_started = perf_counter()

        _run_after_action_hooks(
            hooks=self.hooks,
            run_context=run_context,
            harness_action=harness_action,
            skill_result=skill_result,
        )
        if safety_result.risk_level != RiskLevel.CRISIS:
            _annotate_context_selection(
                run_context=run_context,
                skill_result_data=skill_result.structured_data,
            )
        for hook in self.hooks:
            method = getattr(hook, "after_skill", None)
            if method is not None:
                method(request, skill_result)

        selected_agent = skill_result.selected_agent
        if safety_result.risk_level == RiskLevel.CRISIS:
            selected_agent = "crisis_escalation"
        selected_skill = (
            "lead_harness"
            if (
                permission_decision.action in {PermissionAction.ASK_CONSENT, PermissionAction.BLOCK}
                or (hook_decision is not None and not hook_decision.allow)
            )
            else skill.descriptor.name
        )
        action = _optional_string(skill_result.structured_data.get("action"))
        session_id = (
            _optional_string(skill_result.structured_data.get("session_id"))
            or (
                _optional_string(skill_result.structured_data.get("plan_id"))
                if harness_action == HarnessAction.CREATE_EXPOSURE_PLAN
                else None
            )
            or run_context.session_id
        )
        intervention_plan = None
        linked_intervention_plan_id = (
            await protocol_service.linked_intervention_plan_id(
                protocol_id=protocol_to_consume,
                user_id=request.user_id,
            )
        )
        memory_payload: dict[str, object] = {
            "memory_kind": "intervention_plan",
            "harness_action": harness_action.value,
            "selected_skill": selected_skill,
            "session_id": session_id or run_id,
        }
        should_consider_intervention_plan = (
            safety_result.risk_level != RiskLevel.CRISIS
            and permission_decision.action != PermissionAction.BLOCK
            and harness_action
            not in {
                HarnessAction.REQUEST_CLARIFICATION,
                HarnessAction.DECLINE_OUT_OF_SCOPE,
                HarnessAction.PROPOSE_CALENDAR_EVENT,
            }
        )
        memory_decision = (
            _run_before_memory_write_hooks(
                hooks=self.hooks,
                run_context=run_context,
                memory_kind="intervention_plan",
                payload=memory_payload,
            )
            if should_consider_intervention_plan
            else None
        )
        if memory_decision is not None and not memory_decision.allow:
            errors.append(f"before_memory_write_blocked:{memory_decision.reason}")
            if memory_decision.structured_data:
                skill_result.structured_data.update(memory_decision.structured_data)
        elif should_consider_intervention_plan:
            try:
                if linked_intervention_plan_id is not None:
                    intervention_plan = await intervention_plan_service.mark_action_completed(
                        user_id=request.user_id,
                        plan_id=linked_intervention_plan_id,
                        result_session_id=session_id or run_id,
                        result_summary=f"Executed {action or harness_action.value}.",
                    )
                if intervention_plan is None:
                    protocol_id = _optional_string(skill_result.structured_data.get("protocol_id"))
                    intervention_plan = await _create_intervention_plan(
                        user_id=request.user_id,
                        session_id=session_id or run_id,
                        harness_action=harness_action,
                        selected_skill=selected_skill,
                        permission_decision=permission_decision,
                        skill_result_data=skill_result.structured_data,
                        safety_result=safety_result,
                        protocol_id=protocol_id,
                    )
                    if intervention_plan is not None and protocol_id is not None:
                        await protocol_service.link_intervention_plan(
                            protocol_id=protocol_id,
                            user_id=request.user_id,
                            intervention_plan_id=intervention_plan.plan_id,
                        )
            except Exception as exc:
                errors.append(format_trace_error(ErrorCategory.MEMORY_WRITE_FAILURE, exc))
                skill_result.structured_data["memory_write_failed"] = True
                skill_result.structured_data["memory_error_category"] = (
                    ErrorCategory.MEMORY_WRITE_FAILURE.value
                )
        if intervention_plan is not None:
            run_context.intervention_plan = intervention_plan
            skill_result.structured_data.setdefault(
                "intervention_plan_id",
                intervention_plan.plan_id,
            )
            skill_result.structured_data.setdefault(
                "intervention_plan",
                intervention_plan.model_dump(mode="json"),
            )

        memory_pipeline_result = None
        if intent_result.intent in {
            Intent.EMOTIONAL_SUPPORT,
            Intent.ROLEPLAY_PRACTICE,
            Intent.CBT_WORKSHEET,
            Intent.EXPOSURE_PLANNING,
            Intent.PROGRESS_REVIEW,
        }:
            try:
                memory_pipeline_result = await self.memory_write_pipeline.process_messages(
                    user_id=request.user_id,
                    messages=[{"role": "user", "content": request.message}],
                    source_type=MemorySourceType.CHAT,
                    source_id=(request_id or run_id)[:128],
                    occurred_at=datetime.now(timezone.utc),
                    risk_level=safety_result.risk_level,
                    scenario_spec=_scenario_spec_from_skill_result(
                        skill_result.structured_data
                    ),
                    practice_thread_id=(
                        session_id
                        if intent_result.intent == Intent.ROLEPLAY_PRACTICE
                        else None
                    ),
                )
                skill_result.structured_data["memory_pipeline"] = {
                    "status": memory_pipeline_result.status,
                    "item_count": len(memory_pipeline_result.items),
                    "committed_count": sum(
                        item.action.value in {"auto_commit", "revoke"}
                        for item in memory_pipeline_result.items
                    ),
                    "confirmation_count": sum(
                        item.action.value == "require_confirmation"
                        for item in memory_pipeline_result.items
                    ),
                    "rejected_count": sum(
                        item.action.value == "reject"
                        for item in memory_pipeline_result.items
                    ),
                    "error_category": memory_pipeline_result.error_category,
                }
                if memory_pipeline_result.status == "extraction_failed":
                    errors.append(
                        format_trace_error(
                            ErrorCategory.MEMORY_EXTRACTION_FAILURE,
                            memory_pipeline_result.error_category or "reported_error",
                        )
                    )
                elif memory_pipeline_result.status in {
                    "write_failed",
                    "partial_failure",
                }:
                    errors.append(
                        format_trace_error(
                            ErrorCategory.MEMORY_WRITE_FAILURE,
                            memory_pipeline_result.error_category or "reported_error",
                        )
                    )
            except Exception as exc:
                errors.append(
                    format_trace_error(
                        ErrorCategory.MEMORY_EXTRACTION_FAILURE,
                        exc,
                    )
                )
                skill_result.structured_data["memory_pipeline"] = {
                    "status": "write_failed",
                    "item_count": 0,
                    "error_category": "MEMORY_PIPELINE_ERROR",
                }

        latency_ms = (perf_counter() - started) * 1000
        input_decision = await persistence_gate.persist_text(
            user_id=request.user_id,
            kind=PersistenceKind.TRACE_INPUT,
            text=request.message,
        )
        output_decision = await persistence_gate.persist_text(
            user_id=request.user_id,
            kind=PersistenceKind.TRACE_OUTPUT,
            text=skill_result.response,
        )
        privacy_summary = TracePrivacySummary(
            raw_input_retained=not input_decision.changed,
            raw_output_retained=not output_decision.changed,
            fields=[
                _trace_field_policy("input", input_decision),
                _trace_field_policy("output", output_decision),
            ]
        )
        trace = TraceRecord(
            run_id=run_id,
            request_id=request_id,
            user_id=request.user_id,
            session_id=session_id,
            intervention_plan_id=intervention_plan.plan_id if intervention_plan else None,
            execution_version=build_execution_version_info(
                selected_skill=selected_skill,
                safety_llm_used=safety_result.llm_usage.used,
                intent_llm_used=intent_result.llm_usage.used,
                skill_llm_used=_skill_llm_used(skill_result.structured_data),
                output_semantic_checked=(
                    output_guardrail_result.semantic_checked
                    or output_guardrail_result.semantic_check_failed
                ),
                output_repair_attempted=output_guardrail_result.repair_attempted,
                memory_extraction_used=(
                    memory_pipeline_result is not None
                    and memory_pipeline_result.status != "skipped"
                ),
            ),
            input=input_decision.persisted_text,
            safety_result=safety_result,
            intent_result=intent_result,
            selected_skill=selected_skill,
            selected_agent=selected_agent,
            action=action,
            permission_action=permission_decision.action.value,
            permission_reason=permission_decision.reason,
            context_selected_fields=(
                run_context.skill_context.selected_fields
                if run_context.skill_context is not None
                else []
            ),
            context_field_sources=(
                {
                    field: [source.value for source in metadata.sources]
                    for field, metadata in run_context.skill_context.field_metadata.items()
                }
                if run_context.skill_context is not None
                else {}
            ),
            context_dropped_fields=(
                run_context.skill_context.dropped_fields
                if run_context.skill_context is not None
                else []
            ),
            active_memory_estimated_tokens=(
                run_context.active_memory.estimated_tokens
                if run_context.active_memory is not None
                else 0
            ),
            active_memory_token_budget=(
                run_context.active_memory.token_budget
                if run_context.active_memory is not None
                else 0
            ),
            active_memory_selections=(
                run_context.active_memory.trace_metadata()["selections"]
                if run_context.active_memory is not None
                else []
            ),
            agent_loop_used=skill_result.structured_data.get("agent_loop_used") is True,
            agent_loop_stop_reason=_optional_string(
                skill_result.structured_data.get("agent_loop_stop_reason")
            ),
            agent_loop_steps=_agent_loop_steps(skill_result.structured_data),
            output_guardrail_action=output_guardrail_result.action.value,
            output_guardrail_categories=[
                category.value for category in output_guardrail_result.categories
            ],
            output_guardrail_semantic_checked=output_guardrail_result.semantic_checked,
            output_guardrail_semantic_failed=(
                output_guardrail_result.semantic_check_failed
            ),
            output_guardrail_semantic_error_type=(
                output_guardrail_result.semantic_error_type.value
                if output_guardrail_result.semantic_error_type is not None
                else None
            ),
            output_guardrail_semantic_schema_error_code=(
                output_guardrail_result.semantic_schema_error_code.value
                if output_guardrail_result.semantic_schema_error_code is not None
                else None
            ),
            output_guardrail_semantic_schema_error_field=(
                output_guardrail_result.semantic_schema_error_field
            ),
            output_guardrail_semantic_retry_attempted=(
                output_guardrail_result.semantic_retry_attempted
            ),
            output_guardrail_violation_tier=(
                output_guardrail_result.violation_tier.value
                if output_guardrail_result.violation_tier is not None
                else None
            ),
            output_guardrail_repair_attempted=(
                output_guardrail_result.repair_attempted
            ),
            output_guardrail_repair_succeeded=(
                output_guardrail_result.repair_succeeded
            ),
            output_guardrail_recheck_action=(
                output_guardrail_result.repair_recheck_action.value
                if output_guardrail_result.repair_recheck_action is not None
                else None
            ),
            output=output_decision.persisted_text,
            product_safe=True,
            privacy_summary=privacy_summary,
            latency_ms=latency_ms,
            errors=errors,
            error_categories=_error_categories(errors),
            created_at=datetime.now(timezone.utc),
        )
        trace = self.trace_logger.prepare(trace)
        trace_persisted = True
        try:
            await self.trace_logger.save(trace)
        except Exception as exc:
            trace_persisted = False
            trace = _append_trace_error(
                trace,
                format_observability_error(ErrorCategory.TRACE_PERSISTENCE_FAILURE, exc),
            )
            await _record_observability_event_safely("trace_persistence")
            logger.warning("Trace persistence failed: %s", exc.__class__.__name__)
        observability_hook_failed = False
        for hook in self.hooks:
            method = getattr(hook, "after_trace", None)
            if method is not None:
                try:
                    result = method(trace)
                    if inspect.isawaitable(result):
                        await result
                except Exception as exc:
                    observability_hook_failed = True
                    trace = _append_trace_error(
                        trace,
                        format_observability_error(
                            ErrorCategory.OBSERVABILITY_HOOK_FAILURE,
                            exc,
                        ),
                    )
                    await _record_observability_event_safely("observability_hook")
                    logger.warning(
                        "Post-trace hook failed: %s",
                        exc.__class__.__name__,
                    )
        stop_hook_errors = await _run_on_stop_hooks(self.hooks, run_context, trace)
        for marker in stop_hook_errors:
            trace = _append_trace_error(trace, marker)
        observability_hook_failed = observability_hook_failed or bool(stop_hook_errors)

        _emit_stage_completed(
            event_sink,
            run_id=run_id,
            stage=WorkflowStage.TRACE,
            stage_started=stage_started,
            run_started=started,
        )

        return ChatResponse(
            run_id=run_id,
            risk_level=safety_result.risk_level,
            intent=intent_result.intent,
            response=skill_result.response,
            structured_data={
                **skill_result.structured_data,
                "request_id": request_id,
                "error_categories": trace.error_categories,
                "trace_persisted": trace_persisted,
                "observability_hook_failed": observability_hook_failed,
            },
            trace=trace,
        )

    @staticmethod
    def _default_intent_router() -> BaseIntentRouter:
        llm_client = create_llm_client()
        if llm_client is not None:
            return LlmIntentRouter(llm_client=llm_client)
        return IntentRouter()


def _emit_stage_completed(
    event_sink: WorkflowEventSink | None,
    *,
    run_id: str,
    stage: WorkflowStage,
    stage_started: float,
    run_started: float,
) -> None:
    """Emit timing for one completed stage without exposing workflow content."""
    now = perf_counter()
    _emit_progress(
        event_sink,
        WorkflowProgressEvent(
            type="stage_completed",
            run_id=run_id,
            stage=stage,
            stage_latency_ms=(now - stage_started) * 1000,
            elapsed_ms=(now - run_started) * 1000,
        ),
    )


def _emit_progress(
    event_sink: WorkflowEventSink | None,
    event: WorkflowProgressEvent,
) -> None:
    """Keep optional UI progress reporting from affecting the Agent run."""
    if event_sink is None:
        return
    try:
        event_sink(event)
    except Exception as exc:
        logger.warning("Workflow progress sink failed: %s", exc.__class__.__name__)


def _optional_string(value: object) -> str | None:
    """Return a non-empty string value from loose structured data."""
    if isinstance(value, str) and value:
        return value
    return None


def _scenario_spec_from_skill_result(
    structured_data: dict[str, object],
) -> ScenarioSpec | None:
    """Validate application-produced open-scenario metadata for memory writes."""
    payload = structured_data.get("scenario")
    if not isinstance(payload, dict):
        return None
    try:
        return ScenarioSpec.model_validate(payload)
    except ValueError:
        return None


def _skill_llm_used(structured_data: dict[str, object]) -> bool:
    """Return whether one normalized Skill result reports a real LLM call."""
    for key in ("llm_usage", "agent_loop_llm_usage"):
        value = structured_data.get(key)
        if isinstance(value, dict) and value.get("used") is True:
            return True
    return False


def _grounding_metadata(
    structured_data: dict[str, object],
) -> GroundingMetadata | None:
    """Extract only bounded retrieval facts from one SkillResult."""
    nested = structured_data.get("grounding_metadata")
    if isinstance(nested, dict):
        try:
            return GroundingMetadata.model_validate(nested)
        except ValueError:
            return None

    citations = structured_data.get("citations")
    if not isinstance(citations, list):
        return None
    citation_titles = [
        str(item["title"])[:160]
        for item in citations[:10]
        if isinstance(item, dict) and isinstance(item.get("title"), str)
    ]
    unknown = structured_data.get("retrieval_unknown", structured_data.get("unknown"))
    return GroundingMetadata(
        retrieval_unknown=unknown if isinstance(unknown, bool) else None,
        citation_count=len(citations),
        citation_titles=citation_titles,
        resource_contact_verified=(
            structured_data.get("resource_contact_verified") is True
        ),
    )


def _apply_output_guardrail_result(
    *,
    skill_result: SkillResult,
    result: OutputGuardrailResult,
) -> SkillResult:
    """Return a normalized SkillResult annotated with the global decision."""
    structured_data = {
        **skill_result.structured_data,
        "output_guardrail": {
            "action": result.action.value,
            "categories": [category.value for category in result.categories],
            "sources": result.sources,
            "semantic_checked": result.semantic_checked,
            "semantic_check_failed": result.semantic_check_failed,
            "semantic_error_type": (
                result.semantic_error_type.value
                if result.semantic_error_type is not None
                else None
            ),
            "semantic_schema_error_code": (
                result.semantic_schema_error_code.value
                if result.semantic_schema_error_code is not None
                else None
            ),
            "semantic_schema_error_field": result.semantic_schema_error_field,
            "semantic_retry_attempted": result.semantic_retry_attempted,
            "violation_tier": (
                result.violation_tier.value
                if result.violation_tier is not None
                else None
            ),
            "repair_attempted": result.repair_attempted,
            "repair_succeeded": result.repair_succeeded,
            "repair_recheck_action": (
                result.repair_recheck_action.value
                if result.repair_recheck_action is not None
                else None
            ),
        },
    }
    if result.action in {
        OutputGuardrailAction.REPAIR,
        OutputGuardrailAction.REPLACE,
    }:
        structured_data.update(
            {
                "output_guardrail_replaced": (
                    result.action == OutputGuardrailAction.REPLACE
                ),
                "original_selected_agent": skill_result.selected_agent,
            }
        )
        selected_agent = (
            "output_guardrail_repair"
            if result.action == OutputGuardrailAction.REPAIR
            else "output_guardrail"
        )
    else:
        selected_agent = skill_result.selected_agent
    return SkillResult(
        response=result.response,
        structured_data=structured_data,
        selected_agent=selected_agent,
    )


def _agent_loop_steps(structured_data: dict[str, object]) -> list[dict[str, object]]:
    """Return at most three already-redacted loop step summaries for tracing."""
    value = structured_data.get("agent_loop_steps")
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value[:3] if isinstance(item, dict)]


def _error_categories(errors: list[str]) -> list[str]:
    """Return stable error category labels parsed from trace error strings."""
    categories: list[str] = []
    for error in errors:
        category = error.split(":", 1)[0]
        if category and category not in categories:
            categories.append(category)
    return categories


def _trace_field_policy(field: str, decision: object) -> TraceFieldPolicy:
    """Convert a persistence decision into trace-safe policy metadata."""
    from app.privacy.policy import PersistenceDecision

    if not isinstance(decision, PersistenceDecision):
        return TraceFieldPolicy(field=field, persistence_kind="unknown")
    return TraceFieldPolicy(
        field=field,
        persistence_kind=decision.kind.value,
        minimized=decision.minimized,
        summarized=decision.summarized,
        policy=decision.policy,
        redacted_types=decision.redacted_types,
        original_length=decision.original_length,
        persisted_length=len(decision.persisted_text),
    )


def _action_for_intent(intent: Intent) -> HarnessAction:
    """Map routed intent to a bounded harness action."""
    return {
        Intent.EMOTIONAL_SUPPORT: HarnessAction.GENERAL_SUPPORT,
        Intent.ROLEPLAY_PRACTICE: HarnessAction.START_ROLEPLAY,
        Intent.CBT_WORKSHEET: HarnessAction.CREATE_WORKSHEET,
        Intent.EXPOSURE_PLANNING: HarnessAction.CREATE_EXPOSURE_PLAN,
        Intent.PROGRESS_REVIEW: HarnessAction.CREATE_EXPOSURE_PLAN,
        Intent.CAMPUS_RESOURCE_QUERY: HarnessAction.QUERY_SUPPORT_RESOURCE,
        Intent.CALENDAR_PLANNING: HarnessAction.PROPOSE_CALENDAR_EVENT,
        Intent.CLARIFICATION_NEEDED: HarnessAction.REQUEST_CLARIFICATION,
        Intent.OUT_OF_SCOPE: HarnessAction.DECLINE_OUT_OF_SCOPE,
        Intent.CRISIS: HarnessAction.CRISIS_ESCALATION,
    }.get(intent, HarnessAction.GENERAL_SUPPORT)


def _apply_intensity_adjustment(
    *,
    run_context: RunContext,
    harness_action: HarnessAction,
    permission_decision: PermissionDecision,
) -> None:
    """Apply conservative intensity adjustment before skill execution."""
    if harness_action != HarnessAction.CREATE_EXPOSURE_PLAN:
        return
    adjustment = permission_decision.intensity_adjustment
    if adjustment is None:
        return
    if run_context.request_context.get("permission_intensity_adjusted") is True:
        return
    current = run_context.request_context.get("current_anxiety_level", 5)
    if not isinstance(current, int):
        current = 5
    run_context.request_context["current_anxiety_level"] = max(1, min(10, current + adjustment))
    run_context.request_context["permission_down_shifted"] = True
    run_context.request_context["permission_intensity_adjusted"] = True
    run_context.request_context["permission_intensity_adjustment"] = adjustment


def _annotate_intensity_adjustment(
    *,
    run_context: RunContext,
    skill_result_data: dict[str, object],
) -> None:
    """Expose applied permission intensity modifiers in skill output."""
    if run_context.request_context.get("permission_intensity_adjusted") is not True:
        return
    skill_result_data["permission_intensity_adjusted"] = True
    skill_result_data["permission_intensity_adjustment"] = run_context.request_context.get(
        "permission_intensity_adjustment"
    )


def _annotate_context_selection(
    *,
    run_context: RunContext,
    skill_result_data: dict[str, object],
) -> None:
    """Expose trace-safe field names and provenance without duplicating values."""
    projection = run_context.skill_context
    if projection is None:
        return
    skill_result_data.setdefault(
        "context_selection",
        {
            "skill_name": projection.skill_name,
            "selected_fields": projection.selected_fields,
            "field_metadata": {
                field: metadata.model_dump(mode="json")
                for field, metadata in projection.field_metadata.items()
            },
            "dropped_fields": projection.dropped_fields,
            "drop_reasons": projection.drop_reasons,
        },
    )
    if run_context.active_memory is not None:
        skill_result_data.setdefault(
            "active_memory",
            run_context.active_memory.trace_metadata(),
        )


async def _permission_limited_result(
    *,
    decision: PermissionDecision,
    harness_action: HarnessAction,
    user_id: str,
    session_id: str | None,
    request_hash: str,
) -> SkillResult:
    """Return a bounded response when an action cannot run immediately."""
    if decision.action == PermissionAction.ASK_CONSENT:
        protocol = await protocol_service.create_consent_request(
            user_id=user_id,
            harness_action=harness_action,
            reason=decision.reason,
            required_protocol=decision.required_protocol,
            session_id=session_id,
            request_hash=request_hash,
        )
        response = (
            "在开始这个练习前，我想先确认你是否愿意继续。"
            "你可以选择不同意，也可以随时停止；这只是社交练习，不是诊断或治疗。"
        )
        action = "consent_required"
        protocol_data = {
            "protocol_id": protocol.protocol_id,
            "protocol_type": protocol.protocol_type.value,
            "protocol_status": protocol.status.value,
            "protocol_expires_at": protocol.expires_at.isoformat() if protocol.expires_at else None,
            "protocol_request_hash": protocol.request_hash,
        }
    else:
        response = (
            "我先不启动这个练习。当前表达提示压力较高，更适合先做支持性整理或联系现实支持，"
            "而不是进入角色扮演或分级练习。"
        )
        action = "action_blocked"
        protocol_data = {}

    return SkillResult(
        response=response,
        structured_data={
            "agent": "lead_harness",
            "action": action,
            "blocked": decision.action == PermissionAction.BLOCK,
            "consent_required": decision.action == PermissionAction.ASK_CONSENT,
            "harness_action": harness_action.value,
            "permission_action": decision.action.value,
            "permission_reason": decision.reason,
            "required_protocol": decision.required_protocol,
            "allowed": decision.allowed,
            "requires_consent": decision.requires_consent,
            "intensity_adjustment": decision.intensity_adjustment,
            "escalation_required": decision.escalation_required,
            "block_reason": decision.block_reason,
            **protocol_data,
        },
        selected_agent="lead_harness",
    )


def _protocol_request_hash(
    *,
    harness_action: HarnessAction,
    message: str,
    request_context: dict[str, object],
) -> str:
    """Return a stable hash binding consent to one normalized action request."""
    context_without_control_fields = {
        key: value
        for key, value in request_context.items()
        if key not in {"protocol_id"}
    }
    payload = {
        "harness_action": harness_action.value,
        "message": message,
        "context": context_without_control_fields,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _hook_limited_result(
    *,
    hook_decision: HookDecision,
    harness_action: HarnessAction,
) -> SkillResult:
    """Return a bounded response when a hook blocks an action."""
    structured_data = {
        "agent": "lead_harness",
        "action": "action_blocked",
        "blocked": True,
        "harness_action": harness_action.value,
        "hook_blocked": True,
        "hook_reason": hook_decision.reason,
    }
    if hook_decision.structured_data:
        structured_data.update(hook_decision.structured_data)
    return SkillResult(
        response="这个操作被当前 harness hook 暂停了。你可以先选择更低强度的支持性整理。",
        structured_data=structured_data,
        selected_agent="lead_harness",
    )


async def _create_intervention_plan(
    *,
    user_id: str,
    session_id: str,
    harness_action: HarnessAction,
    selected_skill: str,
    permission_decision: PermissionDecision,
    skill_result_data: dict[str, object],
    safety_result: SafetyResult,
    protocol_id: str | None,
) -> InterventionPlan | None:
    """Create a session-level plan for non-crisis harness actions."""
    if safety_result.risk_level == RiskLevel.CRISIS:
        return None
    if permission_decision.action == PermissionAction.BLOCK:
        return None
    intensity = _plan_intensity(harness_action, skill_result_data)
    return await intervention_plan_service.create_for_action(
        user_id=user_id,
        session_id=session_id,
        harness_action=harness_action,
        selected_skill=selected_skill,
        requires_consent=permission_decision.action == PermissionAction.ASK_CONSENT,
        intensity=intensity,
        protocol_id=protocol_id,
    )


def _plan_intensity(
    harness_action: HarnessAction,
    skill_result_data: dict[str, object],
) -> int | None:
    """Derive a simple plan intensity from skill output."""
    if harness_action == HarnessAction.START_ROLEPLAY:
        value = skill_result_data.get("difficulty")
        return value if isinstance(value, int) else None
    if harness_action == HarnessAction.CREATE_EXPOSURE_PLAN:
        preview_tasks = skill_result_data.get("preview_tasks")
        if isinstance(preview_tasks, list) and preview_tasks:
            first = preview_tasks[0]
            if isinstance(first, dict):
                difficulty = first.get("difficulty")
                return difficulty if isinstance(difficulty, int) else None
    return None


def _run_before_action_hooks(
    *,
    hooks: tuple[AgentHarnessHook, ...],
    run_context: RunContext,
    harness_action: HarnessAction,
    permission_decision: PermissionDecision,
) -> HookDecision | None:
    """Run before-action hooks and return the first blocking decision."""
    for hook in hooks:
        method = getattr(hook, "before_action", None)
        if method is None:
            continue
        decision = method(run_context, harness_action, permission_decision)
        if decision is not None and not decision.allow:
            return decision
    return None


def _run_after_action_hooks(
    *,
    hooks: tuple[AgentHarnessHook, ...],
    run_context: RunContext,
    harness_action: HarnessAction,
    skill_result: SkillResult,
) -> None:
    """Run after-action hooks."""
    for hook in hooks:
        method = getattr(hook, "after_action", None)
        if method is not None:
            method(run_context, harness_action, skill_result)


def _run_before_memory_write_hooks(
    *,
    hooks: tuple[AgentHarnessHook, ...],
    run_context: RunContext,
    memory_kind: str,
    payload: dict[str, object],
) -> HookDecision | None:
    """Run before-memory-write hooks and return the first blocking decision."""
    for hook in hooks:
        method = getattr(hook, "before_memory_write", None)
        if method is None:
            continue
        decision = method(run_context, memory_kind, payload)
        if decision is not None and not decision.allow:
            return decision
    return None


async def _run_on_stop_hooks(
    hooks: tuple[AgentHarnessHook, ...],
    run_context: RunContext,
    trace: TraceRecord,
) -> list[str]:
    """Run stop hooks after trace persistence and isolate observer failures."""
    errors: list[str] = []
    for hook in hooks:
        method = getattr(hook, "on_stop", None)
        if method is not None:
            try:
                result = method(run_context, trace)
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:
                errors.append(
                    format_observability_error(
                        ErrorCategory.OBSERVABILITY_HOOK_FAILURE,
                        exc,
                    )
                )
                await _record_observability_event_safely("observability_hook")
                logger.warning("Stop hook failed: %s", exc.__class__.__name__)
    return errors


def _append_trace_error(trace: TraceRecord, marker: str) -> TraceRecord:
    """Return a trace copy with one stable, content-free failure marker."""
    category = marker.partition(":")[0]
    return trace.model_copy(
        update={
            "errors": [*trace.errors, marker],
            "error_categories": list(dict.fromkeys([*trace.error_categories, category])),
        },
        deep=True,
    )


async def _record_observability_event_safely(event: str) -> None:
    """Best-effort operational metric that can never block a user response."""
    try:
        if event == "trace_persistence":
            from app.observability.runtime_events import record_trace_persistence_failure

            await record_trace_persistence_failure()
        else:
            from app.observability.runtime_events import record_observability_hook_failure

            await record_observability_hook_failure()
    except Exception:
        return


# Backwards-compatible name used by older imports and tests.
AgentWorkflow = AgentHarness
