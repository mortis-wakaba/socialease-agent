"""Role-play agent for safe, RAG-grounded social practice scenarios."""

from app.llm.base import BaseLLMClient
from app.llm.prompts import (
    build_roleplay_system_prompt,
    build_roleplay_user_prompt,
)
from app.llm.retry import ProviderError
from app.models_roleplay import (
    RoleplayFeedback,
    RoleplayGuidance,
    RoleplayMessageFeatures,
    RoleplayRubricBreakdown,
    RoleplayRubricSignal,
    RoleplayScenario,
    RoleplaySession,
)
from app.models_llm import LLMUsage
from app.models_session_context import RoleplayPromptContext


class RoleplayAgent:
    """Generate optional LLM-backed role-play turns plus structured feedback."""

    def __init__(self, llm_client: BaseLLMClient | None = None) -> None:
        self.llm_client = llm_client

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
                "本次场景参考了社交技巧知识库中的建议："
                f"{guidance.citations[0].snippet}"
            )
        return (
            f"{base} 当前难度为 {difficulty}/5。\n"
            f"{guidance_line}\n"
            "这只是社交表达练习，不是诊断或治疗。"
        )

    async def next_turn(
        self,
        session: RoleplaySession,
        user_message: str,
        prompt_context: RoleplayPromptContext | None = None,
    ) -> tuple[str, LLMUsage]:
        """Return a next turn plus compact LLM execution metadata."""
        if self.llm_client is not None:
            try:
                return await self._llm_next_turn(
                    session,
                    user_message,
                    prompt_context=prompt_context,
                ), LLMUsage(
                    used=True,
                    fallback_used=False,
                )
            except Exception as exc:
                category = (
                    exc.category.value
                    if isinstance(exc, ProviderError)
                    else "TRANSIENT_PROVIDER_ERROR"
                )
                return self._deterministic_next_turn(session, user_message), LLMUsage(
                    used=False,
                    fallback_used=True,
                    error_category=category,
                )
        return self._deterministic_next_turn(session, user_message), LLMUsage()

    async def _llm_next_turn(
        self,
        session: RoleplaySession,
        user_message: str,
        *,
        prompt_context: RoleplayPromptContext | None = None,
    ) -> str:
        """Generate one grounded role-play turn through the configured LLM."""
        recent_messages = (
            prompt_context.recent_messages
            if prompt_context is not None
            else [
                f"{message.role.value}: {message.content}"
                for message in session.messages
            ]
        )
        guidance = (
            session.retrieved_guidance.answer
            if not session.retrieved_guidance.no_guidance_found
            else "No specific guidance found; use general safe role-play scaffolding."
        )
        return await self.llm_client.generate_text(
            system_prompt=build_roleplay_system_prompt(),
            user_prompt=build_roleplay_user_prompt(
                scenario=session.scenario.value,
                difficulty=session.difficulty,
                guidance=guidance,
                recent_messages=recent_messages,
                user_message=user_message,
                compact_state=(
                    prompt_context.compact_state.model_dump(mode="json")
                    if prompt_context is not None
                    and prompt_context.compact_state is not None
                    else None
                ),
                retrieved_memories=(
                    prompt_context.retrieved_memories
                    if prompt_context is not None
                    else []
                ),
            ),
        )

    def _deterministic_next_turn(
        self,
        session: RoleplaySession,
        user_message: str,
    ) -> str:
        """Return the deterministic MVP fallback turn."""
        prompt = self.scenario_prompts[session.scenario]
        if session.difficulty >= 4:
            return f"{prompt} 我会稍微追问得更具体一些。"
        if len(user_message) < 12:
            return f"{prompt} 你也可以把句子说完整一点。"
        return prompt

    def feedback(self, session: RoleplaySession) -> RoleplayFeedback:
        """Generate structured feedback from privacy-safe derived features."""
        user_messages = [message for message in session.messages if message.role.value == "user"]
        feature_items = [message.features for message in user_messages if message.features is not None]
        rubric_breakdown: list[RoleplayRubricBreakdown] = []

        if feature_items:
            rubric_breakdown = self._evaluate_feature_rubric(feature_items)
            scores_by_dimension = {
                item.dimension: item.score for item in rubric_breakdown
            }
            clarity_score = scores_by_dimension["clarity"]
            naturalness_score = scores_by_dimension["naturalness"]
            assertiveness_score = scores_by_dimension["assertiveness"]
            empathy_score = scores_by_dimension["empathy"]
            strengths = self._feature_strengths(rubric_breakdown)
            suggestions = self._feature_suggestions(rubric_breakdown)
            next_try_prompt = self._next_try_prompt(rubric_breakdown)
        else:
            clarity_score, naturalness_score, assertiveness_score, empathy_score = (
                self._legacy_content_scores([message.content for message in user_messages])
            )
            strengths = [
                "你已经完成了一次真实场景的表达练习。",
                "你的回应开始包含具体意图，这有助于对方理解。",
            ]
            suggestions = [
                "下一轮可以先说核心诉求，再补充一个简短理由。",
                "如果要表达边界，可以使用“我希望/我暂时不能/我更适合”的句式。",
            ]
            next_try_prompt = "请用两句话重试：第一句表达你的核心意思，第二句补充一个具体理由。"

        return RoleplayFeedback(
            clarity_score=clarity_score,
            naturalness_score=naturalness_score,
            assertiveness_score=assertiveness_score,
            empathy_score=empathy_score,
            rubric_breakdown=rubric_breakdown,
            strengths=strengths,
            suggestions=suggestions,
            next_try_prompt=next_try_prompt,
            citations=session.retrieved_guidance.citations,
        )

    @staticmethod
    def _score(value: int) -> int:
        return max(1, min(5, value))

    def _legacy_content_scores(self, user_messages: list[str]) -> tuple[int, int, int, int]:
        """Fallback scoring for old sessions created before feature metadata existed."""
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
        return clarity_score, naturalness_score, assertiveness_score, empathy_score

    def _evaluate_feature_rubric(
        self,
        features: list[RoleplayMessageFeatures],
    ) -> list[RoleplayRubricBreakdown]:
        """Evaluate role-play feedback from non-verbatim derived features."""
        average_length = sum(item.char_count for item in features) / len(features)
        sentence_total = sum(item.sentence_count for item in features)

        clarity = [
            self._signal("sufficient_detail", "表达长度足以承载一个完整意思", average_length >= 12),
            self._signal("reason_given", "包含简短理由或解释", self._has_reason(features)),
            self._signal("specific_anchor", "包含具体时间、地点或下一步锚点", self._has_specificity(features)),
            self._signal(
                "clear_request",
                "包含可被对方理解的诉求或问题",
                self._has_request(features),
            ),
        ]
        naturalness = [
            self._signal("polite_buffer", "使用了礼貌缓冲或日常表达", self._has_politeness(features)),
            self._signal("conversation_move", "包含问题、邀请或接话动作", self._has_request(features)),
            self._signal(
                "collaborative_option",
                "给出了协商或一起推进的可能",
                self._has_collaboration(features),
            ),
            self._signal(
                "manageable_length",
                "长度适合口头练习",
                8 <= average_length <= 140 and sentence_total <= 5,
            ),
        ]
        assertiveness = [
            self._signal("boundary_or_need", "清楚表达了需求或边界", self._has_boundary(features)),
            self._signal("self_position", "使用第一人称说明自己的立场", self._sum(features, "first_person_count") > 0),
            self._signal("actionable_request", "提出了可执行请求", self._has_request(features)),
            self._signal("reason_supported", "用理由支撑表达", self._has_reason(features)),
        ]
        empathy = [
            self._signal("acknowledgement", "回应中包含理解或感谢", self._has_empathy(features), weight=2),
            self._signal("repair_marker", "包含道歉、修正或承认影响的信号", self._has_repair(features)),
            self._signal("polite_marker", "包含礼貌表达", self._has_politeness(features)),
            self._signal("shared_next_step", "尝试给出共同下一步", self._has_collaboration(features)),
        ]

        return [
            self._breakdown("clarity", clarity, "清晰度关注核心意思、理由和具体锚点。"),
            self._breakdown("naturalness", naturalness, "自然度关注日常可说性和对话推进感。"),
            self._breakdown("assertiveness", assertiveness, "坚定度关注需求、边界和可执行请求。"),
            self._breakdown("empathy", empathy, "共情度关注理解、礼貌和共同下一步。"),
        ]

    def _breakdown(
        self,
        dimension: str,
        signals: list[RoleplayRubricSignal],
        rationale: str,
    ) -> RoleplayRubricBreakdown:
        """Build one scored rubric dimension."""
        score = self._score(1 + sum(signal.weight for signal in signals if signal.present))
        return RoleplayRubricBreakdown(
            dimension=dimension,
            score=score,
            signals=signals,
            rationale=rationale,
        )

    @staticmethod
    def _signal(
        name: str,
        label: str,
        present: bool,
        weight: int = 1,
    ) -> RoleplayRubricSignal:
        return RoleplayRubricSignal(
            name=name,
            label=label,
            present=present,
            weight=weight,
        )

    def _feature_strengths(
        self,
        breakdown: list[RoleplayRubricBreakdown],
    ) -> list[str]:
        """Return practice-oriented strengths without clinical language."""
        strengths = ["你已经完成了一次真实场景的表达练习。"]
        for item in breakdown:
            present = [signal.label for signal in item.signals if signal.present]
            if present:
                strengths.append(f"{self._dimension_label(item.dimension)}：{present[0]}。")
        return strengths[:3]

    def _feature_suggestions(
        self,
        breakdown: list[RoleplayRubricBreakdown],
    ) -> list[str]:
        """Return suggestions based on missing rubric signals."""
        suggestions: list[str] = []
        for item in breakdown:
            missing = [signal for signal in item.signals if not signal.present]
            if missing:
                suggestions.append(
                    f"{self._dimension_label(item.dimension)}可以加强：{missing[0].label}。"
                )
        return suggestions[:3] or ["下一轮可以保持当前结构，再把语气调整得更贴近日常表达。"]

    def _next_try_prompt(self, breakdown: list[RoleplayRubricBreakdown]) -> str:
        """Return one compact next attempt prompt from the weakest dimension."""
        weakest = min(breakdown, key=lambda item: item.score)
        if weakest.dimension == "empathy":
            return "请重试一版：先用一句话表示理解，再说出你的请求或边界。"
        if weakest.dimension == "assertiveness":
            return "请重试一版：用“我希望/我暂时不能/我可以...”说清楚你的边界和下一步。"
        if weakest.dimension == "naturalness":
            return "请重试一版：把表达改成更像日常对话的一到两句话。"
        return "请重试一版：第一句说核心意思，第二句补充一个具体理由或下一步。"

    @staticmethod
    def _dimension_label(dimension: str) -> str:
        labels = {
            "clarity": "清晰度",
            "naturalness": "自然度",
            "assertiveness": "坚定度",
            "empathy": "共情度",
        }
        return labels.get(dimension, dimension)

    @staticmethod
    def _sum(features: list[RoleplayMessageFeatures], field_name: str) -> int:
        return sum(getattr(item, field_name) for item in features)

    def _has_reason(self, features: list[RoleplayMessageFeatures]) -> bool:
        return self._sum(features, "reason_marker_count") > 0 or any(
            item.has_reason for item in features
        )

    def _has_request(self, features: list[RoleplayMessageFeatures]) -> bool:
        return (
            self._sum(features, "request_marker_count") > 0
            or self._sum(features, "question_count") > 0
            or any(item.has_request for item in features)
        )

    def _has_boundary(self, features: list[RoleplayMessageFeatures]) -> bool:
        return self._sum(features, "boundary_marker_count") > 0 or any(
            item.has_boundary_statement for item in features
        )

    def _has_empathy(self, features: list[RoleplayMessageFeatures]) -> bool:
        return self._sum(features, "empathy_marker_count") > 0 or any(
            item.has_empathy_marker for item in features
        )

    def _has_specificity(self, features: list[RoleplayMessageFeatures]) -> bool:
        return self._sum(features, "specificity_marker_count") > 0 or any(
            item.has_specific_time_or_place for item in features
        )

    def _has_politeness(self, features: list[RoleplayMessageFeatures]) -> bool:
        return self._sum(features, "politeness_marker_count") > 0 or any(
            item.has_polite_opening for item in features
        )

    def _has_collaboration(self, features: list[RoleplayMessageFeatures]) -> bool:
        return self._sum(features, "collaborative_marker_count") > 0 or any(
            item.has_collaborative_offer for item in features
        )

    def _has_repair(self, features: list[RoleplayMessageFeatures]) -> bool:
        return self._sum(features, "repair_marker_count") > 0 or any(
            item.has_repair_or_acknowledgement for item in features
        )
