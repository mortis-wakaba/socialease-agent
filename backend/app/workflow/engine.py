"""Workflow engine connecting safety, routing, agents, and tracing."""

from datetime import datetime, timezone
from time import perf_counter
from typing import Any
from uuid import uuid4

from app.agents.support import SupportAgent
from app.models import (
    ChatRequest,
    ChatResponse,
    Intent,
    IntentResult,
    RiskLevel,
    SafetyResult,
    TraceRecord,
)
from app.safety.classifier import BaseSafetyClassifier, SafetyClassifier
from app.tracing.logger import TraceLogger
from app.workflow.router import BaseIntentRouter, IntentRouter


class AgentWorkflow:
    """Small custom workflow engine, shaped for a later LangGraph migration."""

    def __init__(
        self,
        trace_logger: TraceLogger,
        safety_classifier: BaseSafetyClassifier | None = None,
        intent_router: BaseIntentRouter | None = None,
        support_agent: SupportAgent | None = None,
    ) -> None:
        self.trace_logger = trace_logger
        self.safety_classifier = safety_classifier or SafetyClassifier()
        self.intent_router = intent_router or IntentRouter()
        self.support_agent = support_agent or SupportAgent()

    async def run(self, request: ChatRequest) -> ChatResponse:
        """Execute one full agent run and store a trace."""
        started = perf_counter()
        run_id = str(uuid4())
        errors: list[str] = []

        safety_result = self.safety_classifier.classify(request.message)
        intent_result = self.intent_router.route(request.message, safety_result)
        selected_agent = "support_agent"

        if safety_result.risk_level == RiskLevel.CRISIS:
            selected_agent = "crisis_escalation"
            response, structured_data = self._crisis_escalation_response()
        else:
            response, structured_data = self.support_agent.respond(
                message=request.message,
                intent=intent_result.intent,
                safety_result=safety_result,
            )

        latency_ms = (perf_counter() - started) * 1000
        trace = TraceRecord(
            run_id=run_id,
            user_id=request.user_id,
            input=request.message,
            safety_result=safety_result,
            intent_result=intent_result,
            selected_agent=selected_agent,
            output=response,
            latency_ms=latency_ms,
            errors=errors,
            created_at=datetime.now(timezone.utc),
        )
        self.trace_logger.save(trace)

        return ChatResponse(
            run_id=run_id,
            risk_level=safety_result.risk_level,
            intent=intent_result.intent,
            response=response,
            structured_data=structured_data,
            trace=trace,
        )

    @staticmethod
    def _crisis_escalation_response() -> tuple[str, dict[str, Any]]:
        response = (
            "我很担心你现在的安全。这个系统不能处理危机，也不能替代专业帮助。\n\n"
            "如果你可能马上伤害自己或他人，请立刻联系当地紧急服务，或请身边可信任的人陪你一起求助。"
            "如果你在学校，也建议尽快联系学校心理中心、辅导员或宿舍管理人员。\n\n"
            "在获得现实帮助前，尽量不要独处，远离可能伤害自己或他人的物品，并把这条信息直接发给一个"
            "你信任的人：我现在不安全，需要你马上陪我联系帮助。"
        )
        structured_data = {
            "agent": "crisis_escalation",
            "escalation": True,
            "recommended_actions": [
                "contact_local_emergency_services",
                "contact_trusted_person_now",
                "contact_school_counseling_center_or_counselor",
                "avoid_being_alone_until_help_arrives",
            ],
        }
        return response, structured_data
