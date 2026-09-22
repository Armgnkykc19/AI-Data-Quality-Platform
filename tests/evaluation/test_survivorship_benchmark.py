from __future__ import annotations

from evaluation.ci_dataset import require_ci_evaluation_dataset
from evaluation.survivorship_benchmark import run_survivorship_benchmark


def test_survivorship_benchmark_runs_on_ci_dataset():
    dataset_path = require_ci_evaluation_dataset()
    result = run_survivorship_benchmark(dataset_path=dataset_path, split_name="test")
    assert result.ran_successfully
    assert result.canonical_entity_count > 0
    assert result.merge_coherence_rate >= 0.90
    assert result.cluster_person_purity_rate >= 0.99
    assert result.conflict_preservation_rate >= 0.95
