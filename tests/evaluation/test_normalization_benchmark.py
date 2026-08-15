from __future__ import annotations

import json
from pathlib import Path

import pytest

from evaluation.normalization_benchmark import run_normalization_benchmark


def _write_corruption_log(path: Path, events: list[dict[str, object]]) -> None:
    ground_truth = path / "ground_truth"
    ground_truth.mkdir(parents=True, exist_ok=True)
    log_path = ground_truth / "corruption_log.jsonl"
    with log_path.open("w", encoding="utf-8") as file:
        for event in events:
            file.write(json.dumps(event) + "\n")


def test_normalization_benchmark_returns_result_with_accuracy_property(
    tmp_path: Path,
) -> None:
    _write_corruption_log(
        tmp_path,
        [
            {
                "corruption_type": "whitespace",
                "field_name": "first_name",
                "before_value": "Ali",
                "after_value": "  Ali  ",
            },
            {
                "corruption_type": "phone_format",
                "field_name": "phone",
                "before_value": "+905321234567",
                "after_value": "05321234567",
            },
            {
                "corruption_type": "typo",
                "field_name": "first_name",
                "before_value": "Ali",
                "after_value": "Al1",
            },
        ],
    )

    result = run_normalization_benchmark(dataset_path=tmp_path)

    assert result.expected_transformations == 2
    assert result.correct_transformations == 2
    assert hasattr(result, "normalization_accuracy")
    assert result.normalization_accuracy == 1.0
    assert result.passed is True


def test_normalization_benchmark_accuracy_is_one_when_no_events(tmp_path: Path) -> None:
    _write_corruption_log(tmp_path, [])

    result = run_normalization_benchmark(dataset_path=tmp_path)

    assert result.expected_transformations == 0
    assert result.normalization_accuracy == 1.0
    assert "normalization_benchmark:no_normalizable_events" in result.messages


def test_fixture_and_real_normalization_metrics_are_distinct() -> None:
    from evaluation.run import get_fixture_metrics

    fixture_metric = get_fixture_metrics()["normalization_accuracy"]
    dataset_path = Path("datasets/generated/ci-smoke/v0.1.0")
    if not dataset_path.exists():
        pytest.skip("ci-smoke dataset not generated locally")

    real_result = run_normalization_benchmark(dataset_path=dataset_path)

    assert fixture_metric == 0.999
    assert real_result.normalization_accuracy == 1.0
