"""Global deterministic and optional semantic checks for final skill output."""

from __future__ import annotations

import json
import os
import re
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.llm.base import BaseLLMClient
from app.llm.factory import create_llm_client
from app.llm.prompts import (
    build_output_guardrail_system_prompt,
    build_output_guardrail_user_prompt,
    build_output_repair_system_prompt,
    build_output_repair_user_prompt,
)
from app.models import Intent, RiskLevel
from app.privacy.redaction import redact_sensitive_identifiers


class OutputViolationCategory(str, Enum):
    """Stable product-boundary categories understood by backend policy."""

    DIAGNOSIS = "diagnosis"
    TREATMENT_PROMISE = "treatment_promise"
    DEPENDENCY_ENCOURAGEMENT = "dependency_encouragement"
    REAL_SUPPORT_DISCOURAGEMENT = "real_support_discouragement"
    COERCIVE_PRACTICE = "coercive_practice"
    UNSAFE_SITUATION_REFRAMING = "unsafe_situation_reframing"
    INVENTED_USER_FACT = "invented_user_fact"
    FABRICATED_CONTACT = "fabricated_contact"


class GroundingMetadata(BaseModel):
    """Minimal retrieval facts safe to pass into the global output checkpoint."""

    model_config = ConfigDict(extra="forbid")

    retrieval_unknown: bool | None = None
    citation_count: int = Field(default=0, ge=0, le=20)
    citation_titles: list[str] = Field(default_factory=list, max_length=10)
    resource_contact_verified: bool = False


class OutputGuardrailAction(str, Enum):
    """Application-owned decision after all output checks."""

    ALLOW = "allow"
    AUGMENT = "augment"
    REPAIR = "repair"
    REPLACE = "replace"


class OutputBoundaryTier(str, Enum):
    """Operational severity tier for product-boundary enforcement."""

    HARD_SAFETY = "hard_safety"
    SOFT_FACTUAL = "soft_factual"


class SemanticCheckErrorType(str, Enum):
    """Stable, content-free reasons why semantic classification was unusable."""

    PROVIDER_ERROR = "provider_error"
    INVALID_JSON = "invalid_json"
    INVALID_PAYLOAD = "invalid_payload"
    SCHEMA_VALIDATION = "schema_validation"
    INVALID_EVIDENCE = "invalid_evidence"


class SemanticSchemaErrorCode(str, Enum):
    """Content-free validation subtypes safe for evals and product traces."""

    MISSING_FIELD = "missing_field"
    EXTRA_FIELD = "extra_field"
    INVALID_CATEGORY = "invalid_category"
    INVALID_TYPE = "invalid_type"
    CONSTRAINT_VIOLATION = "constraint_violation"
    OTHER = "other"


class SemanticOutputViolation(BaseModel):
    """One model-proposed violation with an exact output evidence span."""

    model_config = ConfigDict(extra="forbid")

    category: OutputViolationCategory
    evidence: str = Field(min_length=1, max_length=160)
    reason: str = Field(min_length=1, max_length=240)


class SemanticOutputAssessment(BaseModel):
    """Bounded semantic classifier response."""

    model_config = ConfigDict(extra="forbid")

    violations: list[SemanticOutputViolation] = Field(default_factory=list, max_length=8)


class OutputRepairProposal(BaseModel):
    """One strictly structured response returned by the repair model."""

    model_config = ConfigDict(extra="forbid")

    repaired_response: str = Field(min_length=1, max_length=2400)


class OutputGuardrailResult(BaseModel):
    """Trace-safe global output decision returned to the Harness."""

    action: OutputGuardrailAction
    response: str
    categories: list[OutputViolationCategory] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    semantic_checked: bool = False
    semantic_check_failed: bool = False
    semantic_error_type: SemanticCheckErrorType | None = None
    semantic_schema_error_code: SemanticSchemaErrorCode | None = None
    semantic_schema_error_field: str | None = None
    semantic_retry_attempted: bool = False
    violation_tier: OutputBoundaryTier | None = None
    repair_attempted: bool = False
    repair_succeeded: bool = False
    repair_recheck_action: OutputGuardrailAction | None = None
    semantic_diagnostics: list[SemanticOutputViolation] = Field(
        default_factory=list,
        exclude=True,
    )


_DETERMINISTIC_PATTERNS: tuple[
    tuple[OutputViolationCategory, re.Pattern[str]], ...
] = (
    (
        OutputViolationCategory.DIAGNOSIS,
        re.compile(
            r"你(?:就是|已经|肯定|基本可以断定)?患有|"
            r"(?:确诊|诊断为).{0,16}(?:障碍|疾病|焦虑症|抑郁症|社交焦虑)"
        ),
    ),
    (
        OutputViolationCategory.TREATMENT_PROMISE,
        re.compile(r"(?:一定|保证|肯定).{0,16}(?:治好|康复|有效|好起来|消除焦虑)"),
    ),
    (
        OutputViolationCategory.DEPENDENCY_ENCOURAGEMENT,
        re.compile(r"只(?:需要)?(?:和|跟)(?:我|系统|agent)聊|只能依靠(?:我|系统|agent)", re.I),
    ),
    (
        OutputViolationCategory.REAL_SUPPORT_DISCOURAGEMENT,
        re.compile(
            r"(?:不要|不必|无需).{0,12}(?:告诉|联系|寻求).{0,18}"
            r"(?:家人|朋友|老师|辅导员|心理中心|现实支持)"
        ),
    ),
    (
        OutputViolationCategory.COERCIVE_PRACTICE,
        re.compile(
            r"(?:不能|不许|不要)暂停|"
            r"必须.{0,18}(?:最高难度|高强度|立刻完成|坚持到底)"
        ),
    ),
    (
        OutputViolationCategory.FABRICATED_CONTACT,
        re.compile(r"(?<!\d)(?:\+?86[\s-]?)?1[3-9]\d[\s-]?\d{4}[\s-]?\d{4}(?!\d)"),
    ),
)

_SOFT_FACTUAL_CATEGORIES = frozenset(
    {OutputViolationCategory.INVENTED_USER_FACT}
)


class OutputGuardrail:
    """Check every final SkillResult and let backend policy own enforcement."""

    def __init__(self, llm_client: BaseLLMClient | None = None) -> None:
        self.llm_client = llm_client

    async def evaluate(
        self,
        *,
        user_message: str,
        response: str,
        intent: Intent,
        risk_level: RiskLevel,
        selected_skill: str,
        selected_agent: str,
        grounding_metadata: GroundingMetadata | None = None,
        historical_user_messages: list[str] | None = None,
        _allow_repair: bool = True,
    ) -> OutputGuardrailResult:
        """Return a bounded output decision without exposing raw evidence in traces."""
        deterministic = _deterministic_categories(response)
        if _has_ungrounded_resource_claim(
            response=response,
            selected_skill=selected_skill,
            grounding_metadata=grounding_metadata,
        ) and OutputViolationCategory.FABRICATED_CONTACT not in deterministic:
            deterministic.append(OutputViolationCategory.FABRICATED_CONTACT)
        if deterministic:
            return _replacement_result(
                categories=deterministic,
                sources=["deterministic"],
                risk_level=risk_level,
                semantic_checked=False,
            )
        if self.llm_client is None:
            return OutputGuardrailResult(
                action=OutputGuardrailAction.ALLOW,
                response=response,
            )

        safe_message = redact_sensitive_identifiers(user_message)[0]
        safe_history = [
            redact_sensitive_identifiers(message)[0]
            for message in (historical_user_messages or [])[-32:]
        ]
        safe_response = redact_sensitive_identifiers(response)[0]
        classification_prompt = build_output_guardrail_user_prompt(
            user_message=safe_message,
            response=safe_response,
            intent=intent.value,
            risk_level=risk_level.value,
            selected_skill=selected_skill,
            selected_agent=selected_agent,
            grounding_metadata=(
                grounding_metadata.model_dump(mode="json")
                if grounding_metadata is not None
                else None
            ),
            historical_user_messages=safe_history,
        )
        assessment: SemanticOutputAssessment | None = None
        semantic_retry_attempted = False
        for attempt in range(2):
            try:
                raw = await self.llm_client.generate_text(
                    system_prompt=build_output_guardrail_system_prompt(),
                    user_prompt=classification_prompt,
                    temperature=0.0,
                )
            except Exception:
                return _semantic_failure_result(
                    response=response,
                    risk_level=risk_level,
                    error_type=SemanticCheckErrorType.PROVIDER_ERROR,
                    retry_attempted=semantic_retry_attempted,
                )
            try:
                assessment = _parse_semantic_assessment(raw)
                break
            except (json.JSONDecodeError, ValidationError, ValueError) as exc:
                (
                    error_type,
                    schema_error_code,
                    schema_error_field,
                ) = _semantic_parse_error(exc)
                if attempt == 0:
                    semantic_retry_attempted = True
                    classification_prompt = (
                        f"{classification_prompt}\n\n"
                        "The previous classifier response violated the required JSON contract. "
                        "Return only an object matching the requested schema, without commentary."
                    )
                    continue
                return _semantic_failure_result(
                    response=response,
                    risk_level=risk_level,
                    error_type=error_type,
                    schema_error_code=schema_error_code,
                    schema_error_field=schema_error_field,
                    retry_attempted=True,
                )
        if assessment is None:  # Defensive: both loop exits above return on failure.
            return _semantic_failure_result(
                response=response,
                risk_level=risk_level,
                error_type=SemanticCheckErrorType.INVALID_PAYLOAD,
                retry_attempted=semantic_retry_attempted,
            )

        semantic_violations = _validated_semantic_violations(
            assessment,
            checked_response=safe_response,
        )
        if assessment.violations and not semantic_violations:
            return _semantic_failure_result(
                response=response,
                risk_level=risk_level,
                error_type=SemanticCheckErrorType.INVALID_EVIDENCE,
                retry_attempted=semantic_retry_attempted,
            )
        semantic_categories = list(
            dict.fromkeys(violation.category for violation in semantic_violations)
        )
        if semantic_categories:
            if (
                _allow_repair
                and risk_level in {RiskLevel.LOW, RiskLevel.MEDIUM}
                and _categories_are_repairable(semantic_categories)
            ):
                return await self._attempt_repair(
                    user_message=safe_message,
                    response=safe_response,
                    intent=intent,
                    risk_level=risk_level,
                    selected_skill=selected_skill,
                    selected_agent=selected_agent,
                    grounding_metadata=grounding_metadata,
                    historical_user_messages=safe_history,
                    violations=semantic_violations,
                    semantic_retry_attempted=semantic_retry_attempted,
                )
            return _replacement_result(
                categories=semantic_categories,
                sources=["semantic"],
                risk_level=risk_level,
                semantic_checked=True,
                semantic_diagnostics=semantic_violations,
                semantic_retry_attempted=semantic_retry_attempted,
            )
        return OutputGuardrailResult(
            action=OutputGuardrailAction.ALLOW,
            response=response,
            semantic_checked=True,
            semantic_retry_attempted=semantic_retry_attempted,
        )

    async def _attempt_repair(
        self,
        *,
        user_message: str,
        response: str,
        intent: Intent,
        risk_level: RiskLevel,
        selected_skill: str,
        selected_agent: str,
        grounding_metadata: GroundingMetadata | None,
        historical_user_messages: list[str],
        violations: list[SemanticOutputViolation],
        semantic_retry_attempted: bool,
    ) -> OutputGuardrailResult:
        """Try one repair and require a second independent global check."""
        if self.llm_client is None:
            return _repair_failed_replacement(
                categories=[violation.category for violation in violations],
                violations=violations,
                risk_level=risk_level,
                semantic_retry_attempted=semantic_retry_attempted,
            )
        try:
            raw = await self.llm_client.generate_text(
                system_prompt=build_output_repair_system_prompt(),
                user_prompt=build_output_repair_user_prompt(
                    user_message=user_message,
                    response=response,
                    violations=[
                        violation.model_dump(mode="json") for violation in violations
                    ],
                    historical_user_messages=historical_user_messages,
                ),
                temperature=0.0,
            )
            repaired_response = _parse_repair_proposal(raw).repaired_response.strip()
        except Exception:
            return _repair_failed_replacement(
                categories=[violation.category for violation in violations],
                violations=violations,
                risk_level=risk_level,
                semantic_retry_attempted=semantic_retry_attempted,
            )

        recheck = await self.evaluate(
            user_message=user_message,
            response=repaired_response,
            intent=intent,
            risk_level=risk_level,
            selected_skill=selected_skill,
            selected_agent="output_guardrail_repair",
            grounding_metadata=grounding_metadata,
            historical_user_messages=historical_user_messages,
            _allow_repair=False,
        )
        if (
            recheck.action == OutputGuardrailAction.ALLOW
            and not recheck.semantic_check_failed
        ):
            return OutputGuardrailResult(
                action=OutputGuardrailAction.REPAIR,
                response=repaired_response,
                categories=list(
                    dict.fromkeys(violation.category for violation in violations)
                ),
                sources=["semantic", "repair", "repair_recheck"],
                semantic_checked=True,
                violation_tier=_boundary_tier(
                    [violation.category for violation in violations]
                ),
                semantic_retry_attempted=(
                    semantic_retry_attempted or recheck.semantic_retry_attempted
                ),
                repair_attempted=True,
                repair_succeeded=True,
                repair_recheck_action=recheck.action,
                semantic_diagnostics=violations,
            )
        return _repair_failed_replacement(
            categories=list(
                dict.fromkeys(
                    [violation.category for violation in violations]
                    + recheck.categories
                )
            ),
            violations=violations,
            risk_level=risk_level,
            semantic_check_failed=recheck.semantic_check_failed,
            semantic_error_type=recheck.semantic_error_type,
            semantic_schema_error_code=recheck.semantic_schema_error_code,
            semantic_schema_error_field=recheck.semantic_schema_error_field,
            semantic_retry_attempted=(
                semantic_retry_attempted or recheck.semantic_retry_attempted
            ),
            recheck_action=recheck.action,
        )


def create_output_guardrail() -> OutputGuardrail:
    """Create a global guardrail with opt-in semantic provider checks."""
    semantic_enabled = (
        os.getenv("OUTPUT_GUARDRAIL_LLM_ENABLED", "false").casefold() == "true"
    )
    return OutputGuardrail(
        llm_client=create_llm_client() if semantic_enabled else None,
    )


def _deterministic_categories(response: str) -> list[OutputViolationCategory]:
    categories: list[OutputViolationCategory] = []
    for category, pattern in _DETERMINISTIC_PATTERNS:
        if pattern.search(response.casefold()) and category not in categories:
            categories.append(category)
    return categories


def _has_ungrounded_resource_claim(
    *,
    response: str,
    selected_skill: str,
    grounding_metadata: GroundingMetadata | None,
) -> bool:
    """Reject affirmative resource output when the resource skill has no citations."""
    if selected_skill != "support_resource_rag_skill" or grounding_metadata is None:
        return False
    if grounding_metadata.citation_count > 0:
        return False
    unknown_markers = (
        "我不知道",
        "没有找到",
        "没有可靠",
        "无法确认",
        "不会编造",
        "当前知识库没有",
    )
    return not any(marker in response for marker in unknown_markers)


def _parse_semantic_assessment(raw: str) -> SemanticOutputAssessment:
    normalized = re.sub(
        r"^```(?:json)?\s*|\s*```$",
        "",
        raw.strip(),
        flags=re.IGNORECASE,
    )
    payload = json.loads(normalized)
    if not isinstance(payload, dict):
        raise ValueError("Output guardrail response must be a JSON object.")
    payload = _truncate_auxiliary_reasons(payload)
    return SemanticOutputAssessment.model_validate(payload)


def _parse_repair_proposal(raw: str) -> OutputRepairProposal:
    normalized = re.sub(
        r"^```(?:json)?\s*|\s*```$",
        "",
        raw.strip(),
        flags=re.IGNORECASE,
    )
    payload = json.loads(normalized)
    if not isinstance(payload, dict):
        raise ValueError("Output repair response must be a JSON object.")
    return OutputRepairProposal.model_validate(payload)


def _semantic_parse_error(
    error: json.JSONDecodeError | ValidationError | ValueError,
) -> tuple[
    SemanticCheckErrorType,
    SemanticSchemaErrorCode | None,
    str | None,
]:
    """Map parser details to stable codes without retaining model content."""
    if isinstance(error, json.JSONDecodeError):
        return SemanticCheckErrorType.INVALID_JSON, None, None
    if not isinstance(error, ValidationError):
        return SemanticCheckErrorType.INVALID_PAYLOAD, None, None

    validation_errors = error.errors(include_url=False, include_context=False)
    error_types = {str(item.get("type", "")) for item in validation_errors}
    locations = {tuple(item.get("loc", ())) for item in validation_errors}
    error_fields = {
        field
        for location in locations
        if (
            field := next(
                (part for part in reversed(location) if isinstance(part, str)),
                None,
            )
        )
        is not None
    }
    raw_error_field = sorted(error_fields)[0] if error_fields else None
    error_field = (
        raw_error_field
        if raw_error_field in {"violations", "category", "evidence", "reason"}
        else "unexpected_field" if raw_error_field is not None else None
    )
    if "missing" in error_types:
        code = SemanticSchemaErrorCode.MISSING_FIELD
    elif "extra_forbidden" in error_types:
        code = SemanticSchemaErrorCode.EXTRA_FIELD
    elif any(
        error_type == "enum" or error_type == "literal_error"
        for error_type in error_types
    ) and any(location and location[-1] == "category" for location in locations):
        code = SemanticSchemaErrorCode.INVALID_CATEGORY
    elif any(
        "too_long" in error_type
        or "too_short" in error_type
        or error_type.startswith("greater_than")
        or error_type.startswith("less_than")
        for error_type in error_types
    ):
        code = SemanticSchemaErrorCode.CONSTRAINT_VIOLATION
    elif any(
        error_type.endswith("_type")
        or error_type.endswith("_parsing")
        for error_type in error_types
    ):
        code = SemanticSchemaErrorCode.INVALID_TYPE
    else:
        code = SemanticSchemaErrorCode.OTHER
    return SemanticCheckErrorType.SCHEMA_VALIDATION, code, error_field


def _truncate_auxiliary_reasons(payload: dict[str, object]) -> dict[str, object]:
    """Bound non-policy explanation text without changing evidence or categories."""
    raw_violations = payload.get("violations")
    if not isinstance(raw_violations, list):
        return payload
    normalized: list[object] = []
    changed = False
    for item in raw_violations:
        if not isinstance(item, dict):
            normalized.append(item)
            continue
        reason = item.get("reason")
        if isinstance(reason, str) and len(reason) > 240:
            normalized.append({**item, "reason": reason[:240]})
            changed = True
        else:
            normalized.append(item)
    return {**payload, "violations": normalized} if changed else payload


def _categories_are_repairable(
    categories: list[OutputViolationCategory],
) -> bool:
    """Allow repair only for unsupported user facts, never severe boundaries."""
    return bool(categories) and set(categories) <= {
        OutputViolationCategory.INVENTED_USER_FACT
    }


def _boundary_tier(
    categories: list[OutputViolationCategory],
) -> OutputBoundaryTier | None:
    """Map detected categories to the strictest operational boundary tier."""
    if not categories:
        return None
    if set(categories) <= _SOFT_FACTUAL_CATEGORIES:
        return OutputBoundaryTier.SOFT_FACTUAL
    return OutputBoundaryTier.HARD_SAFETY


def _validated_semantic_violations(
    assessment: SemanticOutputAssessment,
    *,
    checked_response: str,
) -> list[SemanticOutputViolation]:
    violations: list[SemanticOutputViolation] = []
    for violation in assessment.violations:
        if violation.evidence not in checked_response:
            continue
        if violation not in violations:
            violations.append(violation)
    return violations


def _replacement_result(
    *,
    categories: list[OutputViolationCategory],
    sources: list[str],
    risk_level: RiskLevel,
    semantic_checked: bool,
    semantic_diagnostics: list[SemanticOutputViolation] | None = None,
    semantic_retry_attempted: bool = False,
) -> OutputGuardrailResult:
    if risk_level in {RiskLevel.HIGH, RiskLevel.CRISIS}:
        response = (
            "我先不继续生成这部分建议。请优先联系你信任的人、学校支持资源，"
            "或在存在紧急危险时联系当地紧急服务；你不需要独自处理，也可以先停下来。"
        )
    else:
        response = (
            "刚才的内容没有通过安全边界检查，所以我不继续展示它。"
            "我不能做诊断、保证结果或要求你依赖这里；如果你愿意，我们可以改成一个"
            "可暂停、低强度的社交表达练习。"
        )
    return OutputGuardrailResult(
        action=OutputGuardrailAction.REPLACE,
        response=response,
        categories=categories,
        sources=sources,
        semantic_checked=semantic_checked,
        semantic_retry_attempted=semantic_retry_attempted,
        violation_tier=_boundary_tier(categories),
        semantic_diagnostics=semantic_diagnostics or [],
    )


def _semantic_failure_result(
    *,
    response: str,
    risk_level: RiskLevel,
    error_type: SemanticCheckErrorType,
    schema_error_code: SemanticSchemaErrorCode | None = None,
    schema_error_field: str | None = None,
    retry_attempted: bool = False,
) -> OutputGuardrailResult:
    """Apply risk-tiered policy when the optional semantic provider is unavailable."""
    if risk_level == RiskLevel.LOW:
        return OutputGuardrailResult(
            action=OutputGuardrailAction.ALLOW,
            response=response,
            sources=["semantic_failure"],
            semantic_checked=True,
            semantic_check_failed=True,
            semantic_error_type=error_type,
            semantic_schema_error_code=schema_error_code,
            semantic_schema_error_field=schema_error_field,
            semantic_retry_attempted=retry_attempted,
        )
    if risk_level == RiskLevel.MEDIUM:
        return OutputGuardrailResult(
            action=OutputGuardrailAction.AUGMENT,
            response=(
                f"{response.rstrip()}\n\n"
                "你可以先暂停或把步骤调小；如果压力持续影响日常生活，"
                "可以考虑联系可信任的人或学校支持资源。"
            ),
            sources=["semantic_failure"],
            semantic_checked=True,
            semantic_check_failed=True,
            semantic_error_type=error_type,
            semantic_schema_error_code=schema_error_code,
            semantic_schema_error_field=schema_error_field,
            semantic_retry_attempted=retry_attempted,
        )
    return _replacement_result(
        categories=[],
        sources=["semantic_failure"],
        risk_level=risk_level,
        semantic_checked=True,
    ).model_copy(
        update={
            "semantic_check_failed": True,
            "semantic_error_type": error_type,
            "semantic_schema_error_code": schema_error_code,
            "semantic_schema_error_field": schema_error_field,
            "semantic_retry_attempted": retry_attempted,
        }
    )


def _repair_failed_replacement(
    *,
    categories: list[OutputViolationCategory],
    violations: list[SemanticOutputViolation],
    risk_level: RiskLevel,
    semantic_check_failed: bool = False,
    semantic_error_type: SemanticCheckErrorType | None = None,
    semantic_schema_error_code: SemanticSchemaErrorCode | None = None,
    semantic_schema_error_field: str | None = None,
    semantic_retry_attempted: bool = False,
    recheck_action: OutputGuardrailAction | None = None,
) -> OutputGuardrailResult:
    """Fail closed after one unsuccessful or unverifiable repair attempt."""
    return _replacement_result(
        categories=list(dict.fromkeys(categories)),
        sources=["semantic", "repair_failed"],
        risk_level=risk_level,
        semantic_checked=True,
        semantic_diagnostics=violations,
    ).model_copy(
        update={
            "semantic_check_failed": semantic_check_failed,
            "semantic_error_type": semantic_error_type,
            "semantic_schema_error_code": semantic_schema_error_code,
            "semantic_schema_error_field": semantic_schema_error_field,
            "semantic_retry_attempted": semantic_retry_attempted,
            "repair_attempted": True,
            "repair_succeeded": False,
            "repair_recheck_action": recheck_action,
        }
    )
