"""Deterministic fallback for non-medical social support."""

from typing import Any

from app.models import Intent, RiskLevel, SafetyResult


class SupportAgent:
    """Return a small safe response when grounded LLM generation is unavailable."""

    def respond(
        self,
        message: str,
        intent: Intent,
        safety_result: SafetyResult,
    ) -> tuple[str, dict[str, Any]]:
        """Return an intent-bounded fallback without attempting scene interpretation."""
        del message
        response_parts = [
            self._risk_tone(safety_result.risk_level),
            self._intent_line(intent),
            self._fallback_exercise(intent),
            (
                "我不能做诊断，也不能替代心理咨询或现实支持；"
                "这只是非医疗化的社交自助练习，你可以随时暂停、退出或把步骤调小。"
            ),
        ]
        if safety_result.risk_level == RiskLevel.HIGH:
            response_parts.append(
                "当前更适合先降低练习强度，并联系可信任的人或其他可获得的现实支持。"
            )
        return "\n\n".join(response_parts), {
            "agent": "support_agent",
            "action": "deterministic_support_fallback",
            "safety_boundary": (
                "non_diagnostic_self_help_only; not a substitute for counseling"
            ),
            "suggested_next_steps": [
                "write_one_concern",
                "draft_one_small_expression",
                "pause_or_reduce_if_needed",
            ],
            "echo": {
                "intent": intent.value,
                "risk_level": safety_result.risk_level.value,
            },
        }

    @staticmethod
    def _risk_tone(risk_level: RiskLevel) -> str:
        if risk_level == RiskLevel.HIGH:
            return "听起来这件事已经带来很强的压力，我们先把安全和降低强度放在前面。"
        if risk_level == RiskLevel.MEDIUM:
            return "听起来你正在承受不小的社交压力，可以先停在一个很小的步骤。"
        return "我听到了你的社交压力，可以先从一个很小、可控的步骤开始。"

    @staticmethod
    def _intent_line(intent: Intent) -> str:
        if intent == Intent.ROLEPLAY_PRACTICE:
            return "你可以选择一个低强度场景开始；只要说“暂停”，当前角色扮演就应停止。"
        if intent == Intent.CBT_WORKSHEET:
            return "可以只整理当前情境、最担心的想法和一个下一步，不需要一次填完整。"
        if intent == Intent.EXPOSURE_PLANNING:
            return "练习计划应从可控的小任务开始，并允许暂停、降级或改成书面准备。"
        if intent == Intent.CAMPUS_RESOURCE_QUERY:
            return "支持资源必须来自已审核的公开资料；没有可靠信息时应明确说明不知道。"
        if intent == Intent.PROGRESS_REVIEW:
            return "可以先复盘一个已完成的小步骤，而不是只评价整体表现。"
        return "可以先写下最担心的一句话，再区分已经发生的事实和还未发生的预测。"

    @staticmethod
    def _fallback_exercise(intent: Intent) -> str:
        if intent == Intent.ROLEPLAY_PRACTICE:
            return "最低强度可以只写一句准备表达的话，暂时不进入真实对话。"
        return "最低强度可以只写一句更具体、不预设结果的表达，再决定是否继续。"
