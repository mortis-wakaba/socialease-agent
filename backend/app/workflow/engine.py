"""Agent harness connecting safety, routing, skills, memory, and tracing."""

from collections.abc import Iterable
from datetime import datetime, timezone
from time import perf_counter
from uuid import uuid4

from app.llm.factory import create_llm_client
from app.models import (
    ChatRequest,
    ChatResponse,
    Intent,
    IntentResult,
    RiskLevel,
    TraceRecord,
)
from app.safety.classifier import BaseSafetyClassifier, create_safety_classifier
from app.safety.permissions import PermissionAction, SafetyPermissionGate
from app.skills import SkillContext, SkillRegistry
from app.tracing.logger import TraceLogger
from app.workflow.hooks import AgentHarnessHook
from app.workflow.router import BaseIntentRouter, IntentRouter, LlmIntentRouter


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
    ) -> None:
        self.trace_logger = trace_logger
        self.safety_classifier = safety_classifier or create_safety_classifier()
        self.intent_router = intent_router or self._default_intent_router()
        self.skill_registry = skill_registry or SkillRegistry()
        self.permission_gate = permission_gate or SafetyPermissionGate()
        self.hooks = tuple(hooks or ())

    async def run(self, request: ChatRequest) -> ChatResponse:
        """Execute one full harness run and store a trace."""
        started = perf_counter()
        run_id = str(uuid4())
        errors: list[str] = []

        for hook in self.hooks:
            hook.before_safety(request)

        safety_result = await self.safety_classifier.classify(request.message)
        for hook in self.hooks:
            hook.after_safety(request, safety_result)

        permission_action = self.permission_gate.decide(safety_result)
        if permission_action == PermissionAction.ESCALATE:
            intent_result = IntentResult(
                intent=Intent.CRISIS,
                confidence=1.0,
                reason="Safety permission gate required crisis escalation.",
            )
        else:
            intent_result = await self.intent_router.route(request.message, safety_result)

        for hook in self.hooks:
            hook.after_routing(request, intent_result)

        skill = self.skill_registry.resolve_for_chat(intent_result.intent, safety_result)
        skill_result = skill.run(
            SkillContext(
                user_id=request.user_id,
                message=request.message,
                intent=intent_result.intent,
                safety_result=safety_result,
                request_context=request.context,
            )
        )

        for hook in self.hooks:
            hook.after_skill(request, skill_result)

        selected_agent = skill_result.selected_agent
        if safety_result.risk_level == RiskLevel.CRISIS:
            selected_agent = "crisis_escalation"

        latency_ms = (perf_counter() - started) * 1000
        trace = TraceRecord(
            run_id=run_id,
            user_id=request.user_id,
            input=request.message,
            safety_result=safety_result,
            intent_result=intent_result,
            selected_agent=selected_agent,
            output=skill_result.response,
            latency_ms=latency_ms,
            errors=errors,
            created_at=datetime.now(timezone.utc),
        )
        self.trace_logger.save(trace)
        for hook in self.hooks:
            hook.after_trace(trace)

        return ChatResponse(
            run_id=run_id,
            risk_level=safety_result.risk_level,
            intent=intent_result.intent,
            response=skill_result.response,
            structured_data=skill_result.structured_data,
            trace=trace,
        )

    @staticmethod
    def _default_intent_router() -> BaseIntentRouter:
        llm_client = create_llm_client()
        if llm_client is not None:
            return LlmIntentRouter(llm_client=llm_client)
        return IntentRouter()


# Backwards-compatible name used by older imports and tests.
AgentWorkflow = AgentHarness
