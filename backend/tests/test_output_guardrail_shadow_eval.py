"""Tests for the offline local Output Guardrail Shadow training gate."""

import numpy as np

from app.evals.output_guardrail_shadow import (
    _fit_weighted_ridge,
    _metric,
    _select_threshold,
)


def test_shadow_metric_counts_binary_outcomes() -> None:
    metric = _metric(
        np.asarray([True, True, False, False]),
        np.asarray([True, False, True, False]),
    )

    assert metric.true_positives == 1
    assert metric.false_positives == 1
    assert metric.false_negatives == 1
    assert metric.precision == 0.5
    assert metric.recall == 0.5


def test_shadow_threshold_prefers_candidate_meeting_precision_floor() -> None:
    scores = np.asarray([0.9, 0.8, 0.7, 0.2])
    labels = np.asarray([True, True, False, False])

    threshold = _select_threshold(scores, labels, minimum_precision=0.9)

    assert threshold == 0.8


def test_weighted_ridge_separates_simple_training_examples() -> None:
    features = np.asarray(
        [
            [1.0, 0.0, 1.0],
            [0.8, 0.1, 1.0],
            [0.0, 1.0, 1.0],
            [0.1, 0.8, 1.0],
        ]
    )
    labels = np.asarray([True, True, False, False])

    weights = _fit_weighted_ridge(features, labels, regularization=0.1)

    assert np.all((features @ weights >= 0) == labels)
