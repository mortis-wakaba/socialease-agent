"""Ablation benchmark for safe multi-route episodic-memory retrieval."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
import hashlib
import json
import os
from pathlib import Path
import sys
from time import perf_counter
from typing import Any

from app.evals.memory_retrieval_dataset import (
    MemoryRetrievalDataset,
    MemoryRetrievalEvalSplit,
    build_custom_memory_retrieval_dataset,
    build_default_memory_retrieval_dataset,
)
from app.evals.loader import load_memory_retrieval_sealed_cases
from app.evals.memory_retrieval import (
    EVAL_NOW,
    EVAL_TOKEN_BUDGET,
    evaluate_classical_strategy,
)
from app.evals.memory_retrieval_metrics import (
    build_memory_retrieval_strategy_report,
    filter_memory_retrieval_outcomes,
)
from app.evals.models import (
    MemoryRetrievalAblationReport,
    MemoryRetrievalBenchmarkStrategy,
    MemoryRetrievalEvalCase,
    MemoryRetrievalSplitReport,
    MemoryRetrievalStrategyReport,
)
from app.memory.abstention import MemoryAbstentionPolicy
from app.memory.recall import (
    DenseEmbeddingProvider,
    MemoryRecallChannel,
    MultiRouteMemoryRecall,
)
from app.memory.reranker import (
    CrossEncoderMemoryReranker,
    CrossEncoderProvider,
)
from app.memory.retriever import fit_memory_summary
from app.memory.token_estimator import ConservativeTokenEstimator
from app.models_long_term_memory import (
    EpisodicMemoryRecord,
    MemoryRetrievalRequest,
    MemoryRetrievalStrategy,
)


ABLATION_RECALL_GAIN_GATE = 0.10
ABLATION_P95_LATENCY_GATE_MS = 250.0
SQL_CANDIDATE_WINDOW = 100
RECALL_PER_CHANNEL_LIMIT = 20
DENSE_MIN_SCORE = 0.0
RRF_K = 60
RERANK_CANDIDATE_LIMIT = 20
ABSTENTION_MINIMUM_SCORE = 0.45
ABSTENTION_MINIMUM_CONFLICT_MARGIN = 0.03

_VARIANT_CHANNELS = {
    MemoryRetrievalBenchmarkStrategy.DENSE_ONLY: {
        MemoryRecallChannel.DENSE,
    },
    MemoryRetrievalBenchmarkStrategy.BM25_ONLY: {
        MemoryRecallChannel.BM25,
    },
    MemoryRetrievalBenchmarkStrategy.MULTI_ROUTE: {
        MemoryRecallChannel.DENSE,
        MemoryRecallChannel.BM25,
        MemoryRecallChannel.METADATA,
    },
    MemoryRetrievalBenchmarkStrategy.MULTI_QUERY: set(MemoryRecallChannel),
    MemoryRetrievalBenchmarkStrategy.CROSS_ENCODER: set(MemoryRecallChannel),
    MemoryRetrievalBenchmarkStrategy.FULL_PIPELINE: set(MemoryRecallChannel),
}


class _CachingEmbedder:
    """Avoid recomputing document embeddings across ablation variants."""

    def __init__(self, provider: DenseEmbeddingProvider) -> None:
        self.provider = provider
        self.provider_name = provider.provider_name
        self.model_name = provider.model_name
        self.model_revision = provider.model_revision
        self.dimensions = provider.dimensions
        self.model_size_mb = provider.model_size_mb
        self._query_cache: dict[str, list[float]] = {}
        self._document_cache: dict[str, list[float]] = {}

    def embed_queries(self, texts: Sequence[str]) -> list[list[float]]:
        return self._embed_cached(
            texts,
            cache=self._query_cache,
            method=self.provider.embed_queries,
        )

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return self._embed_cached(
            texts,
            cache=self._document_cache,
            method=self.provider.embed_documents,
        )

    def preload_documents(self, texts: Sequence[str]) -> None:
        """Build the document index outside measured query latency."""
        self.embed_documents(texts)

    def clear_query_cache(self) -> None:
        """Give every ablation variant the same uncached query workload."""
        self._query_cache.clear()

    @staticmethod
    def _embed_cached(
        texts: Sequence[str],
        *,
        cache: dict[str, list[float]],
        method: Callable[[Sequence[str]], list[list[float]]],
    ) -> list[list[float]]:
        missing = list(dict.fromkeys(text for text in texts if text not in cache))
        if missing:
            vectors = method(missing)
            if len(vectors) != len(missing):
                raise RuntimeError("Embedding provider returned an incomplete batch")
            cache.update(zip(missing, vectors, strict=True))
        return [cache[text] for text in texts]


def run_memory_retrieval_ablation(
    *,
    embedder: DenseEmbeddingProvider,
    reranker_provider: CrossEncoderProvider,
    cases: list[MemoryRetrievalEvalCase] | None = None,
    include_scale_background: bool = True,
    sealed_held_out_cases: list[MemoryRetrievalEvalCase] | None = None,
    postgres_fts_database_url: str | None = None,
    progress: Callable[[str], None] | None = None,
) -> tuple[MemoryRetrievalAblationReport, dict[str, list[dict[str, Any]]]]:
    """Compare each added retrieval component against the SQL Text baseline."""
    evaluation_started = perf_counter()
    stage_duration_ms: dict[str, float] = {}

    dataset_started = perf_counter()
    dataset = (
        build_custom_memory_retrieval_dataset(cases)
        if cases is not None
        else build_default_memory_retrieval_dataset(
            include_scale_background=include_scale_background,
            sealed_held_out_cases=sealed_held_out_cases,
        )
    )
    stage_duration_ms["dataset_build"] = _elapsed_ms(dataset_started)
    eval_cases = dataset.cases
    records_by_case = dataset.records_by_case
    validation_case_count = len(
        dataset.splits[MemoryRetrievalEvalSplit.VALIDATION]
    )
    held_out_case_count = len(
        dataset.splits[MemoryRetrievalEvalSplit.SEALED_HELD_OUT]
    )
    _emit_progress(
        progress,
        (
            f"dataset ready: {len(eval_cases)} cases, "
            f"{len(dataset.unique_records)} records, "
            f"{len(dataset.unique_summaries)} unique summaries"
        ),
    )

    baseline_started = perf_counter()
    _emit_progress(progress, "running SQL Text baseline")
    baseline, baseline_outcomes = evaluate_classical_strategy(
        eval_cases,
        strategy=MemoryRetrievalStrategy.SQL_TEXT,
        records_by_case=records_by_case,
        candidate_window_limit=(
            SQL_CANDIDATE_WINDOW if include_scale_background else None
        ),
        report_strategy=(
            MemoryRetrievalBenchmarkStrategy.SQL_RECENT_WINDOW_100
            if include_scale_background
            else MemoryRetrievalBenchmarkStrategy.SQL_TEXT
        ),
    )
    baseline_name = baseline.strategy.value
    stage_duration_ms[baseline_name] = _elapsed_ms(baseline_started)
    _emit_progress(
        progress,
        (
            f"{baseline_name} baseline complete in "
            f"{_seconds(stage_duration_ms[baseline_name])}"
        ),
    )
    reports = {baseline_name: baseline}
    outcomes = {baseline_name: baseline_outcomes}
    postgres_fts_load_latency_ms = 0.0
    postgres_fts_warmup_latency_ms = 0.0
    if postgres_fts_database_url:
        fts_started = perf_counter()
        _emit_progress(progress, "running real PostgreSQL FTS baseline")
        (
            postgres_fts_report,
            postgres_fts_outcomes,
            postgres_fts_load_latency_ms,
            postgres_fts_warmup_latency_ms,
        ) = asyncio.run(
            _run_postgres_fts_baseline(
                database_url=postgres_fts_database_url,
                dataset=dataset,
            )
        )
        reports[postgres_fts_report.strategy.value] = postgres_fts_report
        outcomes[postgres_fts_report.strategy.value] = postgres_fts_outcomes
        stage_duration_ms["postgres_fts"] = _elapsed_ms(fts_started)
        _emit_progress(
            progress,
            (
                "PostgreSQL FTS baseline complete in "
                f"{_seconds(stage_duration_ms['postgres_fts'])}"
            ),
        )
    cached_embedder = _CachingEmbedder(embedder)
    _emit_progress(
        progress,
        f"embedding {len(dataset.unique_summaries)} unique summaries",
    )
    document_started = perf_counter()
    cached_embedder.preload_documents(sorted(dataset.unique_summaries))
    document_embedding_latency_ms = _elapsed_ms(document_started)
    stage_duration_ms["document_embedding"] = document_embedding_latency_ms
    _emit_progress(
        progress,
        (
            "document embedding complete in "
            f"{_seconds(document_embedding_latency_ms)}"
        ),
    )

    reranker_warmup_started = perf_counter()
    _emit_progress(progress, "warming Cross-Encoder")
    _warm_reranker(reranker_provider)
    stage_duration_ms["reranker_warmup"] = _elapsed_ms(
        reranker_warmup_started
    )
    _emit_progress(
        progress,
        (
            "Cross-Encoder warmup complete in "
            f"{_seconds(stage_duration_ms['reranker_warmup'])}"
        ),
    )

    variant_count = len(_VARIANT_CHANNELS)
    for variant_index, (variant, channels) in enumerate(
        _VARIANT_CHANNELS.items(),
        start=1,
    ):
        _emit_progress(
            progress,
            f"running variant {variant_index}/{variant_count}: {variant.value}",
        )
        variant_started = perf_counter()
        cached_embedder.clear_query_cache()
        report, variant_outcomes = _evaluate_variant(
            eval_cases,
            records_by_case=records_by_case,
            embedder=cached_embedder,
            reranker_provider=reranker_provider,
            variant=variant,
            channels=channels,
        )
        reports[variant.value] = report
        outcomes[variant.value] = variant_outcomes
        stage_duration_ms[variant.value] = _elapsed_ms(variant_started)
        _emit_progress(
            progress,
            (
                f"variant {variant.value} complete in "
                f"{_seconds(stage_duration_ms[variant.value])}"
            ),
        )

    reporting_started = perf_counter()
    split_reports = _build_split_reports(
        dataset=dataset,
        reports=reports,
        outcomes=outcomes,
    )
    aggregate_full = reports[
        MemoryRetrievalBenchmarkStrategy.FULL_PIPELINE.value
    ]
    held_out_reports = split_reports[
        MemoryRetrievalEvalSplit.SEALED_HELD_OUT.value
    ]
    scale_reports = split_reports[MemoryRetrievalEvalSplit.SCALE.value]
    fts_name = MemoryRetrievalBenchmarkStrategy.POSTGRES_FTS.value
    postgres_fts_evaluated = fts_name in reports
    if (
        postgres_fts_evaluated
        and held_out_reports.case_count
        and scale_reports.case_count
    ):
        gate_met = passes_ablation_gate(
            candidate=held_out_reports.strategies[
                MemoryRetrievalBenchmarkStrategy.FULL_PIPELINE.value
            ],
            baseline=held_out_reports.strategies[
                fts_name
            ],
            recall_candidate=scale_reports.strategies[
                MemoryRetrievalBenchmarkStrategy.FULL_PIPELINE.value
            ],
            recall_baseline=scale_reports.strategies[
                fts_name
            ],
            latency_candidate=aggregate_full,
        )
    else:
        gate_met = False
    selected = (
        MemoryRetrievalBenchmarkStrategy.FULL_PIPELINE
        if gate_met
        else baseline.strategy
    )
    stage_duration_ms["reporting"] = _elapsed_ms(reporting_started)
    evaluation_duration_ms = _elapsed_ms(evaluation_started)
    report = MemoryRetrievalAblationReport(
        selected_strategy=selected,
        baseline_strategy=(
            MemoryRetrievalBenchmarkStrategy.POSTGRES_FTS
            if postgres_fts_evaluated
            else baseline.strategy
        ),
        strategies=reports,
        dataset_case_count=len(eval_cases),
        development_case_count=len(
            dataset.splits[MemoryRetrievalEvalSplit.DEVELOPMENT]
        ),
        scale_case_count=len(dataset.splits[MemoryRetrievalEvalSplit.SCALE]),
        validation_case_count=validation_case_count,
        held_out_case_count=held_out_case_count,
        indexed_memory_count=len(dataset.unique_records),
        unique_summary_count=len(dataset.unique_summaries),
        dataset_manifest_sha256=_dataset_manifest_sha256(dataset),
        max_candidates_per_query=dataset.max_candidates_per_query,
        document_embedding_latency_ms=round(
            document_embedding_latency_ms,
            6,
        ),
        postgres_fts_evaluated=postgres_fts_evaluated,
        postgres_fts_load_latency_ms=round(
            postgres_fts_load_latency_ms,
            6,
        ),
        postgres_fts_warmup_latency_ms=round(
            postgres_fts_warmup_latency_ms,
            6,
        ),
        evaluation_duration_ms=round(evaluation_duration_ms, 6),
        stage_duration_ms={
            name: round(duration_ms, 6)
            for name, duration_ms in stage_duration_ms.items()
        },
        embedding_provider=embedder.provider_name,
        embedding_model=embedder.model_name,
        embedding_model_revision=embedder.model_revision,
        embedding_dimensions=embedder.dimensions,
        reranker_provider=reranker_provider.provider_name,
        reranker_model=reranker_provider.model_name,
        reranker_model_revision=reranker_provider.model_revision,
        experiment_config={
            "sql_candidate_window": SQL_CANDIDATE_WINDOW,
            "postgres_fts_candidate_limit": 100,
            "postgres_fts_analyzer": "simple_cjk_bigram_v1",
            "recall_per_channel_limit": RECALL_PER_CHANNEL_LIMIT,
            "dense_min_score": DENSE_MIN_SCORE,
            "rrf_k": RRF_K,
            "query_variant_limit": 4,
            "rerank_candidate_limit": RERANK_CANDIDATE_LIMIT,
            "cross_encoder_weight": 0.60,
            "rrf_weight": 0.20,
            "dense_weight": 0.08,
            "bm25_weight": 0.06,
            "metadata_weight": 0.06,
            "abstention_minimum_score": ABSTENTION_MINIMUM_SCORE,
            "abstention_minimum_conflict_margin": (
                ABSTENTION_MINIMUM_CONFLICT_MARGIN
            ),
            "candidate_maximum_score_drop": 0.20,
            "context_token_budget": EVAL_TOKEN_BUDGET,
            "output_limit": 3,
        },
        splits=split_reports,
        adoption_gate_met=gate_met,
    )
    _emit_progress(
        progress,
        f"evaluation complete in {_seconds(evaluation_duration_ms)}",
    )
    return report, outcomes


def _elapsed_ms(started: float) -> float:
    """Return elapsed monotonic time in milliseconds."""
    return (perf_counter() - started) * 1000


async def _run_postgres_fts_baseline(
    *,
    database_url: str,
    dataset: MemoryRetrievalDataset,
) -> tuple[
    MemoryRetrievalStrategyReport,
    list[dict[str, Any]],
    float,
    float,
]:
    """Own one engine and event loop for the disposable PostgreSQL baseline."""
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.db.postgres.long_term_memory_repository import (
        PostgresLongTermMemoryRepository,
    )
    from app.db.postgres.memory_retrieval_eval import (
        PostgresMemoryRetrievalEvalAdapter,
    )
    from app.evals.postgres_memory_fts import evaluate_postgres_fts

    engine = create_async_engine(database_url, pool_pre_ping=True)
    try:
        return await evaluate_postgres_fts(
            dataset=dataset,
            repository=PostgresLongTermMemoryRepository(engine=engine),
            fixture_adapter=PostgresMemoryRetrievalEvalAdapter(engine=engine),
        )
    finally:
        await engine.dispose()


def _dataset_manifest_sha256(dataset: MemoryRetrievalDataset) -> str:
    """Fingerprint labels and candidate identities without exposing their text."""
    payload = {
        "splits": {
            split.value: [
                {
                    "id": case.id,
                    "expected": case.expected_memory_ids,
                    "forbidden": case.forbidden_memory_ids,
                    "expected_abstain": case.expected_abstain,
                }
                for case in dataset.splits[split]
            ]
            for split in MemoryRetrievalEvalSplit
        },
        "records": sorted(
            {
                (
                    record.user_id,
                    record.memory_id,
                    record.content_hash,
                    record.status.value,
                    record.version,
                )
                for records in dataset.records_by_case.values()
                for record in records
            }
        ),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _seconds(duration_ms: float) -> str:
    """Format milliseconds as a compact wall-clock duration."""
    return f"{duration_ms / 1000:.2f}s"


def _emit_progress(
    progress: Callable[[str], None] | None,
    message: str,
) -> None:
    """Emit progress only when a caller supplies a reporter."""
    if progress is not None:
        progress(message)


def _stderr_progress(message: str) -> None:
    """Write progress separately from the JSON result stream."""
    print(f"[memory-ablation] {message}", file=sys.stderr, flush=True)


def _evaluate_variant(
    cases: list[MemoryRetrievalEvalCase],
    *,
    records_by_case: dict[str, list[EpisodicMemoryRecord]],
    embedder: DenseEmbeddingProvider,
    reranker_provider: CrossEncoderProvider,
    variant: MemoryRetrievalBenchmarkStrategy,
    channels: set[MemoryRecallChannel],
) -> tuple[MemoryRetrievalStrategyReport, list[dict[str, Any]]]:
    recall = MultiRouteMemoryRecall(
        embedder=embedder,
        enabled_channels=channels,
        per_channel_limit=RECALL_PER_CHANNEL_LIMIT,
        dense_min_score=DENSE_MIN_SCORE,
        rrf_k=RRF_K,
    )
    reranker = CrossEncoderMemoryReranker(
        provider=reranker_provider,
        candidate_limit=RERANK_CANDIDATE_LIMIT,
    )
    abstention = MemoryAbstentionPolicy(
        minimum_score=ABSTENTION_MINIMUM_SCORE,
        minimum_conflict_margin=ABSTENTION_MINIMUM_CONFLICT_MARGIN,
    )
    estimator = ConservativeTokenEstimator()
    outcomes: list[dict[str, Any]] = []

    for case in cases:
        request = MemoryRetrievalRequest(
            user_id=case.user_id,
            query=case.query,
            allowed_memory_types=case.allowed_memory_types,
            scenario_type=case.scenario_type,
            scenario_id=case.scenario_id,
            practice_thread_id=case.practice_thread_id,
            skill_codes=case.skill_codes,
            include_archived=case.include_archived,
            strategy=MemoryRetrievalStrategy.SQL_TEXT,
        )
        started = perf_counter()
        recall_started = perf_counter()
        recalled = recall.recall(
            request=request,
            records=records_by_case[case.id],
            now=EVAL_NOW,
        )
        recall_latency_ms = _elapsed_ms(recall_started)
        rerank_latency_ms = 0.0
        selection_latency_ms = 0.0
        abstention_reason = None
        abstention_top_score = None
        abstention_score_margin = None
        rerank_input_count = 0
        if variant in {
            MemoryRetrievalBenchmarkStrategy.CROSS_ENCODER,
            MemoryRetrievalBenchmarkStrategy.FULL_PIPELINE,
        }:
            rerank_started = perf_counter()
            reranked = reranker.rerank(
                request=request,
                candidates=recalled.candidates,
                now=EVAL_NOW,
            )
            rerank_latency_ms = _elapsed_ms(rerank_started)
            rerank_input_count = reranked.diagnostics.reranked_candidate_count
            if variant == MemoryRetrievalBenchmarkStrategy.FULL_PIPELINE:
                selection_started = perf_counter()
                decision = abstention.decide(
                    request=request,
                    candidates=reranked.candidates,
                    now=EVAL_NOW,
                )
                selection_latency_ms = _elapsed_ms(selection_started)
                selected_records = [
                    item.recalled.record for item in decision.selected
                ]
                abstention_reason = decision.diagnostics.reason.value
                abstention_top_score = decision.diagnostics.top_score
                abstention_score_margin = decision.diagnostics.score_margin
            else:
                selected_records = [
                    item.recalled.record for item in reranked.candidates[:3]
                ]
        else:
            selected_records = [
                item.record for item in recalled.candidates[:3]
            ]
        token_fit_started = perf_counter()
        retrieved_ids, estimated_tokens = _fit_selected(
            selected_records,
            estimator=estimator,
        )
        token_fit_latency_ms = _elapsed_ms(token_fit_started)
        query_latency_ms = (perf_counter() - started) * 1000
        forbidden_clear = not set(retrieved_ids).intersection(
            case.forbidden_memory_ids
        )
        predicted_abstain = not retrieved_ids
        passed = (
            all(item in retrieved_ids for item in case.expected_memory_ids)
            and forbidden_clear
            and (not case.expected_abstain or predicted_abstain)
        )
        outcomes.append(
            {
                "case_id": case.id,
                "category": case.category,
                "retrieved_ids": retrieved_ids,
                "expected_ids": case.expected_memory_ids,
                "forbidden_ids": case.forbidden_memory_ids,
                "expected_abstain": case.expected_abstain,
                "abstention_reason": abstention_reason,
                "abstention_top_score": abstention_top_score,
                "abstention_score_margin": abstention_score_margin,
                "candidate_count": len(records_by_case[case.id]),
                "eligible_count": recalled.diagnostics.filtered.allowed_count,
                "union_count": recalled.diagnostics.union_count,
                "rerank_input_count": rerank_input_count,
                "expected_in_union": all(
                    memory_id
                    in {
                        item.record.memory_id
                        for item in recalled.candidates
                    }
                    for memory_id in case.expected_memory_ids
                ),
                "expected_union_ranks": _expected_ranks(
                    case.expected_memory_ids,
                    [item.record.memory_id for item in recalled.candidates],
                ),
                "expected_rerank_ranks": (
                    _expected_ranks(
                        case.expected_memory_ids,
                        [
                            item.recalled.record.memory_id
                            for item in reranked.candidates
                        ],
                    )
                    if variant
                    in {
                        MemoryRetrievalBenchmarkStrategy.CROSS_ENCODER,
                        MemoryRetrievalBenchmarkStrategy.FULL_PIPELINE,
                    }
                    else {}
                ),
                "estimated_tokens": estimated_tokens,
                "query_latency_ms": round(query_latency_ms, 6),
                "stage_latency_ms": {
                    "recall": round(recall_latency_ms, 6),
                    "rerank": round(rerank_latency_ms, 6),
                    "candidate_selection": round(selection_latency_ms, 6),
                    "token_fit": round(token_fit_latency_ms, 6),
                },
                "passed": passed,
            }
        )
    return (
        build_memory_retrieval_strategy_report(
            strategy=variant,
            cases=cases,
            outcomes=outcomes,
        ),
        outcomes,
    )


def passes_ablation_gate(
    *,
    candidate: MemoryRetrievalStrategyReport,
    baseline: MemoryRetrievalStrategyReport,
    recall_candidate: MemoryRetrievalStrategyReport | None = None,
    recall_baseline: MemoryRetrievalStrategyReport | None = None,
    latency_candidate: MemoryRetrievalStrategyReport | None = None,
) -> bool:
    """Require scale relevance plus held-out safety without split mixing."""
    quality = recall_candidate or candidate
    quality_baseline = recall_baseline or baseline
    latency = latency_candidate or candidate
    safety_metrics = (
        "false_recall_avoidance",
        "stale_recall_avoidance",
        "conflict_resolution",
        "cross_user_leakage_avoidance",
        "no_memory_abstention",
        "context_token_budget",
    )
    return (
        quality.relevant_recall_at_3.score
        >= quality_baseline.relevant_recall_at_3.score
        + ABLATION_RECALL_GAIN_GATE
        and all(getattr(candidate, name).score >= 1.0 for name in safety_metrics)
        and candidate.case_pass_rate.score >= baseline.case_pass_rate.score
        and latency.p95_query_latency_ms <= ABLATION_P95_LATENCY_GATE_MS
    )


def _build_split_reports(
    *,
    dataset: MemoryRetrievalDataset,
    reports: dict[str, MemoryRetrievalStrategyReport],
    outcomes: dict[str, list[dict[str, Any]]],
) -> dict[str, MemoryRetrievalSplitReport]:
    split_reports: dict[str, MemoryRetrievalSplitReport] = {}
    for split in MemoryRetrievalEvalSplit:
        split_cases = dataset.splits[split]
        strategy_reports = {
            strategy_name: build_memory_retrieval_strategy_report(
                strategy=report.strategy,
                cases=split_cases,
                outcomes=filter_memory_retrieval_outcomes(
                    cases=split_cases,
                    outcomes=outcomes[strategy_name],
                ),
            )
            for strategy_name, report in reports.items()
        }
        split_reports[split.value] = MemoryRetrievalSplitReport(
            case_count=len(split_cases),
            max_candidates_per_query=max(
                (
                    len(dataset.records_by_case[case.id])
                    for case in split_cases
                ),
                default=0,
            ),
            strategies=strategy_reports,
        )
    return split_reports


def _warm_reranker(provider: CrossEncoderProvider) -> None:
    """Exclude one-time ONNX graph initialization from warm latency metrics."""
    scores = provider.score(
        "demo warmup query",
        ["demo warmup memory summary"],
    )
    if len(scores) != 1:
        raise RuntimeError("Cross-Encoder warmup returned an incomplete batch")


def _fit_selected(
    records: list[EpisodicMemoryRecord],
    *,
    estimator: ConservativeTokenEstimator,
) -> tuple[list[str], int]:
    ids: list[str] = []
    used = 0
    for record in records[:3]:
        summary, cost = fit_memory_summary(
            record.summary,
            memory_type=record.memory_type.value,
            remaining_tokens=EVAL_TOKEN_BUDGET - used,
            estimator=estimator,
        )
        if summary is not None:
            ids.append(record.memory_id)
            used += cost
    return ids, used


def _expected_ranks(
    expected_ids: list[str],
    ranked_ids: list[str],
) -> dict[str, int | None]:
    """Return content-free expected-id ranks for stage failure attribution."""
    positions = {
        memory_id: index
        for index, memory_id in enumerate(ranked_ids, start=1)
    }
    return {memory_id: positions.get(memory_id) for memory_id in expected_ids}


def main() -> None:
    """Run the full local-model ablation and print content-free evidence."""
    from app.evals.cross_encoder import FastEmbedBgeReranker
    from app.evals.dense_embedding import FastEmbedBgeSmallZh

    command_started = perf_counter()
    _stderr_progress("loading Dense embedding model")
    embedding_load_started = perf_counter()
    embedder = FastEmbedBgeSmallZh(
        specific_model_path=(
            os.getenv("SOCIALEASE_EMBEDDING_MODEL_PATH", "").strip()
            or None
        ),
    )
    embedding_model_load_ms = _elapsed_ms(embedding_load_started)
    _stderr_progress(
        f"Dense embedding model loaded in {_seconds(embedding_model_load_ms)}"
    )

    _stderr_progress("loading Cross-Encoder reranker")
    reranker_load_started = perf_counter()
    reranker = FastEmbedBgeReranker(
        specific_model_path=(
            os.getenv("SOCIALEASE_RERANKER_MODEL_PATH", "").strip()
            or None
        ),
    )
    reranker_model_load_ms = _elapsed_ms(reranker_load_started)
    _stderr_progress(
        f"Cross-Encoder reranker loaded in {_seconds(reranker_model_load_ms)}"
    )

    sealed_path = os.getenv(
        "SOCIALEASE_MEMORY_SEALED_HELD_OUT_PATH",
        "",
    ).strip()
    sealed_cases = (
        load_memory_retrieval_sealed_cases(Path(sealed_path).resolve())
        if sealed_path
        else None
    )
    postgres_fts_database_url = os.getenv(
        "SOCIALEASE_TEST_DATABASE_URL",
        "",
    ).strip()
    if not postgres_fts_database_url:
        raise RuntimeError(
            "SOCIALEASE_TEST_DATABASE_URL is required for the isolated "
            "PostgreSQL FTS baseline"
        )
    report, outcomes = run_memory_retrieval_ablation(
        embedder=embedder,
        reranker_provider=reranker,
        sealed_held_out_cases=sealed_cases,
        postgres_fts_database_url=postgres_fts_database_url,
        progress=_stderr_progress,
    )
    total_duration_ms = _elapsed_ms(command_started)
    _stderr_progress(f"command complete in {_seconds(total_duration_ms)}")
    print(
        json.dumps(
            {
                "report": report.model_dump(mode="json"),
                "outcomes": outcomes,
                "runtime": {
                    "embedding_model_load_ms": round(
                        embedding_model_load_ms,
                        6,
                    ),
                    "reranker_model_load_ms": round(
                        reranker_model_load_ms,
                        6,
                    ),
                    "evaluation_duration_ms": (
                        report.evaluation_duration_ms
                    ),
                    "total_duration_ms": round(total_duration_ms, 6),
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
