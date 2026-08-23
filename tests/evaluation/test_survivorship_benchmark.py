from __future__ import annotations

from pathlib import Path

from evaluation.survivorship_benchmark import run_survivorship_benchmark

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CI_DATASET = PROJECT_ROOT / "datasets" / "generated" / "ci-smoke" / "v0.1.0"


def test_survivorship_benchmark_runs_on_ci_dataset():
    if not CI_DATASET.exists():
        return
    result = run_survivorship_benchmark(dataset_path=CI_DATASET, split_name="test")
    assert result.ran_successfully
    assert result.canonical_entity_count > 0
    assert result.merge_coherence_rate >= 0.90
    assert result.cluster_person_purity_rate >= 0.99
    assert result.conflict_preservation_rate >= 0.95
