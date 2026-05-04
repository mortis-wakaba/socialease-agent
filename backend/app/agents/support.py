"""Non-medical support agent for low and medium-risk social stress messages."""

from typing import Any

from app.models import Intent, RiskLevel, SafetyResult


class SupportAgent:
    """Generate safe, non-diagnostic support and next-step guidance."""

    def respond(
        self,
        message: str,
        intent: Intent,
        safety_result: SafetyResult,
    ) -> tuple[str, dict[str, Any]]:
        """Return a response and lightweight structured data for the UI."""
        tone_line = self._risk_tone(safety_result.risk_level)
        intent_line = self._intent_line(intent)
        response = (
            f"{tone_line}\n\n"
            "我不能做诊断，也不能替代心理咨询；但我可以帮你把这个社交场景拆小一点，"
            "先找到一个今天能尝试的安全步骤。\n\n"
            f"{intent_line}\n"
            "一个可执行的小练习是：写下这次场景里最担心的一句话，然后准备一个更具体、"
            "更温和的表达版本。练习后可以记录焦虑强度从 0 到 10 的变化。\n\n"
            "如果这种压力已经明显影响安全、睡眠、上课或日常生活，建议同时联系可信任的人、"
            "辅导员或学校心理中心获取现实支持。"
        )
        structured_data = {
            "agent": "support_agent",
            "safety_boundary": (
                "non_diagnostic_self_help_only; not a substitute for counseling"
            ),
            "suggested_next_steps": [
                "name_the_social_situation",
                "write_one_feared_thought",
                "draft_one_safer_expression",
                "rate_anxiety_0_to_10",
            ],
            "echo": {
                "intent": intent.value,
                "risk_level": safety_result.risk_level.value,
            },
        }
        return response, structured_data

    @staticmethod
    def _risk_tone(risk_level: RiskLevel) -> str:
        if risk_level == RiskLevel.HIGH:
            return (
                "听起来这件事已经带来很强的压力。我们先把安全和现实支持放在前面。"
            )
        if risk_level == RiskLevel.MEDIUM:
            return "听起来你正在承受不小的社交压力，我们可以先做一个结构化的小练习。"
        return "我听到了你的社交压力，我们可以先从一个很小、可控的步骤开始。"

    @staticmethod
    def _intent_line(intent: Intent) -> str:
        if intent == Intent.ROLEPLAY_PRACTICE:
            return "你像是想做社交情境模拟。MVP 先给出准备步骤，后续会接入多轮 role-play 节点。"
        if intent == Intent.CBT_WORKSHEET:
            return "你像是想做 CBT 风格自助练习。后续会输出完整 worksheet 结构。"
        if intent == Intent.EXPOSURE_PLANNING:
            return "你像是想做分级暴露计划。后续会生成由易到难的练习阶梯。"
        if intent == Intent.CAMPUS_RESOURCE_QUERY:
            return "你像是在找校园支持资源。后续会接入带引用的 demo RAG 知识库。"
        if intent == Intent.PROGRESS_REVIEW:
            return "你像是想复盘练习进度。后续会接入练习记录和进度页。"
        return "这一步先聚焦情绪支持和现实可执行的小行动。"

