"""Server-resolved candidates for explicit natural-language memory correction."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from app.agents.memory_correction import (
    MemoryCorrectionExtractionError,
    MemoryCorrectionExtractor,
)
from app.db.factory import repository_factory
from app.llm.factory import create_llm_client
from app.memory.long_term_repository import LongTermMemoryRepository
from app.memory.long_term_repository import (
    InvalidMemoryTransitionError,
    MemoryConflictError,
    MemoryNotFoundError,
)
from app.memory.policy_engine import MemoryPolicyEngine
from app.memory.settings_store import UserMemorySettingsRepository
from app.memory.text_semantics import fts_query_terms
from app.models import RiskLevel
from app.models_long_term_memory import (
    MemoryEvidenceType,
    MemoryProposal,
    MemoryRecordStatus,
    MemorySourceType,
    MemoryType,
)
from app.models_memory_center import (
    MemoryCorrectionCandidateView,
    MemoryCorrectionResolutionResponse,
)
from app.models_memory_correction import (
    MEMORY_CORRECTION_OUTPUT_PARSER_VERSION,
    MemoryCorrectionAutoUpdateResult,
)


class MemorySupersessionService(Protocol):
    """Minimal mutation boundary required by autonomous correction."""

    async def supersede(
        self,
        *,
        user_id: str,
        memory_id: str,
        summary: str,
        expected_version: int,
    ) -> object: ...


@dataclass(frozen=True)
class _ResolvedCorrection:
    """Internal resolution data that is never returned by the preview API."""

    response: MemoryCorrectionResolutionResponse
    target_query: str | None = None


class MemoryCorrectionService:
    """Resolve bounded owner candidates without granting the model target authority."""

    def __init__(
        self,
        *,
        extractor: MemoryCorrectionExtractor | None = None,
        memory_repository: LongTermMemoryRepository | None = None,
        settings_repository: UserMemorySettingsRepository | None = None,
        policy_engine: MemoryPolicyEngine | None = None,
    ) -> None:
        factory = (
            repository_factory()
            if memory_repository is None or settings_repository is None
            else None
        )
        self._extractor = extractor or MemoryCorrectionExtractor(create_llm_client())
        if memory_repository is None:
            assert factory is not None
            memory_repository = factory.long_term_memory_repository()
        if settings_repository is None:
            assert factory is not None
            settings_repository = factory.user_memory_settings_repository()
        self._memory_repository = memory_repository
        self._settings_repository = settings_repository
        self._policy_engine = policy_engine or MemoryPolicyEngine()

    async def resolve(
        self,
        *,
        user_id: str,
        statement: str,
    ) -> MemoryCorrectionResolutionResponse:
        """Return up to three candidates; never perform a lifecycle mutation."""
        return (await self._resolve_with_query(user_id=user_id, statement=statement)).response

    async def auto_update(
        self,
        *,
        user_id: str,
        statement: str,
        supersession_service: MemorySupersessionService,
    ) -> MemoryCorrectionAutoUpdateResult:
        """Supersede only one uniquely retrieved, strongly overlapping target."""
        resolved = await self._resolve_with_query(
            user_id=user_id,
            statement=statement,
        )
        response = resolved.response
        if response.status != "confirmation_required":
            return MemoryCorrectionAutoUpdateResult(status=response.status)
        supported_candidates = [
            candidate
            for candidate in response.candidates
            if conservative_target_is_unambiguous(
                target_query=resolved.target_query or "",
                candidate_summary=candidate.current_summary,
            )
        ]
        if not supported_candidates:
            return MemoryCorrectionAutoUpdateResult(status="weak_target")
        if len(supported_candidates) != 1:
            return MemoryCorrectionAutoUpdateResult(status="ambiguous_target")
        candidate = supported_candidates[0]
        try:
            await supersession_service.supersede(
                user_id=user_id,
                memory_id=candidate.memory_id,
                summary=candidate.replacement_summary,
                expected_version=candidate.expected_version,
            )
        except (MemoryConflictError, MemoryNotFoundError, InvalidMemoryTransitionError):
            return MemoryCorrectionAutoUpdateResult(status="concurrent_change")
        except ValueError:
            return MemoryCorrectionAutoUpdateResult(status="rejected")
        return MemoryCorrectionAutoUpdateResult(
            status="updated",
            memory_type=candidate.memory_type,
        )

    async def _resolve_with_query(
        self,
        *,
        user_id: str,
        statement: str,
    ) -> _ResolvedCorrection:
        """Resolve preview candidates while retaining the internal target query."""
        try:
            extracted = await self._extractor.extract(statement)
        except MemoryCorrectionExtractionError as error:
            return _ResolvedCorrection(
                response=self._response(
                    user_id=user_id,
                    status=(
                        "unavailable"
                        if error.error_category == "LLM_DISABLED"
                        else "resolution_failed"
                    ),
                    error_category=error.error_category,
                ),
            )
        if extracted.action == "skip":
            return _ResolvedCorrection(
                response=self._response(user_id=user_id, status="not_correction")
            )
        assert extracted.target_query is not None
        assert extracted.replacement_summary is not None
        timestamp = datetime.now(timezone.utc)
        settings = await self._settings_repository.get(user_id)
        validation = self._policy_engine.decide(
            MemoryProposal(
                memory_type=MemoryType.PRACTICE_EXPERIENCE,
                summary=extracted.replacement_summary,
                source_type=MemorySourceType.USER_CONFIRMED,
                evidence_type=MemoryEvidenceType.USER_CONFIRMED,
                confidence=1.0,
                occurred_at=timestamp,
            ),
            consent_state=settings.consent_state,
            risk_level=RiskLevel.LOW,
        )
        if validation.safe_summary is None:
            return _ResolvedCorrection(
                response=self._response(
                    user_id=user_id,
                    status="rejected",
                    error_category=validation.reason.value,
                ),
                target_query=extracted.target_query,
            )
        query_terms = fts_query_terms(extracted.target_query)
        if not query_terms:
            return _ResolvedCorrection(
                response=self._response(user_id=user_id, status="no_match"),
                target_query=extracted.target_query,
            )
        try:
            matches = await self._memory_repository.search_memory_fts_candidates(
                user_id=user_id,
                statuses=(MemoryRecordStatus.ACTIVE,),
                memory_types=tuple(MemoryType),
                query_terms=query_terms,
                now=timestamp,
                limit=3,
            )
        except Exception:
            return _ResolvedCorrection(
                response=self._response(
                    user_id=user_id,
                    status="resolution_failed",
                    error_category="MEMORY_CANDIDATE_SEARCH_ERROR",
                ),
                target_query=extracted.target_query,
            )
        candidates = [
            MemoryCorrectionCandidateView(
                memory_id=memory.memory_id,
                memory_type=memory.memory_type,
                current_summary=memory.summary,
                replacement_summary=validation.safe_summary,
                expected_version=memory.version,
            )
            for memory in matches[:3]
        ]
        return _ResolvedCorrection(
            response=self._response(
                user_id=user_id,
                status="confirmation_required" if candidates else "no_match",
                candidates=candidates,
            ),
            target_query=extracted.target_query,
        )

    @staticmethod
    def _response(
        *,
        user_id: str,
        status: str,
        candidates: list[MemoryCorrectionCandidateView] | None = None,
        error_category: str | None = None,
    ) -> MemoryCorrectionResolutionResponse:
        return MemoryCorrectionResolutionResponse(
            user_id=user_id,
            status=status,
            candidates=candidates or [],
            error_category=error_category,
            output_parser_version=MEMORY_CORRECTION_OUTPUT_PARSER_VERSION,
        )


def conservative_target_is_unambiguous(
    *,
    target_query: str,
    candidate_summary: str,
    minimum_overlap: int = 2,
    minimum_query_coverage: float = 0.5,
) -> bool:
    """Require deterministic lexical support before an autonomous mutation."""
    query_terms = set(fts_query_terms(target_query))
    if not query_terms:
        return False
    candidate_terms = set(fts_query_terms(candidate_summary))
    overlap = len(query_terms.intersection(candidate_terms))
    return (
        overlap >= max(1, minimum_overlap)
        and overlap / len(query_terms) >= minimum_query_coverage
    )


class ConservativeMemoryUpdateService:
    """Bind correction resolution to the existing atomic supersession service."""

    def __init__(
        self,
        *,
        correction_service: MemoryCorrectionService,
        supersession_service: MemorySupersessionService,
    ) -> None:
        self._correction_service = correction_service
        self._supersession_service = supersession_service

    async def update(
        self,
        *,
        user_id: str,
        statement: str,
    ) -> MemoryCorrectionAutoUpdateResult:
        """Run one conservative correction under server-owned authority."""
        return await self._correction_service.auto_update(
            user_id=user_id,
            statement=statement,
            supersession_service=self._supersession_service,
        )
