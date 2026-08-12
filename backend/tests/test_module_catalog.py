"""Tests for the canonical conversation-module catalog."""

from datetime import UTC, datetime, timedelta

import pytest

from app.conversation.module_catalog import (
    build_module_parameters,
    module_memory_request_context,
    module_proposal_copy,
    module_proposal_reason,
    module_skill_name,
    module_start_message,
    module_type_for_intent,
)
from app.models import Intent
from app.models_conversation import (
    ModuleProposal,
    ModuleProposalReason,
    ModuleType,
)


@pytest.mark.parametrize(
    (
        "intent",
        "module_type",
        "skill_name",
        "reason",
        "label",
        "memory_context",
    ),
    [
        (
            Intent.ROLEPLAY_PRACTICE,
            ModuleType.ROLEPLAY,
            "roleplay_skill",
            ModuleProposalReason.EXPLICIT_PRACTICE_REQUEST,
            "角色扮演",
            {"scenario": "测试请求", "difficulty": 2},
        ),
        (
            Intent.CBT_WORKSHEET,
            ModuleType.WORKSHEET,
            "worksheet_skill",
            ModuleProposalReason.STRUCTURED_REFLECTION_MAY_HELP,
            "结构化想法记录",
            {},
        ),
        (
            Intent.EXPOSURE_PLANNING,
            ModuleType.EXPOSURE,
            "exposure_planning_skill",
            ModuleProposalReason.GRADED_PRACTICE_MAY_HELP,
            "分级社交练习",
            {"target_scenario": "测试请求"},
        ),
        (
            Intent.CAMPUS_RESOURCE_QUERY,
            ModuleType.RESOURCE,
            None,
            ModuleProposalReason.RESOURCE_LOOKUP_REQUESTED,
            "支持资源查询",
            {},
        ),
    ],
)
def test_module_catalog_keeps_routing_proposal_and_memory_metadata_aligned(
    intent: Intent,
    module_type: ModuleType,
    skill_name: str | None,
    reason: ModuleProposalReason,
    label: str,
    memory_context: dict[str, object],
) -> None:
    parameters = build_module_parameters(module_type, "测试请求")
    now = datetime.now(UTC)
    proposal = ModuleProposal(
        proposal_id=f"proposal-{module_type.value}",
        conversation_id="conversation-catalog",
        user_id="owner",
        proposed_module=module_type,
        reason_code=reason,
        bounded_parameters=parameters,
        request_hash="0" * 64,
        expires_at=now + timedelta(minutes=10),
        created_at=now,
    )

    assert module_type_for_intent(intent) is module_type
    assert module_skill_name(module_type) == skill_name
    assert module_proposal_reason(module_type) is reason
    assert label in module_proposal_copy(module_type)
    assert module_start_message(proposal) == "测试请求"
    assert module_memory_request_context(
        module_type,
        "fallback",
        parameters,
    ) == memory_context


def test_non_module_intent_has_no_catalog_route() -> None:
    assert module_type_for_intent(Intent.EMOTIONAL_SUPPORT) is None
