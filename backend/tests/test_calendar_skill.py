"""Tests for the non-writing Calendar proposal Skill and intent route."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.models import ChatRequest, Intent, IntentResult, RiskLevel, SafetyResult
from app.db.repositories import InMemoryTraceRepository
from app.skills.base import SkillContext
from app.skills.calendar import CalendarPlanningSkill
from app.tracing.logger import TraceLogger
from app.workflow.engine import AgentHarness
from app.workflow.context import RunContext
from app.workflow.router import RuleBasedIntentRouter


@pytest.mark.anyio
async def test_calendar_router_selects_calendar_planning() -> None:
    result = await RuleBasedIntentRouter().route(
        "请每天晚上8点在日历里提醒我练习",
        SafetyResult(risk_level=RiskLevel.LOW, reason="ordinary request"),
    )

    assert result.intent == Intent.CALENDAR_PLANNING


@pytest.mark.anyio
async def test_calendar_skill_returns_preview_without_writing() -> None:
    run = RunContext(
        run_id="calendar-proposal-run",
        user_id="calendar-user",
        session_id=None,
        message="请每天晚上8点在日历里提醒我练习",
        request_context={},
        safety_result=SafetyResult(risk_level=RiskLevel.LOW, reason="ordinary"),
        intent_result=IntentResult(
            intent=Intent.CALENDAR_PLANNING,
            confidence=0.9,
            reason="calendar request",
        ),
    )
    skill = CalendarPlanningSkill(
        clock=lambda: datetime(2026, 7, 19, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    )

    result = await skill.run(SkillContext(run=run))

    proposal = result.structured_data["calendar_proposal"]
    assert proposal["start_time"] == "2026-07-19T20:00:00+08:00"
    assert proposal["recurrence"] == "daily"
    assert result.structured_data["calendar_write_executed"] is False
    assert result.structured_data["consent_required_for_write"] is True


@pytest.mark.anyio
async def test_calendar_skill_asks_for_time_when_missing() -> None:
    run = RunContext(
        run_id="calendar-clarify-run",
        user_id="calendar-user",
        session_id=None,
        message="帮我在日历里加一个练习提醒",
        request_context={},
        safety_result=SafetyResult(risk_level=RiskLevel.LOW, reason="ordinary"),
        intent_result=IntentResult(
            intent=Intent.CALENDAR_PLANNING,
            confidence=0.9,
            reason="calendar request",
        ),
    )

    result = await CalendarPlanningSkill().run(SkillContext(run=run))

    assert result.structured_data["action"] == "calendar_proposal_needs_time"
    assert "calendar_proposal" not in result.structured_data


@pytest.mark.anyio
async def test_harness_routes_calendar_request_to_non_writing_preview() -> None:
    harness = AgentHarness(
        trace_logger=TraceLogger(repository=InMemoryTraceRepository()),
    )

    response = await harness.run(
        request=ChatRequest(
            user_id="calendar-harness-owner",
            message="请每天晚上8点在日历里提醒我练习",
            context={},
        )
    )

    assert response.intent == Intent.CALENDAR_PLANNING
    assert response.trace.selected_skill == "calendar_planning_skill"
    assert response.structured_data["calendar_write_executed"] is False
    assert "intervention_plan_id" not in response.structured_data
