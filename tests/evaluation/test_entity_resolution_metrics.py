from __future__ import annotations

from evaluation.entity_resolution_benchmark import EntityResolutionBenchmarkResult


def test_auto_match_recall_and_coverage_share_denominator():
    result = EntityResolutionBenchmarkResult(
        split_name="test",
        labeled_positive_pairs=145,
    )
    result.auto_match_coverage = 104 / 145
    assert result.auto_match_recall_on_labeled_positives == result.auto_match_coverage
    assert abs(result.auto_match_recall_on_labeled_positives - 0.717241) < 1e-6
