from __future__ import annotations

from evaluation.ci_dataset import require_ci_evaluation_dataset
from evaluation.entity_resolution_benchmark import run_entity_resolution_benchmark


def test_entity_resolution_benchmark_runs_on_ci_dataset():
    dataset_path = require_ci_evaluation_dataset()
    result = run_entity_resolution_benchmark(dataset_path=dataset_path, split_name="test")
    assert result.ran_successfully
    assert result.auto_match_incorrect == 0
    assert result.hard_negative_false_auto_match == 0
    assert result.candidate_recall >= 0.94
