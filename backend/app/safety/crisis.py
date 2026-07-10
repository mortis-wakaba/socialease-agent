"""Shared non-medical crisis escalation copy for direct APIs and skills."""


def crisis_escalation_response(*, paused_activity: str) -> str:
    """Return a consistent crisis response for paused SocialEase activities."""
    return (
        f"这个输入包含危机风险表达，系统会先暂停{paused_activity}。"
        "请立刻联系可信任的人、学校心理中心或当地紧急服务；如果你可能马上伤害自己或他人，"
        "请不要独处，并尽快寻求现场帮助。"
    )


def full_crisis_escalation_response() -> str:
    """Return the longer crisis response used by the lead escalation skill."""
    return (
        "我很担心你现在的安全。这个系统不能处理危机，也不能替代专业帮助。\n\n"
        "如果你可能马上伤害自己或他人，请立刻联系当地紧急服务，或请身边可信任的人陪你一起求助。"
        "如果你在学校，也建议尽快联系学校心理中心、辅导员或宿舍管理人员。\n\n"
        "在获得现实帮助前，尽量不要独处，远离可能伤害自己或他人的物品，并把这条信息直接发给一个"
        "你信任的人：我现在不安全，需要你马上陪我联系帮助。"
    )
