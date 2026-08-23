from __future__ import annotations

from pathlib import Path

from evaluation.entity_resolution_benchmark import run_entity_resolution_benchmark

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CI_DATASET = PROJECT_ROOT / "datasets" / "generated" / "ci-smoke" / "v0.1.0"


def test_entity_resolution_benchmark_runs_on_ci_dataset():
    if not CI_DATASET.exists():
        return
    result = run_entity_resolution_benchmark(dataset_path=CI_DATASET, split_name="test")
    assert result.ran_successfully
    assert result.auto_match_incorrect == 0
    assert result.hard_negative_false_auto_match == 0
    assert result.candidate_recall >= 0.94
