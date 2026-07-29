"""Ablation benchmark for safe multi-route episodic-memory retrieval."""

from __future__ import annotations

from collections.abc import Sequence
import json
import math
from time import perf_counter
from typing import Any

from app.evals.loader import (
    load_memory_retrieval_cases,
    load_memory_retrieval_v2_heldout_cases,
    load_memory_vector_challenge_cases,
)
from app.evals.memory_retrieval import (
    EVAL_NOW,
    EVAL_TOKEN_BUDGET,
    evaluate_classical_strategy,
    record_from_fixture,
)
from app.evals.memory_retrieval_scale import (
    build_scale_background_memories,
    build_scale_retrieval_cases,
)
from app.evals.metrics import ratio
from app.evals.models import (
    EvalMetric,
    MemoryRetrievalAblationReport,
    MemoryRetrievalBenchmarkStrategy,
    MemoryRetrievalEvalCase,
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

    @staticmethod
    def _embed_cached(texts, *, cache, method):
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
    held_out_case_count: int = 0,
) -> tuple[MemoryRetrievalAblationReport, dict[str, list[dict[str, Any]]]]:
    """Compare each added retrieval component against the SQL Text baseline."""
    held_out_cases = load_memory_retrieval_v2_heldout_cases() if cases is None else []
    eval_cases = cases or [
        *load_memory_retrieval_cases(),
        *load_memory_vector_challenge_cases(),
        *build_scale_retrieval_cases(),
        *held_out_cases,
    ]
    if cases is None:
        held_out_case_count = len(held_out_cases)
    background_by_user = (
        {
            user_id: [
                record_from_fixture(item)
                for item in build_scale_background_memories(user_id=user_id)
            ]
            for user_id in {case.user_id for case in eval_cases}
        }
        if include_scale_background
        else {}
    )
    records_by_case = {
        case.id: [
            *[record_from_fixture(item) for item in case.memories],
            *background_by_user.get(case.user_id, []),
        ]
        for case in eval_cases
    }
    baseline, baseline_outcomes = evaluate_classical_strategy(
        eval_cases,
        strategy=MemoryRetrievalStrategy.SQL_TEXT,
        records_by_case=records_by_case,
        candidate_window_limit=100 if include_scale_background else None,
    )
    baseline = baseline.model_copy(
        update={"relevant_mrr": _mrr_metric(eval_cases, baseline_outcomes)}
    )
    reports = {MemoryRetrievalBenchmarkStrategy.SQL_TEXT.value: baseline}
    outcomes = {
        MemoryRetrievalBenchmarkStrategy.SQL_TEXT.value: baseline_outcomes
    }
    cached_embedder = _CachingEmbedder(embedder)
    for variant, channels in _VARIANT_CHANNELS.items():
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

    full = reports[MemoryRetrievalBenchmarkStrategy.FULL_PIPELINE.value]
    gate_met = passes_ablation_gate(candidate=full, baseline=baseline)
    selected = (
        MemoryRetrievalBenchmarkStrategy.FULL_PIPELINE
        if gate_met
        else MemoryRetrievalBenchmarkStrategy.SQL_TEXT
    )
    unique_memories = {
        record.summary for records in records_by_case.values() for record in records
    }
    return (
        MemoryRetrievalAblationReport(
            selected_strategy=selected,
            baseline_strategy=MemoryRetrievalBenchmarkStrategy.SQL_TEXT,
            strategies=reports,
            dataset_case_count=len(eval_cases),
            held_out_case_count=held_out_case_count,
            indexed_memory_count=len(unique_memories),
            reranker_provider=reranker_provider.provider_name,
            reranker_model=reranker_provider.model_name,
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
        dense_min_score=0.0,
    )
    reranker = CrossEncoderMemoryReranker(provider=reranker_provider)
    abstention = MemoryAbstentionPolicy()
    estimator = ConservativeTokenEstimator()
    expected_total = expected_found = 0
    false_results: list[bool] = []
    stale_results: list[bool] = []
    conflict_results: list[bool] = []
    cross_user_results: list[bool] = []
    budget_results: list[bool] = []
    abstention_labels: list[bool] = []
    abstention_predictions: list[bool] = []
    latencies: list[float] = []
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
        if variant in {
            MemoryRetrievalBenchmarkStrategy.CROSS_ENCODER,
            MemoryRetrievalBenchmarkStrategy.FULL_PIPELINE,
        }:
            reranked = reranker.rerank(
                request=request,
                candidates=recalled.candidates,
                now=EVAL_NOW,
            )
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
        latencies.append((perf_counter() - started) * 1000)
        expected_total += len(case.expected_memory_ids)
        expected_found += sum(
            memory_id in retrieved_ids for memory_id in case.expected_memory_ids
        )
        forbidden_clear = not set(retrieved_ids).intersection(
            case.forbidden_memory_ids
        )
        if case.forbidden_memory_ids:
            false_results.append(forbidden_clear)
        if case.category == "stale":
            stale_results.append(forbidden_clear)
        if case.category == "conflict":
            conflict_results.append(forbidden_clear and not retrieved_ids)
        if case.category == "cross_user":
            cross_user_results.append(forbidden_clear)
        predicted_abstain = not retrieved_ids
        abstention_labels.append(case.expected_abstain)
        abstention_predictions.append(predicted_abstain)
        budget_results.append(
            estimated_tokens <= EVAL_TOKEN_BUDGET and len(retrieved_ids) <= 3
        )
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
                "eligible_count": recalled.diagnostics.filtered.allowed_count,
                "union_count": recalled.diagnostics.union_count,
                "estimated_tokens": estimated_tokens,
                "passed": passed,
            }
        )
    true_positive_abstentions = sum(
        expected and predicted
        for expected, predicted in zip(
            abstention_labels,
            abstention_predictions,
            strict=True,
        )
    )
    predicted_abstentions = sum(abstention_predictions)
    expected_abstentions = sum(abstention_labels)
    return (
        MemoryRetrievalStrategyReport(
            strategy=variant,
            relevant_recall_at_3=ratio(expected_found, expected_total),
            relevant_mrr=_mrr_metric(cases, outcomes),
            false_recall_avoidance=_bool_metric(false_results),
            stale_recall_avoidance=_bool_metric(stale_results),
            conflict_resolution=_bool_metric(conflict_results),
            cross_user_leakage_avoidance=_bool_metric(cross_user_results),
            no_memory_abstention=ratio(
                true_positive_abstentions,
                expected_abstentions,
            ),
            abstention_precision=ratio(
                true_positive_abstentions,
                predicted_abstentions,
            ),
            abstention_recall=ratio(
                true_positive_abstentions,
                expected_abstentions,
            ),
            context_token_budget=_bool_metric(budget_results),
            case_pass_rate=_bool_metric(
                [bool(outcome["passed"]) for outcome in outcomes]
            ),
            mean_query_latency_ms=_mean(latencies),
            p95_query_latency_ms=_percentile_95(latencies),
        ),
        outcomes,
    )


def passes_ablation_gate(
    *,
    candidate: MemoryRetrievalStrategyReport,
    baseline: MemoryRetrievalStrategyReport,
) -> bool:
    """Require material relevance gain without any safety regression."""
    safety_metrics = (
        "false_recall_avoidance",
        "stale_recall_avoidance",
        "conflict_resolution",
        "cross_user_leakage_avoidance",
        "no_memory_abstention",
        "context_token_budget",
    )
    return (
        candidate.relevant_recall_at_3.score
        >= baseline.relevant_recall_at_3.score + ABLATION_RECALL_GAIN_GATE
        and all(getattr(candidate, name).score >= 1.0 for name in safety_metrics)
        and candidate.case_pass_rate.score >= baseline.case_pass_rate.score
        and candidate.p95_query_latency_ms <= ABLATION_P95_LATENCY_GATE_MS
    )


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


def _mrr_metric(
    cases: list[MemoryRetrievalEvalCase],
    outcomes: list[dict[str, Any]],
) -> EvalMetric:
    reciprocal_rank_sum = 0.0
    total = 0
    for case, outcome in zip(cases, outcomes, strict=True):
        expected = set(case.expected_memory_ids)
        if not expected:
            continue
        total += 1
        rank = next(
            (
                index
                for index, memory_id in enumerate(
                    outcome["retrieved_ids"],
                    start=1,
                )
                if memory_id in expected
            ),
            None,
        )
        if rank is not None:
            reciprocal_rank_sum += 1.0 / rank
    return ratio(reciprocal_rank_sum, total)


def _bool_metric(values: list[bool]) -> EvalMetric:
    return ratio(sum(values), len(values))


def _mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 6) if values else 0.0


def _percentile_95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, math.ceil(len(ordered) * 0.95) - 1)
    return round(ordered[index], 6)


def main() -> None:
    """Run the full local-model ablation and print content-free evidence."""
    from app.evals.cross_encoder import FastEmbedBgeReranker
    from app.evals.dense_embedding import FastEmbedBgeSmallZh

    report, outcomes = run_memory_retrieval_ablation(
        embedder=FastEmbedBgeSmallZh(),
        reranker_provider=FastEmbedBgeReranker(),
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
