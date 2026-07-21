"""Calendar proposal Skill that never performs an external write directly."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
import re
from zoneinfo import ZoneInfo

from app.models import Intent
from app.models_calendar import CalendarEventProposal, CalendarRecurrence
from app.skills.base import SkillContext, SkillDescriptor, SkillResult


class CalendarPlanningSkill:
    """Build a neutral, finite reminder proposal for explicit user review."""

    descriptor = SkillDescriptor(
        name="calendar_planning_skill",
        description="Builds a reviewable calendar proposal without executing a write.",
        supported_intents=(Intent.CALENDAR_PLANNING,),
        entrypoint="app.skills.calendar.CalendarPlanningSkill.run",
        safety_notes=(
            "Produces a proposal only; create, update and delete require separate consented APIs."
        ),
        manifest_path=str(Path(__file__).parent / "manifests" / "calendar" / "SKILL.md"),
    )

    def __init__(self, clock: Callable[[], datetime] | None = None) -> None:
        self.clock = clock or (lambda: datetime.now(ZoneInfo("Asia/Shanghai")))

    async def run(self, context: SkillContext) -> SkillResult:
        """Return a proposal or request the missing start time."""
        proposal = _proposal_from_message(context.message, now=self.clock())
        if proposal is None:
            return SkillResult(
                response=(
                    "我可以先帮你生成日历提醒预览。请告诉我具体时间，例如“每天晚上8点”，"
                    "确认预览后系统才会写入日历。"
                ),
                structured_data={
                    "agent": "calendar_planner",
                    "action": "calendar_proposal_needs_time",
                    "calendar_write_executed": False,
                    "consent_required_for_write": True,
                },
                selected_agent="calendar_planner",
            )
        return SkillResult(
            response=(
                f"我生成了一份日历预览：{proposal.title}，从"
                f"{proposal.start_time.isoformat()}开始，每次{proposal.duration_minutes}分钟。"
                "目前还没有写入日历；请确认时间和重复规则后再执行。"
            ),
            structured_data={
                "agent": "calendar_planner",
                "action": "calendar_proposal_created",
                "calendar_proposal": proposal.model_dump(mode="json"),
                "calendar_write_executed": False,
                "consent_required_for_write": True,
                "next_ui": "calendar_proposal",
            },
            selected_agent="calendar_planner",
        )


def _proposal_from_message(
    message: str,
    *,
    now: datetime,
) -> CalendarEventProposal | None:
    """Extract only a bounded time/recurrence shape with conservative defaults."""
    time_parts = _extract_time(message)
    if time_parts is None:
        return None
    hour, minute = time_parts
    timezone = now.tzinfo or ZoneInfo("Asia/Shanghai")
    day_offset = 2 if "后天" in message else 1 if "明天" in message else 0
    start_date = (now + timedelta(days=day_offset)).date()
    start = datetime(
        start_date.year,
        start_date.month,
        start_date.day,
        hour,
        minute,
        tzinfo=timezone,
    )
    if day_offset == 0 and start <= now:
        start += timedelta(days=1)
    recurrence = CalendarRecurrence.NONE
    end_date = None
    lowered = message.casefold()
    if "每天" in lowered or "daily" in lowered:
        recurrence = CalendarRecurrence.DAILY
        end_date = start.date() + timedelta(days=7)
    elif "每周" in lowered or "weekly" in lowered:
        recurrence = CalendarRecurrence.WEEKLY
        end_date = start.date() + timedelta(days=28)
    return CalendarEventProposal(
        title="15分钟练习",
        start_time=start,
        duration_minutes=15,
        recurrence=recurrence,
        recurrence_end_date=end_date,
        reminder_minutes=10,
    )


def _extract_time(message: str) -> tuple[int, int] | None:
    """Parse one explicit 24-hour or Chinese clock expression."""
    colon = re.search(r"(?<!\d)([01]?\d|2[0-3])[:：]([0-5]\d)(?!\d)", message)
    if colon is not None:
        return int(colon.group(1)), int(colon.group(2))
    chinese = re.search(r"(?<!\d)([01]?\d|2[0-3])点(?:(半)|([0-5]?\d)分?)?", message)
    if chinese is None:
        return None
    hour = int(chinese.group(1))
    if any(term in message for term in ("晚上", "下午")) and hour < 12:
        hour += 12
    minute = 30 if chinese.group(2) else int(chinese.group(3) or 0)
    return hour, minute

