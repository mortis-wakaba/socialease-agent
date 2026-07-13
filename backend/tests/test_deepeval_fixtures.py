"""Offline validation for synthetic fixtures used by optional DeepEval checks."""

import json
from pathlib import Path

from app.knowledge.service import KnowledgeService
from app.models_knowledge import KnowledgeBaseType


DATA_DIR = Path(__file__).resolve().parents[1] / "app" / "evals" / "data"


def _rows(name: str) -> list[dict[str, object]]:
    with (DATA_DIR / name).open(encoding="utf-8") as file:
        return [json.loads(line) for line in file]


def test_deepeval_fixtures_are_synthetic_demo_data() -> None:
    """Prevent optional judge suites from accidentally ingesting real traces."""
    rows = [
        *_rows("deepeval_resource.jsonl"),
        *_rows("deepeval_boundary.jsonl"),
        *_rows("deepeval_boundary_negative.jsonl"),
    ]
    assert rows
    assert all(row.get("demo") is True for row in rows)


def test_deepeval_resource_fixtures_have_retrieval_context() -> None:
    """Faithfulness requires at least one retrieved snippet per resource case."""
    knowledge = KnowledgeService()
    for row in _rows("deepeval_resource.jsonl"):
        response = knowledge.query(
            query=str(row["input"]),
            kb_type=KnowledgeBaseType(str(row["kb_type"])),
        )
        assert response.citations, row["id"]
        assert not response.unknown, row["id"]
