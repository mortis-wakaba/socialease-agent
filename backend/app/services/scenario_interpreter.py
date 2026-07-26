"""Deterministically minimize and structure open social-practice scenarios."""

from dataclasses import dataclass
import re

from app.models_scenario import (
    ScenarioCounterpartRole,
    ScenarioInteractionMode,
    ScenarioSpec,
    SocialSkillCode,
)
from app.privacy.redaction import redact_sensitive_identifiers


@dataclass(frozen=True)
class _SkillRule:
    skill: SocialSkillCode
    terms: tuple[str, ...]


_SKILL_RULES = (
    _SkillRule(
        SocialSkillCode.BOUNDARY_SETTING,
        ("拒绝", "边界", "不能", "不方便", "不想答应", "额外任务", "借"),
    ),
    _SkillRule(
        SocialSkillCode.SPECIFIC_REQUEST,
        ("提出请求", "希望对方", "调整", "能不能", "可不可以"),
    ),
    _SkillRule(
        SocialSkillCode.EMPATHY,
        ("理解", "关系", "配合", "礼貌", "不伤害", "不尴尬", "体谅"),
    ),
    _SkillRule(
        SocialSkillCode.DISAGREEMENT,
        ("不同意见", "不同看法", "反对", "不赞同", "争论", "分歧"),
    ),
    _SkillRule(
        SocialSkillCode.CONFLICT_REPAIR,
        ("冲突", "矛盾", "道歉", "修复", "误会", "升级", "和好"),
    ),
    _SkillRule(
        SocialSkillCode.CONVERSATION_INITIATION,
        ("开场", "搭话", "破冰", "认识", "聊天", "寒暄", "开口"),
    ),
    _SkillRule(
        SocialSkillCode.QUESTION_ASKING,
        ("提问", "问问题", "请教", "询问", "老师"),
    ),
    _SkillRule(
        SocialSkillCode.INVITATION,
        ("邀请", "约", "一起吃饭", "一起参加"),
    ),
    _SkillRule(
        SocialSkillCode.SELF_INTRODUCTION,
        ("自我介绍", "介绍自己", "面试"),
    ),
    _SkillRule(
        SocialSkillCode.ASSERTIVE_EXPRESSION,
        ("表达", "发言", "观点", "说明", "说清楚", "汇报"),
    ),
    _SkillRule(
        SocialSkillCode.COLLABORATIVE_PROBLEM_SOLVING,
        ("协商", "合作", "小组", "共同", "方案", "分工"),
    ),
    _SkillRule(
        SocialSkillCode.CONVERSATION_EXIT,
        ("结束对话", "告别", "离开", "结束聊天"),
    ),
)


class ScenarioInterpreter:
    """Map arbitrary safe text to bounded application-owned scenario facets."""

    def interpret(
        self,
        *,
        description: str,
        practice_goal: str | None = None,
    ) -> ScenarioSpec:
        """Return a minimized scenario without inferring clinical attributes."""
        normalized = _normalize_text(description)
        safe_summary, _ = redact_sensitive_identifiers(normalized)
        safe_summary = safe_summary[:240].strip()
        if not safe_summary:
            safe_summary = "练习一次具体的社交表达"

        raw_goal = _normalize_text(practice_goal or "")
        safe_goal, _ = redact_sensitive_identifiers(raw_goal)
        safe_goal = safe_goal[:200].strip()
        if not safe_goal:
            safe_goal = _default_goal(safe_summary)

        combined = f"{safe_summary} {safe_goal}".casefold()
        skills = [
            rule.skill
            for rule in _SKILL_RULES
            if any(term.casefold() in combined for term in rule.terms)
        ]
        skills = list(dict.fromkeys(skills))[:5]
        if not skills:
            skills = [SocialSkillCode.ASSERTIVE_EXPRESSION]

        return ScenarioSpec(
            safe_summary=safe_summary,
            practice_goal=safe_goal,
            counterpart_role=_counterpart_role(combined),
            interaction_mode=_interaction_mode(skills),
            skill_codes=skills,
            context_tags=_context_tags(combined),
        )


def _normalize_text(value: str) -> str:
    return " ".join(value.split())


def _default_goal(summary: str) -> str:
    return f"在该情境中完成一次清楚、尊重且具体的表达：{summary}"[:200]


def _counterpart_role(text: str) -> ScenarioCounterpartRole:
    if any(term in text for term in ("老师", "导师", "领导", "负责人")):
        return ScenarioCounterpartRole.AUTHORITY
    if any(term in text for term in ("面试", "面试官")):
        return ScenarioCounterpartRole.INTERVIEWER
    if any(term in text for term in ("小组", "大家", "多人", "群里")):
        return ScenarioCounterpartRole.GROUP
    if any(term in text for term in ("陌生", "不熟悉", "新同学")):
        return ScenarioCounterpartRole.ACQUAINTANCE
    if any(term in text for term in ("同学", "室友", "朋友", "同事", "社团")):
        return ScenarioCounterpartRole.PEER
    return ScenarioCounterpartRole.UNSPECIFIED


def _interaction_mode(skills: list[SocialSkillCode]) -> ScenarioInteractionMode:
    priority = (
        (SocialSkillCode.BOUNDARY_SETTING, ScenarioInteractionMode.RESPOND_TO_REQUEST),
        (SocialSkillCode.CONFLICT_REPAIR, ScenarioInteractionMode.HANDLE_CONFLICT),
        (SocialSkillCode.INVITATION, ScenarioInteractionMode.INVITE),
        (SocialSkillCode.QUESTION_ASKING, ScenarioInteractionMode.ASK_QUESTION),
        (SocialSkillCode.SELF_INTRODUCTION, ScenarioInteractionMode.INTRODUCE_SELF),
        (SocialSkillCode.CONVERSATION_INITIATION, ScenarioInteractionMode.START_CONVERSATION),
        (SocialSkillCode.SPECIFIC_REQUEST, ScenarioInteractionMode.MAKE_REQUEST),
    )
    for skill, mode in priority:
        if skill in skills:
            return mode
    if SocialSkillCode.ASSERTIVE_EXPRESSION in skills:
        return ScenarioInteractionMode.EXPRESS_VIEW
    return ScenarioInteractionMode.UNSPECIFIED


def _context_tags(text: str) -> list[str]:
    tags: list[str] = []
    terms_by_tag = {
        "education": ("课堂", "老师", "课程", "作业", "面试"),
        "shared_living": ("宿舍", "室友", "共同居住"),
        "group": ("小组", "社团", "团队", "群里"),
        "request_response": ("请求", "拒绝", "借", "额外任务"),
        "first_meeting": ("陌生", "第一次", "破冰", "不熟悉"),
    }
    for tag, terms in terms_by_tag.items():
        if any(term in text for term in terms):
            tags.append(tag)
    return tags[:5]
