"""Read-only projection for sessions created before open scenarios."""


_LEGACY_DESCRIPTIONS = {
    "classroom_speech": "在课堂中练习清楚表达自己的观点",
    "group_discussion": "在小组讨论中表达观点并参与协作",
    "dorm_conflict": "与共同居住者沟通一个具体问题",
    "club_icebreaking": "在社团破冰活动中与不熟悉的人开始交流",
    "invite_classmate_meal": "自然地邀请同学一起参加轻量活动",
    "ask_teacher_question": "向老师提出一个具体问题",
    "interview_self_intro": "在面试情境中进行简短自我介绍",
    "refuse_request": "礼貌而清楚地拒绝一个请求",
    "express_disagreement": "清楚表达不同意见并保持尊重",
}


def project_legacy_scenario(value: str) -> str:
    """Translate one persisted legacy code without exposing it to new writes."""
    return _LEGACY_DESCRIPTIONS.get(value, value)
