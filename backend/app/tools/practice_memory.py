"""Registered search and conservative update Tools for practice memory."""

from __future__ import annotations

from typing import Protocol, cast

from pydantic import BaseModel, ConfigDict, Field

from app.models import RiskLevel
from app.models_memory_correction import MemoryCorrectionAutoUpdateResult
from app.models_memory_tool import MemoryToolRunResult
from app.tools.runtime import ToolExecutionContext, ToolRegistry


SEARCH_PRACTICE_MEMORY_TOOL_NAME = "search_practice_memory"
UPDATE_PRACTICE_MEMORY_TOOL_NAME = "update_practice_memory"


class SearchPracticeMemoryArguments(BaseModel):
    """The only model-controlled input accepted by the memory search Tool."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    query: str = Field(min_length=2, max_length=600)


class UpdatePracticeMemoryArguments(BaseModel):
    """No model-owned fields: the Tool consumes the current user request."""

    model_config = ConfigDict(extra="forbid")


class PracticeMemorySearchService(Protocol):
    """Server-owned search boundary wrapped by the registered Tool."""

    async def search(
        self,
        *,
        user_id: str,
        query: str,
        current_request: str,
        skill_name: str,
        risk_level: RiskLevel,
    ) -> MemoryToolRunResult: ...


class PracticeMemoryUpdateService(Protocol):
    """Server-owned conservative correction boundary wrapped by the Tool."""

    async def update(
        self,
        *,
        user_id: str,
        statement: str,
    ) -> MemoryCorrectionAutoUpdateResult: ...


class SearchPracticeMemoryTool:
    """Expose query-only memory search while keeping scope in application context."""

    name = SEARCH_PRACTICE_MEMORY_TOOL_NAME
    description = (
        "Search the current user's saved social-practice history when the request "
        "explicitly needs cross-session evidence."
    )
    input_model = SearchPracticeMemoryArguments

    def __init__(self, search_service: PracticeMemorySearchService) -> None:
        self._search_service = search_service

    async def execute(
        self,
        *,
        arguments: BaseModel,
        context: ToolExecutionContext,
    ) -> BaseModel:
        """Delegate only after registry validation and server-owned scoping."""
        validated = cast(SearchPracticeMemoryArguments, arguments)
        return await self._search_service.search(
            user_id=context.user_id,
            query=validated.query,
            current_request=context.current_request,
            skill_name=context.skill_name,
            risk_level=context.risk_level,
        )


class UpdatePracticeMemoryTool:
    """Apply an explicit correction only when the server resolves one safe target."""

    name = UPDATE_PRACTICE_MEMORY_TOOL_NAME
    description = (
        "Update one saved practice memory only when the user explicitly says a "
        "previously saved fact was wrong or has been replaced. The server may "
        "abstain when the target is missing, weak, or ambiguous."
    )
    input_model = UpdatePracticeMemoryArguments

    def __init__(self, update_service: PracticeMemoryUpdateService) -> None:
        self._update_service = update_service

    async def execute(
        self,
        *,
        arguments: BaseModel,
        context: ToolExecutionContext,
    ) -> BaseModel:
        """Use only application-owned request text and owner scope."""
        UpdatePracticeMemoryArguments.model_validate(arguments)
        if context.risk_level in {RiskLevel.HIGH, RiskLevel.CRISIS}:
            return MemoryCorrectionAutoUpdateResult(status="blocked_risk")
        return await self._update_service.update(
            user_id=context.user_id,
            statement=context.current_request,
        )


def memory_tool_registry(
    search_service: PracticeMemorySearchService,
    *,
    update_service: PracticeMemoryUpdateService | None = None,
) -> ToolRegistry:
    """Build the current registry without coupling callers to the adapter class."""
    tools = [SearchPracticeMemoryTool(search_service)]
    if update_service is not None:
        tools.append(UpdatePracticeMemoryTool(update_service))
    return ToolRegistry(tools)
