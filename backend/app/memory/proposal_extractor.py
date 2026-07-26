"""Strict LLM adapter that can only emit untrusted memory candidates."""

from datetime import datetime
import json

from pydantic import ValidationError

from app.llm.base import BaseLLMClient
from app.llm.prompts import (
    build_memory_extraction_system_prompt,
    build_memory_extraction_user_prompt,
)
from app.llm.retry import ProviderError
from app.models_long_term_memory import (
    EpisodicMemoryRecord,
    MemoryExtractionResponse,
    MemoryProposal,
    MemorySourceType,
)


class MemoryExtractionError(RuntimeError):
    """Safe, categorized extraction failure without raw provider output."""

    def __init__(self, error_category: str) -> None:
        super().__init__(error_category)
        self.error_category = error_category


class MemoryProposalExtractor:
    """Extract strict candidates without granting persistence authority."""

    _PROPOSAL_KEYS = {
        "operation",
        "memory_type",
        "summary",
        "source_type",
        "source_id",
        "evidence_type",
        "confidence",
        "occurred_at",
    }

    def __init__(self, llm_client: BaseLLMClient | None) -> None:
        self.llm_client = llm_client

    @property
    def enabled(self) -> bool:
        """Return whether a provider is configured."""
        return self.llm_client is not None

    async def extract(
        self,
        *,
        messages: list[dict[str, str]],
        source_type: MemorySourceType,
        source_id: str | None,
        occurred_at: datetime,
        existing_memories: list[EpisodicMemoryRecord],
    ) -> MemoryExtractionResponse:
        """Return validated candidates whose source scope matches the application."""
        if self.llm_client is None:
            raise MemoryExtractionError("LLM_DISABLED")
        try:
            raw_output = await self.llm_client.generate_text(
                system_prompt=build_memory_extraction_system_prompt(),
                user_prompt=build_memory_extraction_user_prompt(
                    messages=messages,
                    source_type=source_type.value,
                    source_id=source_id,
                    occurred_at=occurred_at.isoformat(),
                    existing_memories=[
                        {
                            "memory_type": memory.memory_type.value,
                            "summary": memory.summary,
                            "scenario_type": (
                                memory.scenario_type
                            ),
                            "status": memory.status.value,
                        }
                        for memory in existing_memories[:20]
                    ],
                ),
                temperature=0.0,
            )
            payload = json.loads(raw_output)
            _validate_raw_shape(payload, expected_keys=self._PROPOSAL_KEYS)
            response = MemoryExtractionResponse.model_validate(payload)
            _validate_application_scope(
                response.proposals,
                source_type=source_type,
                source_id=source_id,
                occurred_at=occurred_at,
            )
            return response
        except json.JSONDecodeError as error:
            raise MemoryExtractionError("INVALID_JSON") from error
        except ValidationError as error:
            raise MemoryExtractionError("SCHEMA_VALIDATION_ERROR") from error
        except ValueError as error:
            raise MemoryExtractionError("SCHEMA_VALIDATION_ERROR") from error
        except MemoryExtractionError:
            raise
        except Exception as error:
            category = (
                error.category.value
                if isinstance(error, ProviderError)
                else "TRANSIENT_PROVIDER_ERROR"
            )
            raise MemoryExtractionError(category) from error


def _validate_raw_shape(
    payload: object,
    *,
    expected_keys: set[str],
) -> None:
    """Reject model-added authority fields before Pydantic defaults apply."""
    if not isinstance(payload, dict) or set(payload) != {"proposals"}:
        raise ValueError("memory extraction must contain exactly proposals")
    proposals = payload["proposals"]
    if not isinstance(proposals, list):
        raise ValueError("memory extraction proposals must be a list")
    for proposal in proposals:
        if not isinstance(proposal, dict) or set(proposal) != expected_keys:
            raise ValueError("memory proposal fields do not match the exact schema")


def _validate_application_scope(
    proposals: list[MemoryProposal],
    *,
    source_type: MemorySourceType,
    source_id: str | None,
    occurred_at: datetime,
) -> None:
    """Prevent model output from changing source identity or occurrence time."""
    for proposal in proposals:
        if proposal.source_type != source_type or proposal.source_id != source_id:
            raise ValueError("memory proposal attempted to change application source")
        if proposal.occurred_at != occurred_at:
            raise ValueError("memory proposal attempted to change occurrence time")
