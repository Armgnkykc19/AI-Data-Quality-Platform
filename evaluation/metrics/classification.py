from dataclasses import dataclass


@dataclass(frozen=True)
class ClassificationMetrics:
    true_positives: int
    false_positives: int
    false_negatives: int
    precision: float
    recall: float
    f1: float


def safe_divide(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 0.0

    return numerator / denominator


def calculate_classification_metrics(
    *,
    true_positives: int,
    false_positives: int,
    false_negatives: int,
) -> ClassificationMetrics:
    precision = safe_divide(
        true_positives,
        true_positives + false_positives,
    )

    recall = safe_divide(
        true_positives,
        true_positives + false_negatives,
    )

    if precision + recall == 0:
        f1 = 0.0
    else:
        f1 = 2 * precision * recall / (precision + recall)

    return ClassificationMetrics(
        true_positives=true_positives,
        false_positives=false_positives,
        false_negatives=false_negatives,
        precision=precision,
        recall=recall,
        f1=f1,
    )
