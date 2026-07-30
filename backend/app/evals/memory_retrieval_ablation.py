"""Ablation benchmark for safe multi-route episodic-memory retrieval."""

from __future__ import annotations

from collections.abc import Callable, Sequence
import json
import os
from time import perf_counter
from typing import Any

from app.evals.memory_retrieval_dataset import (
    MemoryRetrievalDataset,
    MemoryRetrievalEvalSplit,
    build_custom_memory_retrieval_dataset,
    build_default_memory_retrieval_dataset,
)
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
) -> tuple[MemoryRetrievalAblationReport, dict[str, list[dict[str, Any]]]]:
    """Compare each added retrieval component against the SQL Text baseline."""
    dataset = (
        build_custom_memory_retrieval_dataset(cases)
        if cases is not None
        else build_default_memory_retrieval_dataset(
            include_scale_background=include_scale_background,
        )
    )
    eval_cases = dataset.cases
    records_by_case = dataset.records_by_case
    held_out_case_count = len(
        dataset.splits[MemoryRetrievalEvalSplit.HELD_OUT]
    )
    baseline, baseline_outcomes = evaluate_classical_strategy(
        eval_cases,
        strategy=MemoryRetrievalStrategy.SQL_TEXT,
        records_by_case=records_by_case,
        candidate_window_limit=(
            SQL_CANDIDATE_WINDOW if include_scale_background else None
        ),
    )
    reports = {MemoryRetrievalBenchmarkStrategy.SQL_TEXT.value: baseline}
    outcomes = {
        MemoryRetrievalBenchmarkStrategy.SQL_TEXT.value: baseline_outcomes
    }
    cached_embedder = _CachingEmbedder(embedder)
    document_started = perf_counter()
    cached_embedder.preload_documents(sorted(dataset.unique_summaries))
    document_embedding_latency_ms = (
        perf_counter() - document_started
    ) * 1000
    _warm_reranker(reranker_provider)
    for variant, channels in _VARIANT_CHANNELS.items():
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

    split_reports = _build_split_reports(
        dataset=dataset,
        reports=reports,
        outcomes=outcomes,
    )
    aggregate_full = reports[
        MemoryRetrievalBenchmarkStrategy.FULL_PIPELINE.value
    ]
    held_out_reports = split_reports[MemoryRetrievalEvalSplit.HELD_OUT.value]
    scale_reports = split_reports[MemoryRetrievalEvalSplit.SCALE.value]
    if held_out_reports.case_count and scale_reports.case_count:
        gate_met = passes_ablation_gate(
            candidate=held_out_reports.strategies[
                MemoryRetrievalBenchmarkStrategy.FULL_PIPELINE.value
            ],
            baseline=held_out_reports.strategies[
                MemoryRetrievalBenchmarkStrategy.SQL_TEXT.value
            ],
            recall_candidate=scale_reports.strategies[
                MemoryRetrievalBenchmarkStrategy.FULL_PIPELINE.value
            ],
            recall_baseline=scale_reports.strategies[
                MemoryRetrievalBenchmarkStrategy.SQL_TEXT.value
            ],
            latency_candidate=aggregate_full,
        )
    else:
        gate_met = passes_ablation_gate(
            candidate=aggregate_full,
            baseline=baseline,
        )
    selected = (
        MemoryRetrievalBenchmarkStrategy.FULL_PIPELINE
        if gate_met
        else MemoryRetrievalBenchmarkStrategy.SQL_TEXT
    )
    return (
        MemoryRetrievalAblationReport(
            selected_strategy=selected,
            baseline_strategy=MemoryRetrievalBenchmarkStrategy.SQL_TEXT,
            strategies=reports,
            dataset_case_count=len(eval_cases),
            development_case_count=len(
                dataset.splits[MemoryRetrievalEvalSplit.DEVELOPMENT]
            ),
            scale_case_count=len(
                dataset.splits[MemoryRetrievalEvalSplit.SCALE]
            ),
            held_out_case_count=held_out_case_count,
            indexed_memory_count=len(dataset.unique_records),
            unique_summary_count=len(dataset.unique_summaries),
            max_candidates_per_query=dataset.max_candidates_per_query,
            document_embedding_latency_ms=round(
                document_embedding_latency_ms,
                6,
            ),
            embedding_provider=embedder.provider_name,
            embedding_model=embedder.model_name,
            embedding_model_revision=embedder.model_revision,
            embedding_dimensions=embedder.dimensions,
            reranker_provider=reranker_provider.provider_name,
            reranker_model=reranker_provider.model_name,
            reranker_model_revision=reranker_provider.model_revision,
            experiment_config={
                "sql_candidate_window": SQL_CANDIDATE_WINDOW,
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
                "context_token_budget": EVAL_TOKEN_BUDGET,
                "output_limit": 3,
            },
            splits=split_reports,
            adoption_gate_met=gate_met,
        ),
        outcomes,
    )


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
        recalled = recall.recall(
            request=request,
            records=records_by_case[case.id],
            now=EVAL_NOW,
        )
        abstention_reason = None
        abstention_top_score = None
        abstention_score_margin = None
        rerank_input_count = 0
        if variant in {
            MemoryRetrievalBenchmarkStrategy.CROSS_ENCODER,
            MemoryRetrievalBenchmarkStrategy.FULL_PIPELINE,
        }:
            reranked = reranker.rerank(
                request=request,
                candidates=recalled.candidates,
                now=EVAL_NOW,
            )
            rerank_input_count = reranked.diagnostics.reranked_candidate_count
            if variant == MemoryRetrievalBenchmarkStrategy.FULL_PIPELINE:
                decision = abstention.decide(
                    request=request,
                    candidates=reranked.candidates,
                    now=EVAL_NOW,
                )
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
        retrieved_ids, estimated_tokens = _fit_selected(
            selected_records,
            estimator=estimator,
        )
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
                "estimated_tokens": estimated_tokens,
                "query_latency_ms": round(query_latency_ms, 6),
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


def main() -> None:
    """Run the full local-model ablation and print content-free evidence."""
    from app.evals.cross_encoder import FastEmbedBgeReranker
    from app.evals.dense_embedding import FastEmbedBgeSmallZh

    report, outcomes = run_memory_retrieval_ablation(
        embedder=FastEmbedBgeSmallZh(
            specific_model_path=(
                os.getenv("SOCIALEASE_EMBEDDING_MODEL_PATH", "").strip()
                or None
            ),
        ),
        reranker_provider=FastEmbedBgeReranker(
            specific_model_path=(
                os.getenv("SOCIALEASE_RERANKER_MODEL_PATH", "").strip()
                or None
            ),
        ),
    )
    print(
        json.dumps(
            {
                "report": report.model_dump(mode="json"),
                "outcomes": outcomes,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
