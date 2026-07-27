"""Migration boundary for modules that now run only in unified conversations."""

from app.skills.base import SkillResult


def unified_conversation_result(module_label: str) -> SkillResult:
    """Direct a legacy module request to chat without creating domain state."""
    return SkillResult(
        response=(
            f"请在统一对话中继续；系统会先给出{module_label}选项，"
            "只有你确认后才会在当前对话里开始。"
        ),
        structured_data={
            "agent": "lead_harness",
            "action": "use_unified_conversation",
            "next_ui": "chat",
            "consent_required": True,
            "deprecated_entrypoint": True,
            "blocked": False,
        },
        selected_agent="lead_harness",
    )
