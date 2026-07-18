"""Deterministic skills for clarification and product-boundary responses."""

from app.models import Intent
from app.skills.base import SkillContext, SkillDescriptor, SkillResult


class ClarificationSkill:
    """Ask one bounded question without starting a state-changing workflow."""

    descriptor = SkillDescriptor(
        name="clarification_skill",
        description="Clarifies an underspecified in-domain request before selecting an action.",
        supported_intents=(Intent.CLARIFICATION_NEEDED,),
        entrypoint="app.skills.boundary.ClarificationSkill.run",
        safety_notes="Runs after safety classification and never starts a practice action.",
    )

    async def run(self, context: SkillContext) -> SkillResult:
        """Offer a small set of non-medical next-step choices."""
        del context
        return SkillResult(
            response=(
                "我可以先帮你把需求说清楚。你现在更希望：先聊聊发生了什么、"
                "整理当时的想法、练习一句具体表达，还是制定一个低强度小步骤？"
            ),
            structured_data={
                "agent": "clarification_agent",
                "action": "clarification_requested",
                "options": [
                    "general_support",
                    "structured_reflection",
                    "roleplay_practice",
                    "graded_practice_plan",
                ],
                "state_changed": False,
            },
            selected_agent="clarification_agent",
        )


class OutOfScopeSkill:
    """Return a concise product boundary without pretending to be a general assistant."""

    descriptor = SkillDescriptor(
        name="out_of_scope_skill",
        description="Declines requests outside the bounded SocialEase product domain.",
        supported_intents=(Intent.OUT_OF_SCOPE,),
        entrypoint="app.skills.boundary.OutOfScopeSkill.run",
        safety_notes="Runs after safety classification and performs no tools or persistence writes.",
    )

    async def run(self, context: SkillContext) -> SkillResult:
        """Explain the supported scope without inventing an answer."""
        del context
        return SkillResult(
            response=(
                "这个请求不在 SocialEase 当前的能力范围内。这里主要提供社交压力支持、"
                "沟通练习、结构化反思、分级练习计划和已审核公开资源导航。"
                "如果它和某个社交场景有关，你可以补充具体情境，我再帮你拆解。"
            ),
            structured_data={
                "agent": "product_boundary_agent",
                "action": "out_of_scope",
                "state_changed": False,
                "supported_capabilities": [
                    "social_support",
                    "roleplay",
                    "worksheet",
                    "graded_practice",
                    "reviewed_resources",
                ],
            },
            selected_agent="product_boundary_agent",
        )
