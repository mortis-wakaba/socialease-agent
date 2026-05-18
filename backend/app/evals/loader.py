"""JSONL dataset loader for deterministic evaluation cases."""

import json
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from app.evals.models import (
    IntentEvalCase,
    RagEvalCase,
    RoleplayFeedbackEvalCase,
    SafetyEvalCase,
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


def load_intent_cases() -> list[IntentEvalCase]:
    """Load intent-routing cases."""
    return load_jsonl(DATA_DIR / "intent.jsonl", IntentEvalCase)


def load_rag_cases() -> list[RagEvalCase]:
    """Load knowledge-retrieval cases."""
    return load_jsonl(DATA_DIR / "rag.jsonl", RagEvalCase)


def load_roleplay_feedback_cases() -> list[RoleplayFeedbackEvalCase]:
    """Load role-play feedback cases."""
    return load_jsonl(DATA_DIR / "roleplay_feedback.jsonl", RoleplayFeedbackEvalCase)


def load_worksheet_cases() -> list[WorksheetEvalCase]:
    """Load worksheet-extraction cases."""
    return load_jsonl(DATA_DIR / "worksheet.jsonl", WorksheetEvalCase)
