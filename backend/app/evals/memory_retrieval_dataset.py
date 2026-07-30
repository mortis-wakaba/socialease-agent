"""Clean dataset assembly for episodic-memory retrieval evaluations."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.evals.loader import (
    load_memory_retrieval_cases,
    load_memory_retrieval_v2_heldout_cases,
    load_memory_vector_challenge_cases,
)
from app.evals.memory_retrieval import record_from_fixture
from app.evals.memory_retrieval_scale import (
    build_scale_background_memories,
    build_scale_retrieval_cases,
)
from app.evals.models import MemoryRetrievalEvalCase
from app.models_long_term_memory import EpisodicMemoryRecord


class MemoryRetrievalEvalSplit(str, Enum):
    """Non-overlapping roles for retrieval evaluation cases."""

    DEVELOPMENT = "development"
    SCALE = "scale"
    HELD_OUT = "held_out"


@dataclass(frozen=True)
class MemoryRetrievalDataset:
    """Cases, split membership and case-scoped candidate corpora."""

    splits: dict[MemoryRetrievalEvalSplit, list[MemoryRetrievalEvalCase]]
    records_by_case: dict[str, list[EpisodicMemoryRecord]]

    @property
    def cases(self) -> list[MemoryRetrievalEvalCase]:
        """Return cases in stable development, scale and held-out order."""
        return [
            case
            for split in MemoryRetrievalEvalSplit
            for case in self.splits.get(split, [])
        ]

    @property
    def unique_records(self) -> set[tuple[str, str]]:
        """Return conceptual records, deduplicating shared scale corpora."""
        return {
            (record.user_id, record.memory_id)
            for records in self.records_by_case.values()
            for record in records
        }

    @property
    def unique_summaries(self) -> set[str]:
        """Return texts requiring distinct document embeddings."""
        return {
            record.summary
            for records in self.records_by_case.values()
            for record in records
        }

    @property
    def max_candidates_per_query(self) -> int:
        """Return the largest raw candidate corpus presented to one case."""
        return max(map(len, self.records_by_case.values()), default=0)


def build_default_memory_retrieval_dataset(
    *,
    include_scale_background: bool = True,
    include_held_out: bool = True,
) -> MemoryRetrievalDataset:
    """Build isolated functional cases plus one shared long-history corpus."""
    splits = {
        MemoryRetrievalEvalSplit.DEVELOPMENT: [
            *load_memory_retrieval_cases(),
            *load_memory_vector_challenge_cases(),
        ],
        MemoryRetrievalEvalSplit.SCALE: build_scale_retrieval_cases(),
        MemoryRetrievalEvalSplit.HELD_OUT: (
            load_memory_retrieval_v2_heldout_cases()
            if include_held_out
            else []
        ),
    }
    validate_memory_retrieval_splits(splits)
    return MemoryRetrievalDataset(
        splits=splits,
        records_by_case=build_memory_retrieval_case_corpora(
            splits,
            include_scale_background=include_scale_background,
        ),
    )


def build_custom_memory_retrieval_dataset(
    cases: list[MemoryRetrievalEvalCase],
) -> MemoryRetrievalDataset:
    """Build an isolated development-only dataset for contract tests."""
    splits = {
        MemoryRetrievalEvalSplit.DEVELOPMENT: list(cases),
        MemoryRetrievalEvalSplit.SCALE: [],
        MemoryRetrievalEvalSplit.HELD_OUT: [],
    }
    validate_memory_retrieval_splits(splits)
    return MemoryRetrievalDataset(
        splits=splits,
        records_by_case={
            case.id: [record_from_fixture(item) for item in case.memories]
            for case in cases
        },
    )


def build_memory_retrieval_case_corpora(
    splits: dict[MemoryRetrievalEvalSplit, list[MemoryRetrievalEvalCase]],
    *,
    include_scale_background: bool,
) -> dict[str, list[EpisodicMemoryRecord]]:
    """Keep functional cases isolated and share only the scale-user corpus."""
    records_by_case = {
        case.id: [record_from_fixture(item) for item in case.memories]
        for split, cases in splits.items()
        if split != MemoryRetrievalEvalSplit.SCALE
        for case in cases
    }
    scale_cases = splits.get(MemoryRetrievalEvalSplit.SCALE, [])
    scale_records_by_user: dict[str, dict[str, EpisodicMemoryRecord]] = {}
    for case in scale_cases:
        user_records = scale_records_by_user.setdefault(case.user_id, {})
        for item in case.memories:
            record = record_from_fixture(item)
            existing = user_records.get(record.memory_id)
            if existing is not None and existing != record:
                raise ValueError(
                    f"scale memory id has conflicting records: {record.memory_id}"
                )
            user_records[record.memory_id] = record
    if include_scale_background:
        for user_id, user_records in scale_records_by_user.items():
            for item in build_scale_background_memories(user_id=user_id):
                record = record_from_fixture(item)
                if record.memory_id in user_records:
                    raise ValueError(
                        f"scale background id collides with fixture: {record.memory_id}"
                    )
                user_records[record.memory_id] = record
    for case in scale_cases:
        records_by_case[case.id] = list(
            scale_records_by_user.get(case.user_id, {}).values()
        )
    return records_by_case


def validate_memory_retrieval_splits(
    splits: dict[MemoryRetrievalEvalSplit, list[MemoryRetrievalEvalCase]],
) -> None:
    """Reject exact case, query or fixture-text leakage across dataset splits."""
    for split, cases in splits.items():
        case_ids = [case.id for case in cases]
        queries = [case.query for case in cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError(f"case ids must be unique within {split.value}")
        if len(queries) != len(set(queries)):
            raise ValueError(f"queries must be unique within {split.value}")
    for left_index, left_split in enumerate(MemoryRetrievalEvalSplit):
        left_cases = splits.get(left_split, [])
        for right_split in list(MemoryRetrievalEvalSplit)[left_index + 1 :]:
            right_cases = splits.get(right_split, [])
            overlaps = {
                "case ids": {case.id for case in left_cases}.intersection(
                    case.id for case in right_cases
                ),
                "queries": {case.query for case in left_cases}.intersection(
                    case.query for case in right_cases
                ),
                "fixture summaries": {
                    memory.summary for case in left_cases for memory in case.memories
                }.intersection(
                    memory.summary
                    for case in right_cases
                    for memory in case.memories
                ),
            }
            for label, values in overlaps.items():
                if values:
                    raise ValueError(
                        f"{label} overlap between {left_split.value} and "
                        f"{right_split.value}: {sorted(values)!r}"
                    )
