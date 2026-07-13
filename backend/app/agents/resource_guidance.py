"""Bounded model-driven loop for grounded resource and practice guidance."""

import json

from pydantic import ValidationError

from app.knowledge.service import KnowledgeService
from app.llm.base import BaseLLMClient
from app.llm.prompts import (
    build_resource_loop_system_prompt,
    build_resource_loop_user_prompt,
)
from app.llm.retry import ProviderError
from app.models_knowledge import Citation, KnowledgeBaseType
from app.models_llm import LLMUsage
from app.models_resource_loop import (
    ResourceGuidanceLoopResult,
    ResourceLoopAction,
    ResourceLoopDecision,
    ResourceLoopObservation,
    ResourceLoopStep,
    ResourceLoopStopReason,
)
from app.models_support import SupportQueryRequest
from app.services.support_resource_service import SupportResourceService


class ResourceGuidanceAgentLoop:
    """Let a model choose among bounded read-only retrieval tools."""

    def __init__(
        self,
        *,
        llm_client: BaseLLMClient | None,
        knowledge: KnowledgeService,
        fallback_service: SupportResourceService,
        max_steps: int = 3,
    ) -> None:
        self.llm_client = llm_client
        self.knowledge = knowledge
        self.fallback_service = fallback_service
        self.max_steps = max(1, min(max_steps, 3))

    async def run(self, query: str) -> ResourceGuidanceLoopResult:
        """Run at most three model decisions and return grounded output."""
        if self.llm_client is None:
            return await self._deterministic_result(
                query=query,
                stop_reason=ResourceLoopStopReason.LLM_DISABLED,
                steps=[],
                used_agent_loop=False,
                fallback_used=False,
                error_category=None,
            )

        observations: list[ResourceLoopObservation] = []
        steps: list[ResourceLoopStep] = []
        feedback: list[str] = []

        for step_number in range(1, self.max_steps + 1):
            try:
                raw_decision = await self.llm_client.generate_text(
                    system_prompt=build_resource_loop_system_prompt(max_steps=self.max_steps),
                    user_prompt=build_resource_loop_user_prompt(
                        query=query,
                        observations=[item.model_dump(mode="json") for item in observations],
                        feedback=feedback,
                        step_number=step_number,
                    ),
                    temperature=0.0,
                )
                decision = _parse_decision(raw_decision)
            except (ValueError, json.JSONDecodeError, ValidationError):
                return await self._deterministic_result(
                    query=query,
                    stop_reason=ResourceLoopStopReason.INVALID_MODEL_OUTPUT,
                    steps=steps,
                    used_agent_loop=True,
                    fallback_used=True,
                    error_category="INVALID_JSON",
                )
            except Exception as exc:
                category = (
                    exc.category.value
                    if isinstance(exc, ProviderError)
                    else "TRANSIENT_PROVIDER_ERROR"
                )
                return await self._deterministic_result(
                    query=query,
                    stop_reason=ResourceLoopStopReason.PROVIDER_ERROR,
                    steps=steps,
                    used_agent_loop=True,
                    fallback_used=True,
                    error_category=category,
                )

            if decision.action == ResourceLoopAction.FINISH:
                selected = _select_observations(observations, decision.observation_ids)
                if len(selected) != len(set(decision.observation_ids)):
                    feedback.append("finish_rejected: every observation id must exist")
                    steps.append(
                        _finish_step(
                            step_number=step_number,
                            decision=decision,
                            outcome="rejected_invalid_observation_ids",
                        )
                    )
                    continue
                if not any(
                    item.tool == ResourceLoopAction.SEARCH_SUPPORT_RESOURCES
                    for item in selected
                ):
                    feedback.append(
                        "finish_rejected: select at least one support-resource observation"
                    )
                    steps.append(
                        _finish_step(
                            step_number=step_number,
                            decision=decision,
                            outcome="rejected_missing_support_resource",
                        )
                    )
                    continue
                steps.append(
                    _finish_step(
                        step_number=step_number,
                        decision=decision,
                        outcome="finished",
                    )
                )
                return _compose_grounded_result(selected=selected, steps=steps)

            try:
                observation = self._execute_search(
                    observation_id=len(observations) + 1,
                    decision=decision,
                )
            except Exception:
                return await self._deterministic_result(
                    query=query,
                    stop_reason=ResourceLoopStopReason.TOOL_ERROR,
                    steps=steps,
                    used_agent_loop=True,
                    fallback_used=True,
                    error_category="TOOL_OR_SKILL_FAILURE",
                )
            observations.append(observation)
            steps.append(
                ResourceLoopStep(
                    step=step_number,
                    action=decision.action,
                    reason=decision.reason,
                    query=decision.query,
                    observation_id=observation.observation_id,
                    citation_count=len(observation.citations),
                    unknown=observation.unknown,
                    outcome="tool_completed",
                )
            )

        return await self._deterministic_result(
            query=query,
            stop_reason=ResourceLoopStopReason.MAX_STEPS,
            steps=steps,
            used_agent_loop=True,
            fallback_used=True,
            error_category="MAX_STEPS",
        )

    def _execute_search(
        self,
        *,
        observation_id: int,
        decision: ResourceLoopDecision,
    ) -> ResourceLoopObservation:
        """Execute one allow-listed read-only retrieval action."""
        if decision.action == ResourceLoopAction.SEARCH_SUPPORT_RESOURCES:
            kb_type = KnowledgeBaseType.SUPPORT_RESOURCES
        elif decision.action == ResourceLoopAction.SEARCH_PRACTICE_GUIDANCE:
            kb_type = KnowledgeBaseType.SOCIAL_SKILLS
        else:
            raise ValueError("Finish is not an executable retrieval tool.")
        if decision.query is None:
            raise ValueError("Validated search decision did not include a query.")
        response = self.knowledge.query(query=decision.query, kb_type=kb_type)
        return ResourceLoopObservation(
            observation_id=observation_id,
            tool=decision.action,
            query=decision.query,
            answer=response.answer,
            citations=response.citations,
            unknown=response.unknown,
            confidence=response.confidence,
        )

    async def _deterministic_result(
        self,
        *,
        query: str,
        stop_reason: ResourceLoopStopReason,
        steps: list[ResourceLoopStep],
        used_agent_loop: bool,
        fallback_used: bool,
        error_category: str | None,
    ) -> ResourceGuidanceLoopResult:
        """Return the existing safe resource query as deterministic fallback."""
        response = await self.fallback_service.query_resources(
            SupportQueryRequest(query=query)
        )
        return ResourceGuidanceLoopResult(
            answer=response.answer,
            citations=response.citations,
            unknown=response.unknown,
            confidence=response.confidence,
            blocked=response.blocked,
            steps=steps,
            stop_reason=stop_reason,
            used_agent_loop=used_agent_loop,
            fallback_used=fallback_used,
            llm_usage=LLMUsage(
                used=used_agent_loop,
                fallback_used=fallback_used,
                error_category=error_category,
            ),
        )


def _parse_decision(raw_decision: str) -> ResourceLoopDecision:
    """Parse one strict JSON model decision."""
    payload = json.loads(raw_decision)
    if not isinstance(payload, dict):
        raise ValueError("Resource loop decision must be an object.")
    return ResourceLoopDecision.model_validate(payload)


def _select_observations(
    observations: list[ResourceLoopObservation],
    observation_ids: list[int],
) -> list[ResourceLoopObservation]:
    """Return selected observations once each in requested order."""
    by_id = {item.observation_id: item for item in observations}
    selected: list[ResourceLoopObservation] = []
    for observation_id in observation_ids:
        item = by_id.get(observation_id)
        if item is not None and item not in selected:
            selected.append(item)
    return selected


def _finish_step(
    *,
    step_number: int,
    decision: ResourceLoopDecision,
    outcome: str,
) -> ResourceLoopStep:
    """Return a trace-safe finish decision summary."""
    return ResourceLoopStep(
        step=step_number,
        action=decision.action,
        reason=decision.reason,
        selected_observation_ids=decision.observation_ids,
        outcome=outcome,
    )


def _compose_grounded_result(
    *,
    selected: list[ResourceLoopObservation],
    steps: list[ResourceLoopStep],
) -> ResourceGuidanceLoopResult:
    """Compose an answer only from selected retrieval observations."""
    answer_parts: list[str] = []
    citations: list[Citation] = []
    citation_keys: set[tuple[str, str, str | None, str]] = set()
    for observation in selected:
        label = (
            "公开支持资源"
            if observation.tool == ResourceLoopAction.SEARCH_SUPPORT_RESOURCES
            else "社交练习指导"
        )
        answer_parts.append(f"{label}：{observation.answer}")
        for citation in observation.citations:
            key = (
                citation.title,
                citation.source_name,
                citation.source_url,
                citation.snippet,
            )
            if key not in citation_keys:
                citation_keys.add(key)
                citations.append(citation)
    return ResourceGuidanceLoopResult(
        answer="\n\n".join(answer_parts),
        citations=citations,
        unknown=all(item.unknown for item in selected),
        confidence=min((item.confidence for item in selected), default=0.0),
        blocked=False,
        steps=steps,
        stop_reason=ResourceLoopStopReason.FINISHED,
        used_agent_loop=True,
        fallback_used=False,
        llm_usage=LLMUsage(used=True),
    )
