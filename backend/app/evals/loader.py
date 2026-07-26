"""JSONL dataset loader for deterministic evaluation cases."""

import json
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from app.evals.models import (
    E2EWorkflowEvalCase,
    IntentEvalCase,
    MemoryRetrievalEvalCase,
    OutputGuardrailEvalCase,
    ProductBoundaryEvalCase,
    RagEvalCase,
    RoleplayFeedbackEvalCase,
    SafetyEvalCase,
    SafetyRedTeamEvalCase,
    WorksheetEvalCase,
)

ModelT = TypeVar("ModelT", bound=BaseModel)
DATA_DIR = Path(__file__).with_name("data")


def load_jsonl(path: Path, model: type[ModelT]) -> list[ModelT]:
    """Load non-empty JSONL lines into validated Pydantic models."""
    cases: list[ModelT] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            cases.append(model.model_validate(json.loads(line)))
    return cases


def load_safety_cases() -> list[SafetyEvalCase]:
    """Load safety-classification cases."""
    return load_jsonl(DATA_DIR / "safety.jsonl", SafetyEvalCase)


def load_safety_red_team_cases() -> list[SafetyRedTeamEvalCase]:
    """Load conservative safety red-team cases."""
    return load_jsonl(DATA_DIR / "safety_red_team.jsonl", SafetyRedTeamEvalCase)


def load_intent_cases() -> list[IntentEvalCase]:
    """Load intent-routing cases."""
    return load_jsonl(DATA_DIR / "intent.jsonl", IntentEvalCase)


def load_rag_cases() -> list[RagEvalCase]:
    """Load knowledge-retrieval cases."""
    return load_jsonl(DATA_DIR / "rag.jsonl", RagEvalCase)


def load_memory_retrieval_cases() -> list[MemoryRetrievalEvalCase]:
    """Load fixed Chinese episodic-memory retrieval cases."""
    return load_jsonl(
        DATA_DIR / "memory_retrieval.jsonl",
        MemoryRetrievalEvalCase,
    )


def load_memory_vector_challenge_cases() -> list[MemoryRetrievalEvalCase]:
    """Load semantic hard-negative cases for optional dense retrieval evals."""
    return load_jsonl(
        DATA_DIR / "memory_retrieval_vector.jsonl",
        MemoryRetrievalEvalCase,
    )


def load_roleplay_feedback_cases() -> list[RoleplayFeedbackEvalCase]:
    """Load role-play feedback cases."""
    return load_jsonl(DATA_DIR / "roleplay_feedback.jsonl", RoleplayFeedbackEvalCase)


def load_worksheet_cases() -> list[WorksheetEvalCase]:
    """Load worksheet-extraction cases."""
    return load_jsonl(DATA_DIR / "worksheet.jsonl", WorksheetEvalCase)


def load_e2e_workflow_cases() -> list[E2EWorkflowEvalCase]:
    """Load end-to-end harness workflow cases."""
    return load_jsonl(DATA_DIR / "e2e_workflow.jsonl", E2EWorkflowEvalCase)


def load_product_boundary_cases() -> list[ProductBoundaryEvalCase]:
    """Load product-boundary eval cases."""
    return [
        *load_jsonl(DATA_DIR / "product_boundaries.jsonl", ProductBoundaryEvalCase),
        *load_jsonl(DATA_DIR / "product_boundaries_phase6.jsonl", ProductBoundaryEvalCase),
    ]


def load_output_guardrail_cases() -> list[OutputGuardrailEvalCase]:
    """Load demo-only global output policy cases."""
    return load_jsonl(
        DATA_DIR / "output_guardrail_cases.jsonl",
        OutputGuardrailEvalCase,
    )
