"""RAG-grounded LLM support generation with CBT structure and safe fallback."""

import json
import re
from typing import Any

from pydantic import ValidationError

from app.agents.support import SupportAgent
from app.knowledge.service import KnowledgeService
from app.llm.base import BaseLLMClient
from app.llm.prompts import build_support_system_prompt, build_support_user_prompt
from app.llm.retry import ProviderError
from app.models import Intent, RiskLevel, SafetyResult
from app.models_context import SupportGenerationContext
from app.models_knowledge import KnowledgeBaseType
from app.models_llm import LLMUsage
from app.models_support_generation import PresentationConstraints, SupportGeneration
from app.privacy.redaction import (
    redact_sensitive_identifiers,
    redact_validated_candidates,
)


class SupportGenerationAgent:
    """Generate bounded CBT-style support and retain a deterministic fallback."""

    def __init__(
        self,
        *,
        llm_client: BaseLLMClient | None,
        knowledge: KnowledgeService | None = None,
        fallback_agent: SupportAgent | None = None,
    ) -> None:
        self.llm_client = llm_client
        self.knowledge = knowledge or KnowledgeService()
        self.fallback_agent = fallback_agent or SupportAgent()

    async def respond(
        self,
        *,
        message: str,
        intent: Intent,
        safety_result: SafetyResult,
        support_context: SupportGenerationContext | None = None,
        application_constraints: PresentationConstraints | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Return validated LLM support or a deterministic safe fallback."""
        if self.llm_client is None:
            return self._fallback(
                message=message,
                intent=intent,
                safety_result=safety_result,
                fallback_reason="LLM_DISABLED",
                application_constraints=application_constraints,
            )
        if safety_result.risk_level == RiskLevel.HIGH:
            return self._fallback(
                message=message,
                intent=intent,
                safety_result=safety_result,
                fallback_reason="HIGH_RISK_DETERMINISTIC_SUPPORT",
                application_constraints=application_constraints,
            )

        guidance = self.knowledge.query(message, KnowledgeBaseType.SOCIAL_SKILLS)
        try:
            raw_output = await self.llm_client.generate_text(
                system_prompt=build_support_system_prompt(),
                user_prompt=build_support_user_prompt(
                    message=message,
                    intent=intent.value,
                    risk_level=safety_result.risk_level.value,
                    retrieved_guidance=[
                        {
                            "title": citation.title,
                            "snippet": citation.snippet,
                        }
                        for citation in guidance.citations
                    ],
                    application_context=_support_context_payload(support_context),
                    response_constraints=(
                        application_constraints.model_dump(mode="json")
                        if application_constraints is not None
                        else {}
                    ),
                ),
                temperature=0.1,
            )
            proposal = _parse_support_generation(raw_output)
            _validate_support_generation(proposal, message=message, intent=intent)
        except SupportGuardrailError as exc:
            return self._fallback(
                message=message,
                intent=intent,
                safety_result=safety_result,
                fallback_reason="OUTPUT_GUARDRAIL",
                error_category=exc.category,
                validation_issues=[str(exc)],
                application_constraints=application_constraints,
            )
        except json.JSONDecodeError as exc:
            return self._fallback(
                message=message,
                intent=intent,
                safety_result=safety_result,
                fallback_reason="JSON_PARSE_ERROR",
                error_category="JSON_PARSE_ERROR",
                validation_issues=[f"line_{exc.lineno}:column_{exc.colno}"],
                application_constraints=application_constraints,
            )
        except ValidationError as exc:
            return self._fallback(
                message=message,
                intent=intent,
                safety_result=safety_result,
                fallback_reason="SCHEMA_VALIDATION_ERROR",
                error_category="SCHEMA_VALIDATION_ERROR",
                validation_issues=_safe_validation_issues(exc),
                application_constraints=application_constraints,
            )
        except ValueError as exc:
            return self._fallback(
                message=message,
                intent=intent,
                safety_result=safety_result,
                fallback_reason="JSON_SHAPE_ERROR",
                error_category="JSON_SHAPE_ERROR",
                validation_issues=[str(exc)[:160]],
                application_constraints=application_constraints,
            )
        except Exception as exc:
            category = (
                exc.category.value
                if isinstance(exc, ProviderError)
                else "TRANSIENT_PROVIDER_ERROR"
            )
            return self._fallback(
                message=message,
                intent=intent,
                safety_result=safety_result,
                fallback_reason="PROVIDER_ERROR",
                error_category=category,
                application_constraints=application_constraints,
            )

        constraints = _resolve_presentation_constraints(
            message=message,
            proposed=proposal.presentation_constraints,
            application=application_constraints,
        )
        composed_response = _compose_support_response(proposal, constraints=constraints)
        response, deterministic_categories = redact_sensitive_identifiers(composed_response)
        response, semantic_categories = redact_validated_candidates(
            response,
            [
                (candidate.text, candidate.category)
                for candidate in proposal.privacy_candidates
            ],
        )
        safe_steps = [
            redact_sensitive_identifiers(step)[0] for step in proposal.practice_steps
        ]
        safe_steps = [
            redact_validated_candidates(
                step,
                [
                    (candidate.text, candidate.category)
                    for candidate in proposal.privacy_candidates
                ],
            )[0]
            for step in safe_steps
        ]
        return response, {
            "agent": "support_generation_agent",
            "action": "bounded_support_generation",
            "response_mode": proposal.response_mode,
            "presentation_constraints": constraints.model_dump(mode="json"),
            "pause_supported": proposal.pause_supported,
            "needs_real_support": proposal.needs_real_support,
            "suggested_next_steps": safe_steps,
            "citations": [
                citation.model_dump(mode="json") for citation in guidance.citations
            ],
            "retrieval_unknown": guidance.unknown,
            "llm_usage": LLMUsage(used=True).model_dump(mode="json"),
            "fallback_used": False,
            "support_context_fields": sorted(
                _support_context_payload(support_context)
            ),
            "privacy_redaction": {
                "deterministic_categories": deterministic_categories,
                "semantic_categories": semantic_categories,
            },
            "safety_boundary": (
                "non_diagnostic_self_help_only; not a substitute for counseling"
            ),
        }

    def _fallback(
        self,
        *,
        message: str,
        intent: Intent,
        safety_result: SafetyResult,
        fallback_reason: str,
        error_category: str | None = None,
        validation_issues: list[str] | None = None,
        application_constraints: PresentationConstraints | None = None,
    ) -> tuple[str, dict[str, Any]]:
        response, data = self.fallback_agent.respond(message, intent, safety_result)
        constraints = _resolve_presentation_constraints(
            message=message,
            proposed=PresentationConstraints(),
            application=application_constraints,
        )
        response = _apply_presentation_constraints(response, constraints)
        result = {
            **data,
            "fallback_used": True,
            "fallback_reason": fallback_reason,
            "llm_usage": LLMUsage(
                fallback_used=self.llm_client is not None,
                error_category=error_category,
            ).model_dump(mode="json"),
            "presentation_constraints": constraints.model_dump(mode="json"),
        }
        if validation_issues:
            result["validation_issues"] = validation_issues
        return response, result


def _parse_support_generation(raw_output: str) -> SupportGeneration:
    """Parse one strict JSON support proposal."""
    normalized = re.sub(
        r"^```(?:json)?\s*|\s*```$",
        "",
        raw_output.strip(),
        flags=re.IGNORECASE,
    )
    payload = json.loads(normalized)
    if not isinstance(payload, dict):
        raise ValueError("Support generation response must be a JSON object.")
    return SupportGeneration.model_validate(payload)


def _support_context_payload(
    support_context: SupportGenerationContext | None,
) -> dict[str, object]:
    """Return only validated non-null preference fields for prompt injection."""
    if support_context is None:
        return {}
    return support_context.model_dump(mode="json", exclude_none=True)


class SupportGuardrailError(ValueError):
    """Raised when a parsed support proposal violates an output boundary."""

    def __init__(self, message: str, *, category: str = "OUTPUT_GUARDRAIL") -> None:
        super().__init__(message)
        self.category = category


def _validate_support_generation(
    proposal: SupportGeneration,
    *,
    message: str,
    intent: Intent,
) -> None:
    """Reject medical, coercive, dependent, or fabricated-contact output."""
    text = " ".join(_proposal_text_values(proposal)).casefold()
    prohibited_patterns = (
        r"你(?:就是|已经|肯定)?患有",
        r"(?:确诊|诊断为).{0,12}(?:障碍|疾病|焦虑症|社交焦虑)",
        r"(?:一定|保证).{0,12}(?:治好|康复|有效|好起来)",
        r"(?:不要|不必).{0,10}(?:告诉|联系).{0,16}(?:家人|朋友|老师|辅导员|现实支持)",
        r"只(?:需要)?和(?:我|系统|agent)聊",
        r"(?:不能|不许|不要)暂停",
        r"必须.{0,16}(?:最高难度|高强度|立刻完成)",
        r"(?<!\d)1[3-9]\d{9}(?!\d)",
    )
    if any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in prohibited_patterns):
        raise SupportGuardrailError("Support generation failed output guardrails.")
    if (
        proposal.automatic_thought is not None
        and proposal.automatic_thought.strip() not in message
    ):
        raise SupportGuardrailError(
            "Automatic thought must be an explicit span from the user message."
        )
    if proposal.needs_real_support and not proposal.real_support_note:
        raise SupportGuardrailError("Real-support flag requires a bounded support note.")
    if proposal.response_mode == "direct_practice":
        if intent != Intent.EMOTIONAL_SUPPORT or not _requests_direct_wording(message):
            raise SupportGuardrailError(
                "direct_practice requires an explicit request for directly usable wording.",
                category="MODE_SELECTION_GUARDRAIL",
            )


def _proposal_text_values(proposal: SupportGeneration) -> list[str]:
    payload = proposal.model_dump(mode="python")
    values: list[str] = []
    for value in payload.values():
        if isinstance(value, str):
            values.append(value)
        elif isinstance(value, list):
            values.extend(item for item in value if isinstance(item, str))
    return values


def _compose_support_response(
    proposal: SupportGeneration,
    *,
    constraints: PresentationConstraints | None = None,
) -> str:
    """Compose an application-owned response from validated model fields."""
    resolved = constraints or proposal.presentation_constraints
    if proposal.response_mode == "direct_practice":
        return _apply_presentation_constraints(proposal.suggested_phrase or "", resolved)
    if proposal.response_mode == "clarify":
        return _apply_presentation_constraints(proposal.followup_question or "", resolved)

    sections: list[str] = []
    if proposal.acknowledgement:
        sections.append(proposal.acknowledgement)
    if proposal.situation_summary:
        sections.append(f"情境：{proposal.situation_summary}")
    if proposal.automatic_thought:
        sections.append(f"你刚才表达的担心可能是：{proposal.automatic_thought}")
    if proposal.fact_prediction_distinction:
        sections.append(f"事实与预测：{proposal.fact_prediction_distinction}")
    if proposal.balanced_thought:
        sections.append(f"可以尝试的平衡想法：{proposal.balanced_thought}")
    if proposal.suggested_phrase:
        sections.append(f"可以先练这句话：{proposal.suggested_phrase}")
    practice_steps = proposal.practice_steps[
        : resolved.item_count if resolved.item_count is not None else None
    ]
    steps = "\n".join(
        f"{index}. {step}" for index, step in enumerate(practice_steps, start=1)
    )
    sections.append(f"低强度步骤：\n{steps}")
    if proposal.followup_question:
        sections.append(proposal.followup_question)
    if proposal.real_support_note:
        sections.append(proposal.real_support_note)
    sections.append(
        "我不能做诊断，也不能替代心理咨询或现实支持；"
        "这只是非医疗化的社交自助练习，你可以随时暂停、退出或把步骤调小。"
    )
    return _apply_presentation_constraints("\n\n".join(sections), resolved)


def _requests_direct_wording(message: str) -> bool:
    """Recognize explicit requests for a phrase rather than a guided practice flow."""
    patterns = (
        r"(?:一|1|两|2|三|3)句(?:话|表达)",
        r"(?:怎么|如何)(?:说|回复|开口|表达)",
        r"(?:开场白|回复模板|话术|措辞)",
        r"只(?:回复|回答|输出|给).{0,8}(?:一句|表达|话术|模板)",
        r"帮我(?:写|改|拆成|组织).{0,8}(?:一句|表达|话术|回复)",
    )
    return any(re.search(pattern, message, flags=re.IGNORECASE) for pattern in patterns)


def _safe_validation_issues(exc: ValidationError) -> list[str]:
    """Return schema diagnostics without retaining model-generated field contents."""
    issues: list[str] = []
    for error in exc.errors(include_url=False, include_context=False, include_input=False)[:8]:
        location = ".".join(str(part) for part in error.get("loc", ())) or "root"
        issues.append(f"{location}:{error.get('type', 'validation_error')}")
    return issues


def _resolve_presentation_constraints(
    *,
    message: str,
    proposed: PresentationConstraints,
    application: PresentationConstraints | None = None,
) -> PresentationConstraints:
    """Merge model-extracted preferences with high-confidence user-text rules."""
    extracted = application or PresentationConstraints()
    max_chars = extracted.max_chars or proposed.max_chars
    match = re.search(r"(?:不超过|最多|控制在)\s*(\d{1,4})\s*(?:个)?字", message)
    if match:
        max_chars = min(1000, max(10, int(match.group(1))))

    output_format = (
        extracted.output_format
        if extracted.output_format != "plain"
        else proposed.output_format
    )
    if re.search(r"(?:只(?:回复|回答|输出|给)?|帮我写)?\s*(?:一|1)句话", message):
        output_format = "single_sentence"
    elif "不要分点" in message or "不要列点" in message:
        output_format = "plain"

    verbosity = (
        "brief"
        if extracted.verbosity == "brief"
        else proposed.verbosity
    )
    if max_chars is not None or output_format == "single_sentence" or "简短" in message:
        verbosity = "brief"
    return PresentationConstraints(
        verbosity=verbosity,
        max_chars=max_chars,
        output_format=output_format,
        requested_language=extracted.requested_language or proposed.requested_language,
        item_count=extracted.item_count or proposed.item_count,
        plain_language=extracted.plain_language or proposed.plain_language,
    )


def _apply_presentation_constraints(
    text: str,
    constraints: PresentationConstraints,
) -> str:
    """Apply deterministic, safe formatting constraints to composed output."""
    rendered = text.strip()
    if constraints.output_format == "single_sentence":
        first = re.split(r"(?<=[。！？!?])|\n", rendered, maxsplit=1)[0].strip()
        rendered = first or rendered
    if constraints.max_chars is not None and len(rendered) > constraints.max_chars:
        rendered = rendered[: constraints.max_chars].rstrip("，,；;：:、 ")
    return rendered
