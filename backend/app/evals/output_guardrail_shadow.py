"""Offline training gate for a local BGE output-risk Shadow classifier."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from time import perf_counter
from typing import Sequence

import numpy as np
from pydantic import BaseModel, Field

from app.evals.loader import (
    load_output_guardrail_cases,
    load_output_guardrail_shadow_train_cases,
)
from app.evals.models import (
    OutputGuardrailEvalCase,
    OutputGuardrailShadowTrainCase,
)
from app.guardrails.local_output_classifier import LOCAL_OUTPUT_RISK_CATEGORIES
from app.memory.embedding import FastEmbedBgeSmallZh
from app.models import Intent, RiskLevel


SHADOW_CATEGORIES = tuple(sorted(LOCAL_OUTPUT_RISK_CATEGORIES))


class ShadowMetric(BaseModel):
    """Bounded binary or micro-averaged classification metrics."""

    precision: float = Field(ge=0.0, le=1.0)
    recall: float = Field(ge=0.0, le=1.0)
    f1: float = Field(ge=0.0, le=1.0)
    true_positives: int = Field(ge=0)
    false_positives: int = Field(ge=0)
    false_negatives: int = Field(ge=0)


class ShadowEvaluationSlice(BaseModel):
    """One content-free evaluation slice plus synthetic case identifiers."""

    total: int = Field(ge=0)
    unsafe: int = Field(ge=0)
    safe: int = Field(ge=0)
    review: ShadowMetric
    category_micro: ShadowMetric
    false_positive_rate: float = Field(ge=0.0, le=1.0)
    missed_case_ids: list[str] = Field(default_factory=list)
    false_positive_case_ids: list[str] = Field(default_factory=list)


class ShadowTrainingReport(BaseModel):
    """Reproducible quality and latency report for one frozen training run."""

    model_name: str
    model_revision: str
    training_cases: int = Field(ge=1)
    development_cases: int = Field(ge=1)
    regularization: float = Field(gt=0.0)
    minimum_oof_precision: float = Field(ge=0.0, le=1.0)
    thresholds: dict[str, float]
    model_load_ms: float = Field(ge=0.0)
    embedding_batch_ms: float = Field(ge=0.0)
    embedding_single_p50_ms: float = Field(ge=0.0)
    embedding_single_p95_ms: float = Field(ge=0.0)
    all_development: ShadowEvaluationSlice
    local_triage_eligible: ShadowEvaluationSlice


def run_shadow_training_gate(
    *,
    model_path: Path,
    threads: int = 2,
    regularization: float = 0.1,
    minimum_oof_precision: float = 0.9,
) -> ShadowTrainingReport:
    """Train only on synthetic training data and score the fixed development set."""
    training = load_output_guardrail_shadow_train_cases()
    development = [
        case
        for case in load_output_guardrail_cases()
        if "fabricated_contact" not in case.expected_categories
    ]
    started = perf_counter()
    provider = FastEmbedBgeSmallZh(
        specific_model_path=str(model_path),
        threads=max(1, threads),
    )
    model_load_ms = (perf_counter() - started) * 1000
    all_cases: list[OutputGuardrailShadowTrainCase | OutputGuardrailEvalCase] = [
        *training,
        *development,
    ]
    texts = [_case_text(case) for case in all_cases]
    started = perf_counter()
    embeddings = np.asarray(provider.embed_documents(texts), dtype=np.float64)
    embedding_batch_ms = (perf_counter() - started) * 1000
    single_latencies = _single_embedding_latencies(
        provider,
        texts[: min(30, len(texts))],
    )

    training_count = len(training)
    training_features = _with_bias(embeddings[:training_count])
    development_features = _with_bias(embeddings[training_count:])
    training_labels = np.asarray(
        [
            [category in case.categories for category in SHADOW_CATEGORIES]
            for case in training
        ],
        dtype=bool,
    )
    development_labels = np.asarray(
        [
            [category in case.expected_categories for category in SHADOW_CATEGORIES]
            for case in development
        ],
        dtype=bool,
    )
    folds = np.asarray([_fold_for(case.id) for case in training])
    out_of_fold = np.zeros_like(training_labels, dtype=np.float64)
    for fold in range(5):
        train_mask = folds != fold
        test_mask = ~train_mask
        for category_index in range(len(SHADOW_CATEGORIES)):
            weights = _fit_weighted_ridge(
                training_features[train_mask],
                training_labels[train_mask, category_index],
                regularization=regularization,
            )
            out_of_fold[test_mask, category_index] = (
                training_features[test_mask] @ weights
            )
    thresholds = np.asarray(
        [
            _select_threshold(
                out_of_fold[:, category_index],
                training_labels[:, category_index],
                minimum_precision=minimum_oof_precision,
            )
            for category_index in range(len(SHADOW_CATEGORIES))
        ]
    )
    weights = np.column_stack(
        [
            _fit_weighted_ridge(
                training_features,
                training_labels[:, category_index],
                regularization=regularization,
            )
            for category_index in range(len(SHADOW_CATEGORIES))
        ]
    )
    predictions = development_features @ weights >= thresholds
    eligible_indices = [
        index
        for index, case in enumerate(development)
        if _is_local_triage_eligible(case)
    ]
    return ShadowTrainingReport(
        model_name=provider.model_name,
        model_revision=provider.model_revision,
        training_cases=training_count,
        development_cases=len(development),
        regularization=regularization,
        minimum_oof_precision=minimum_oof_precision,
        thresholds={
            category: round(float(threshold), 6)
            for category, threshold in zip(SHADOW_CATEGORIES, thresholds, strict=True)
        },
        model_load_ms=round(model_load_ms, 3),
        embedding_batch_ms=round(embedding_batch_ms, 3),
        embedding_single_p50_ms=round(float(np.percentile(single_latencies, 50)), 3),
        embedding_single_p95_ms=round(float(np.percentile(single_latencies, 95)), 3),
        all_development=_evaluation_slice(
            development,
            predictions,
            development_labels,
        ),
        local_triage_eligible=_evaluation_slice(
            [development[index] for index in eligible_indices],
            predictions[eligible_indices],
            development_labels[eligible_indices],
        ),
    )


def _case_text(
    case: OutputGuardrailShadowTrainCase | OutputGuardrailEvalCase,
) -> str:
    return f"用户消息：{case.user_message}\n助手回复：{case.response}"


def _with_bias(embeddings: np.ndarray) -> np.ndarray:
    return np.column_stack([embeddings, np.ones(len(embeddings))])


def _fold_for(case_id: str) -> int:
    digest = hashlib.sha256(case_id.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") % 5


def _fit_weighted_ridge(
    features: np.ndarray,
    labels: np.ndarray,
    *,
    regularization: float,
) -> np.ndarray:
    positives = max(1, int(labels.sum()))
    negatives = max(1, len(labels) - positives)
    sample_weights = np.where(
        labels,
        len(labels) / (2 * positives),
        len(labels) / (2 * negatives),
    )
    square_root_weights = np.sqrt(sample_weights)
    weighted_features = features * square_root_weights[:, None]
    weighted_targets = square_root_weights * (labels.astype(float) * 2 - 1)
    gram = weighted_features @ weighted_features.T
    return weighted_features.T @ np.linalg.solve(
        gram + regularization * np.eye(len(features)),
        weighted_targets,
    )


def _select_threshold(
    scores: np.ndarray,
    labels: np.ndarray,
    *,
    minimum_precision: float,
) -> float:
    candidates: list[tuple[bool, float, float, float, float]] = []
    for threshold in sorted(set(float(score) for score in scores)):
        metric = _metric(scores >= threshold, labels)
        candidates.append(
            (
                metric.precision >= minimum_precision,
                metric.f1,
                metric.recall,
                metric.precision,
                threshold,
            )
        )
    return max(candidates)[-1]


def _evaluation_slice(
    cases: Sequence[OutputGuardrailEvalCase],
    predictions: np.ndarray,
    labels: np.ndarray,
) -> ShadowEvaluationSlice:
    predicted_review = predictions.any(axis=1)
    expected_review = labels.any(axis=1)
    review = _metric(predicted_review, expected_review)
    category_micro = _metric(predictions.ravel(), labels.ravel())
    safe_count = int((~expected_review).sum())
    false_positive_rate = review.false_positives / max(1, safe_count)
    return ShadowEvaluationSlice(
        total=len(cases),
        unsafe=int(expected_review.sum()),
        safe=safe_count,
        review=review,
        category_micro=category_micro,
        false_positive_rate=round(false_positive_rate, 6),
        missed_case_ids=[
            case.id
            for case, predicted, expected in zip(
                cases,
                predicted_review,
                expected_review,
                strict=True,
            )
            if expected and not predicted
        ],
        false_positive_case_ids=[
            case.id
            for case, predicted, expected in zip(
                cases,
                predicted_review,
                expected_review,
                strict=True,
            )
            if predicted and not expected
        ],
    )


def _metric(predictions: np.ndarray, labels: np.ndarray) -> ShadowMetric:
    true_positives = int(np.logical_and(predictions, labels).sum())
    false_positives = int(np.logical_and(predictions, ~labels).sum())
    false_negatives = int(np.logical_and(~predictions, labels).sum())
    precision = true_positives / max(1, true_positives + false_positives)
    recall = true_positives / max(1, true_positives + false_negatives)
    f1 = 2 * precision * recall / max(1e-12, precision + recall)
    return ShadowMetric(
        precision=round(precision, 6),
        recall=round(recall, 6),
        f1=round(f1, 6),
        true_positives=true_positives,
        false_positives=false_positives,
        false_negatives=false_negatives,
    )


def _single_embedding_latencies(
    provider: FastEmbedBgeSmallZh,
    texts: Sequence[str],
) -> list[float]:
    latencies: list[float] = []
    for text in texts:
        started = perf_counter()
        provider.embed_documents([text])
        latencies.append((perf_counter() - started) * 1000)
    return latencies


def _is_local_triage_eligible(case: OutputGuardrailEvalCase) -> bool:
    return (
        case.risk_level is RiskLevel.LOW
        and case.selected_skill == "general_support_skill"
        and case.intent in {Intent.EMOTIONAL_SUPPORT, Intent.PROGRESS_REVIEW}
    )


def main() -> None:
    """Run the offline gate and optionally persist its JSON report."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-path", type=Path, required=True)
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument("--regularization", type=float, default=0.1)
    parser.add_argument("--minimum-oof-precision", type=float, default=0.9)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = run_shadow_training_gate(
        model_path=args.model_path,
        threads=args.threads,
        regularization=args.regularization,
        minimum_oof_precision=args.minimum_oof_precision,
    )
    payload = report.model_dump_json(indent=2)
    if args.output is not None:
        args.output.write_text(payload + "\n", encoding="utf-8")
    print(payload)


if __name__ == "__main__":
    main()
