"""Privacy-safe progress events emitted by one Agent Harness run."""

from collections.abc import Callable
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class WorkflowStage(str, Enum):
    """Coarse workflow stages safe to expose to an authenticated client."""

    SAFETY = "safety"
    ROUTING = "routing"
    SKILL = "skill"
    OUTPUT_GUARDRAIL = "output_guardrail"
    TRACE = "trace"


class WorkflowProgressEvent(BaseModel):
    """One progress update without raw input, output, or classifier reasons."""

    type: Literal["run_started", "stage_completed"]
    run_id: str
    stage: WorkflowStage | None = None
    stage_latency_ms: float | None = Field(default=None, ge=0.0)
    elapsed_ms: float = Field(ge=0.0)


WorkflowEventSink = Callable[[WorkflowProgressEvent], None]

