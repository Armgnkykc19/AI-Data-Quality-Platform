import pytest

from evaluation.metrics.classification import (
    calculate_classification_metrics,
    safe_divide,
)


def test_safe_divide_returns_zero_when_denominator_is_zero() -> None:
    assert safe_divide(10, 0) == 0.0


def test_calculate_classification_metrics() -> None:
    metrics = calculate_classification_metrics(
        true_positives=80,
        false_positives=20,
        false_negatives=10,
    )

    assert metrics.true_positives == 80
    assert metrics.false_positives == 20
    assert metrics.false_negatives == 10
    assert metrics.precision == pytest.approx(0.8)
    assert metrics.recall == pytest.approx(80 / 90)
    assert metrics.f1 == pytest.approx(
        2 * metrics.precision * metrics.recall
        / (metrics.precision + metrics.recall)
    )


def test_metrics_are_zero_when_no_positive_predictions_exist() -> None:
    metrics = calculate_classification_metrics(
        true_positives=0,
        false_positives=0,
        false_negatives=10,
    )

    assert metrics.precision == 0.0
    assert metrics.recall == 0.0
    assert metrics.f1 == 0.0


def test_metrics_are_zero_when_no_positive_examples_exist() -> None:
    metrics = calculate_classification_metrics(
        true_positives=0,
        false_positives=10,
        false_negatives=0,
    )

    assert metrics.precision == 0.0
    assert metrics.recall == 0.0
    assert metrics.f1 == 0.0
