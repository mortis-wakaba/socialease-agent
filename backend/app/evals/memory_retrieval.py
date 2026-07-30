"""Deterministic A/B/C benchmark for episodic-memory retrieval."""

from datetime import datetime, timedelta, timezone
import hashlib
from time import perf_counter
from typing import Any

from app.evals.loader import load_memory_retrieval_cases
from app.evals.memory_retrieval_metrics import (
    build_memory_retrieval_strategy_report,
)
from app.evals.models import (
    MemoryRetrievalBenchmarkReport,
    MemoryRetrievalBenchmarkStrategy,
    MemoryRetrievalEvalCase,
    MemoryRetrievalFixture,
    MemoryRetrievalStrategyReport,
)
from app.memory.retriever import rank_memory_candidates
from app.memory.token_estimator import ConservativeTokenEstimator
from app.models_long_term_memory import (
    EpisodicMemoryRecord,
    MemoryEvidenceType,
    MemoryRecordStatus,
    MemoryRetrievalRequest,
    MemoryRetrievalStrategy,
    MemorySourceType,
)


EVAL_NOW = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)
EVAL_TOKEN_BUDGET = 256


def run_memory_retrieval_benchmark(
) -> tuple[MemoryRetrievalBenchmarkReport, list[dict[str, Any]]]:
    """Compare recent, metadata and SQL-text strategies on fixed Chinese cases."""
    cases = load_memory_retrieval_cases()
    strategy_reports: dict[str, MemoryRetrievalStrategyReport] = {}
    selected_outcomes: list[dict[str, Any]] = []
    for strategy in MemoryRetrievalStrategy:
        report, outcomes = evaluate_classical_strategy(
            cases,
            strategy=strategy,
        )
        strategy_reports[strategy.value] = report
        if strategy == MemoryRetrievalStrategy.SQL_TEXT:
            selected_outcomes = outcomes
    benchmark = MemoryRetrievalBenchmarkReport(
        selected_strategy=MemoryRetrievalBenchmarkStrategy.SQL_TEXT,
        strategies=strategy_reports,
        dataset_case_count=len(cases),
        vector_evaluated=False,
        hybrid_evaluated=False,
        vector_gate_met=False,
    )
    return benchmark, selected_outcomes


def evaluate_classical_strategy(
    cases: list[MemoryRetrievalEvalCase],
    *,
    strategy: MemoryRetrievalStrategy,
    records_by_case: dict[str, list[EpisodicMemoryRecord]] | None = None,
    candidate_window_limit: int | None = None,
    report_strategy: MemoryRetrievalBenchmarkStrategy | None = None,
) -> tuple[MemoryRetrievalStrategyReport, list[dict[str, Any]]]:
    estimator = ConservativeTokenEstimator()
    outcomes: list[dict[str, Any]] = []

    for case in cases:
        started = perf_counter()
        request = MemoryRetrievalRequest(
            user_id=case.user_id,
            query=case.query,
            allowed_memory_types=case.allowed_memory_types,
            scenario_type=case.scenario_type,
            scenario_id=case.scenario_id,
            practice_thread_id=case.practice_thread_id,
            skill_codes=case.skill_codes,
            include_archived=case.include_archived,
            strategy=strategy,
        )
        candidates = (
            records_by_case[case.id]
            if records_by_case is not None
            else [record_from_fixture(item) for item in case.memories]
        )
        if candidate_window_limit is not None:
            candidates = _repository_candidate_window(
                candidates,
                request=request,
                limit=candidate_window_limit,
            )
        ranking_started = perf_counter()
        hits, eligible_count = rank_memory_candidates(
            request=request,
            candidates=candidates,
            now=EVAL_NOW,
            token_estimator=estimator,
            token_budget=EVAL_TOKEN_BUDGET,
        )
        ranking_latency_ms = (perf_counter() - ranking_started) * 1000
        query_latency_ms = (perf_counter() - started) * 1000
        retrieved_ids = [hit.memory_id for hit in hits]
        forbidden_clear = not set(retrieved_ids).intersection(
            case.forbidden_memory_ids
        )
        estimated_tokens = sum(hit.estimated_tokens for hit in hits)
        expected_ok = all(
            memory_id in retrieved_ids for memory_id in case.expected_memory_ids
        )
        abstain_ok = not case.expected_abstain or not retrieved_ids
        passed = expected_ok and forbidden_clear and abstain_ok
        outcomes.append(
            {
                "case_id": case.id,
                "category": case.category,
                "retrieved_ids": retrieved_ids,
                "expected_ids": case.expected_memory_ids,
                "forbidden_ids": case.forbidden_memory_ids,
                "expected_abstain": case.expected_abstain,
                "eligible_count": eligible_count,
                "estimated_tokens": estimated_tokens,
                "query_latency_ms": round(query_latency_ms, 6),
                "stage_latency_ms": {
                    "candidate_assembly": round(
                        max(query_latency_ms - ranking_latency_ms, 0.0),
                        6,
                    ),
                    "ranking_and_token_fit": round(ranking_latency_ms, 6),
                },
                "passed": passed,
            }
        )

    return (
        build_memory_retrieval_strategy_report(
            strategy=(
                report_strategy
                or MemoryRetrievalBenchmarkStrategy(strategy.value)
            ),
            cases=cases,
            outcomes=outcomes,
        ),
        outcomes,
    )


def _repository_candidate_window(
    candidates: list[EpisodicMemoryRecord],
    *,
    request: MemoryRetrievalRequest,
    limit: int,
) -> list[EpisodicMemoryRecord]:
    """Mirror the production SQL hard-filter and recent candidate window."""
    allowed_statuses = {MemoryRecordStatus.ACTIVE}
    if request.include_archived:
        allowed_statuses.add(MemoryRecordStatus.ARCHIVED)
    scoped = [
        record
        for record in candidates
        if (
            record.user_id == request.user_id
            and record.status in allowed_statuses
            and record.memory_type in request.allowed_memory_types
            and (
                record.expires_at is None
                or record.expires_at > EVAL_NOW
            )
        )
    ]
    scoped.sort(
        key=lambda record: (
            record.occurred_at,
            record.last_retrieved_at is None,
            record.memory_id,
        ),
        reverse=True,
    )
    return scoped[: max(1, limit)]


def record_from_fixture(
    fixture: MemoryRetrievalFixture,
) -> EpisodicMemoryRecord:
    occurred_at = EVAL_NOW - timedelta(days=fixture.occurred_days_ago)
    expires_at = (
        EVAL_NOW + timedelta(days=fixture.expires_days_from_now)
        if fixture.expires_days_from_now is not None
        else None
    )
    content_hash = hashlib.sha256(
        fixture.summary.encode("utf-8")
    ).hexdigest()
    idempotency_key = hashlib.sha256(
        f"{fixture.user_id}:{fixture.memory_id}".encode("utf-8")
    ).hexdigest()
    return EpisodicMemoryRecord(
        memory_id=fixture.memory_id,
        user_id=fixture.user_id,
        memory_type=fixture.memory_type,
        summary=fixture.summary,
        scenario_type=fixture.scenario_type,
        scenario_id=fixture.scenario_id,
        practice_thread_id=fixture.practice_thread_id,
        skill_codes=fixture.skill_codes,
        context_tags=fixture.context_tags,
        source_type=MemorySourceType.USER_CONFIRMED,
        source_id=fixture.source_id,
        evidence_type=MemoryEvidenceType.USER_CONFIRMED,
        confidence=fixture.confidence,
        status=fixture.status,
        occurred_at=occurred_at,
        created_at=occurred_at,
        updated_at=occurred_at,
        expires_at=expires_at,
        consent_version="practice-summary-v1",
        content_hash=content_hash,
        idempotency_key=idempotency_key,
    )
