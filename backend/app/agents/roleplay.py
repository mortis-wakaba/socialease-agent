"""Role-play agent for safe, RAG-grounded social practice scenarios."""

from app.models_roleplay import (
    RoleplayFeedback,
    RoleplayGuidance,
    RoleplayScenario,
    RoleplaySession,
)


class RoleplayAgent:
    """Generate deterministic MVP role-play turns and structured feedback."""

    scenario_openings: dict[RoleplayScenario, str] = {
        RoleplayScenario.CLASSROOM_SPEECH: "你现在在课堂上准备发言。我会扮演同学，先看着你，等你开口。",
        RoleplayScenario.GROUP_DISCUSSION: "我们正在小组讨论。我会扮演组员，问你：你觉得这个方案哪里最重要？",
        RoleplayScenario.DORM_CONFLICT: "我会扮演室友。你想和我沟通一个让你不舒服的宿舍问题。",
        RoleplayScenario.CLUB_ICEBREAKING: "我会扮演社团新同学。我们刚见面，你可以先做一个轻松开场。",
        RoleplayScenario.INVITE_CLASSMATE_MEAL: "我会扮演同学。你想自然地邀请我一起吃饭。",
        RoleplayScenario.ASK_TEACHER_QUESTION: "我会扮演老师。你可以向我提出一个具体问题。",
        RoleplayScenario.INTERVIEW_SELF_INTRO: "我会扮演面试官。请你做一个简短自我介绍。",
        RoleplayScenario.REFUSE_REQUEST: "我会扮演提出请求的人。你可以练习清楚、礼貌地拒绝。",
        RoleplayScenario.EXPRESS_DISAGREEMENT: "我会扮演持不同意见的同学。你可以练习表达不同看法。",
    }

    scenario_prompts: dict[RoleplayScenario, str] = {
        RoleplayScenario.CLASSROOM_SPEECH: "我听到了。你能用一句话先说出你的核心观点吗？",
        RoleplayScenario.GROUP_DISCUSSION: "这个想法有意思。你能补充一个理由，帮助小组理解你的判断吗？",
        RoleplayScenario.DORM_CONFLICT: "我可能没意识到这影响你了。你希望我们具体怎么调整？",
        RoleplayScenario.CLUB_ICEBREAKING: "你好，我也刚来。你可以问我一个轻松的问题，让对话继续。",
        RoleplayScenario.INVITE_CLASSMATE_MEAL: "听起来可以。你可以给我一个具体时间或地点吗？",
        RoleplayScenario.ASK_TEACHER_QUESTION: "这个问题可以。你能说明你已经尝试到哪一步了吗？",
        RoleplayScenario.INTERVIEW_SELF_INTRO: "谢谢。你能再补一句你和这个机会最相关的经历吗？",
        RoleplayScenario.REFUSE_REQUEST: "我有点失望。你能在保持边界的同时表达理解吗？",
        RoleplayScenario.EXPRESS_DISAGREEMENT: "我不太同意。你能用更具体的例子说明你的观点吗？",
    }

    scenario_queries: dict[RoleplayScenario, str] = {
        RoleplayScenario.CLASSROOM_SPEECH: "课堂发言 准备 核心观点 开场",
        RoleplayScenario.GROUP_DISCUSSION: "小组讨论 表达观点 补充理由 社交练习",
        RoleplayScenario.DORM_CONFLICT: "宿舍沟通 冲突 事实 影响 请求",
        RoleplayScenario.CLUB_ICEBREAKING: "社团迎新 寒暄 兴趣交换 活动签到",
        RoleplayScenario.INVITE_CLASSMATE_MEAL: "邀请同学吃饭 具体时间 地点 自然表达",
        RoleplayScenario.ASK_TEACHER_QUESTION: "向老师提问 具体问题 已经尝试",
        RoleplayScenario.INTERVIEW_SELF_INTRO: "面试 自我介绍 相关经历 简短表达",
        RoleplayScenario.REFUSE_REQUEST: "拒绝请求 表达边界 礼貌 理解",
        RoleplayScenario.EXPRESS_DISAGREEMENT: "表达不同意见 具体例子 清楚表达",
    }

    def guidance_query(self, scenario: RoleplayScenario) -> str:
        """Return the Social Skills RAG query for a scenario."""
        return self.scenario_queries[scenario]

    def opening(
        self,
        scenario: RoleplayScenario,
        difficulty: int,
        guidance: RoleplayGuidance,
    ) -> str:
        """Return a grounded opening message for a scenario."""
        base = self.scenario_openings[scenario]
        if guidance.no_guidance_found:
            guidance_line = "本次没有检索到足够相关的社交技巧文档，将使用通用、安全的练习脚手架。"
        else:
            guidance_line = (
                "本次场景参考了 Social Skills demo 知识库中的建议："
                f"{guidance.citations[0].snippet}"
            )
        return (
            f"{base} 当前难度为 {difficulty}/5。\n"
            f"{guidance_line}\n"
            "这只是社交表达练习，不是诊断或治疗。"
        )

    def next_turn(self, session: RoleplaySession, user_message: str) -> str:
        """Return the agent's next role-play turn."""
        prompt = self.scenario_prompts[session.scenario]
        if session.difficulty >= 4:
            return f"{prompt} 我会稍微追问得更具体一些。"
        if len(user_message) < 12:
            return f"{prompt} 你也可以把句子说完整一点。"
        return prompt

    def feedback(self, session: RoleplaySession) -> RoleplayFeedback:
        """Generate simple structured feedback from user messages."""
        user_messages = [
            message.content
            for message in session.messages
            if message.role.value == "user"
        ]
        joined = " ".join(user_messages)
        average_length = (
            sum(len(message) for message in user_messages) / len(user_messages)
            if user_messages
            else 0
        )

        clarity_score = self._score(
            2 + int(average_length >= 12) + int("因为" in joined or "我想" in joined)
        )
        naturalness_score = self._score(
            2 + int(any(term in joined for term in ("可以", "请", "谢谢", "想")))
        )
        assertiveness_score = self._score(
            2 + int(any(term in joined for term in ("我希望", "我不能", "我想", "我觉得")))
        )
        empathy_score = self._score(
            2 + int(any(term in joined for term in ("理解", "谢谢", "辛苦", "不好意思")))
        )

        return RoleplayFeedback(
            clarity_score=clarity_score,
            naturalness_score=naturalness_score,
            assertiveness_score=assertiveness_score,
            empathy_score=empathy_score,
            strengths=[
                "你已经完成了一次真实场景的表达练习。",
                "你的回应开始包含具体意图，这有助于对方理解。",
            ],
            suggestions=[
                "下一轮可以先说核心诉求，再补充一个简短理由。",
                "如果要表达边界，可以使用“我希望/我暂时不能/我更适合”的句式。",
            ],
            next_try_prompt="请用两句话重试：第一句表达你的核心意思，第二句补充一个具体理由。",
            citations=session.retrieved_guidance.citations,
        )

    @staticmethod
    def _score(value: int) -> int:
        return max(1, min(5, value))
