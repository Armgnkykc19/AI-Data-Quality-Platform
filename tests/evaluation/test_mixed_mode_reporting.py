from __future__ import annotations

import json
from pathlib import Path

import pytest

from evaluation.evaluator.hard_gates import HardGateResult
from evaluation.reporting import (
    EVALUATION_MODE_MIXED,
    PRODUCT_QUALITY_PARTIALLY_AVAILABLE,
    build_report_data,
    write_markdown_report,
)
from evaluation.run import get_fixture_metrics, run_evaluation


def test_fixture_metrics_normalization_accuracy_is_not_real_benchmark() -> None:
    metrics = get_fixture_metrics()

    assert metrics["normalization_accuracy"] == 0.999


def test_build_report_data_includes_real_metric_domains() -> None:
    report = build_report_data(
        dataset_name="fixture",
        dataset_version="0.1.0",
        metrics={"auto_merge_precision": 0.995},
        gate_results=[
            HardGateResult(
                name="auto_merge_precision",
                actual=0.995,
                threshold=0.99,
                operator="gte",
                passed=True,
            )
        ],
        overall_passed=True,
        evaluation_mode=EVALUATION_MODE_MIXED,
        product_quality_evaluation=PRODUCT_QUALITY_PARTIALLY_AVAILABLE,
        real_validation_benchmark={
            "precision": 1.0,
            "recall": 1.0,
            "f1": 1.0,
            "true_positives": 8,
            "false_positives": 0,
            "false_negatives": 0,
        },
        real_normalization_benchmark={
            "normalization_accuracy": 1.0,
            "expected_transformations": 74,
            "correct_transformations": 74,
            "missed_transformations": 0,
            "incorrect_transformations": 0,
        },
    )

    assert report["evaluation_mode"] == EVALUATION_MODE_MIXED
    assert report["product_quality_evaluation"] == PRODUCT_QUALITY_PARTIALLY_AVAILABLE
    assert report["metrics_source"] == "fixture_smoke"
    assert report["real_validation_benchmark"]["precision"] == 1.0
    assert report["real_normalization_benchmark"]["normalization_accuracy"] == 1.0


def test_run_evaluation_fixture_only_reports_partial_product_quality(
    tmp_path: Path,
    capsys,
) -> None:
    config_file = tmp_path / "evaluation.yaml"
    report_directory = tmp_path / "reports"
    config_file.write_text(
        f"""
dataset:
  name: fixture
  version: "0.1.0"

hard_gates:
  auto_merge_precision:
    operator: gte
    threshold: 0.99
  false_merge_rate:
    operator: lte
    threshold: 0.005
  candidate_recall:
    operator: gte
    threshold: 0.94
  schema_mapping_accuracy:
    operator: gte
    threshold: 0.98
  normalization_accuracy:
    operator: gte
    threshold: 0.995
  review_routing_recall:
    operator: gte
    threshold: 0.95

reporting:
  output_directory: "{report_directory.as_posix()}"
  json: true
  markdown: true
""".strip(),
        encoding="utf-8",
    )

    exit_code = run_evaluation(config_file)
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Evaluation Mode: MIXED" in captured.out
    assert "Product Quality Evaluation: PARTIALLY_AVAILABLE" in captured.out
    assert "Real Validation Benchmark Metrics" in captured.out
    assert "Fixture Smoke Metrics (Infrastructure Only)" in captured.out
    assert "normalization_accuracy: 0.9990" in captured.out

    report = json.loads((report_directory / "report.json").read_text(encoding="utf-8"))
    assert report["evaluation_mode"] == EVALUATION_MODE_MIXED
    assert report["product_quality_evaluation"] == PRODUCT_QUALITY_PARTIALLY_AVAILABLE
    assert "real_validation_benchmark" in report
    assert "real_normalization_benchmark" not in report


def test_run_evaluation_with_dataset_reports_both_real_benchmarks(
    tmp_path: Path,
    capsys,
) -> None:
    dataset_path = Path("datasets/generated/ci-smoke/v0.1.0")
    if not dataset_path.exists():
        pytest.skip("ci-smoke dataset not generated locally")

    config_file = tmp_path / "evaluation.yaml"
    report_directory = tmp_path / "reports"
    config_file.write_text(
        f"""
dataset:
  name: fixture
  version: "0.1.0"

hard_gates:
  auto_merge_precision:
    operator: gte
    threshold: 0.99
  false_merge_rate:
    operator: lte
    threshold: 0.005
  candidate_recall:
    operator: gte
    threshold: 0.94
  schema_mapping_accuracy:
    operator: gte
    threshold: 0.98
  normalization_accuracy:
    operator: gte
    threshold: 0.995
  review_routing_recall:
    operator: gte
    threshold: 0.95

reporting:
  output_directory: "{report_directory.as_posix()}"
  json: false
  markdown: false
""".strip(),
        encoding="utf-8",
    )

    dataset_path = Path("datasets/generated/ci-smoke/v0.1.0")
    exit_code = run_evaluation(config_file, dataset_path=dataset_path)
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Real Validation Benchmark Metrics" in captured.out
    assert "Real Deterministic Normalization Benchmark Metrics" in captured.out
    assert "(source: golden_dataset_corruption_log)" in captured.out


def test_markdown_report_includes_real_benchmark_sections(tmp_path: Path) -> None:
    report = build_report_data(
        dataset_name="fixture",
        dataset_version="0.1.0",
        metrics={"auto_merge_precision": 0.995},
        gate_results=[
            HardGateResult(
                name="auto_merge_precision",
                actual=0.995,
                threshold=0.99,
                operator="gte",
                passed=True,
            )
        ],
        overall_passed=True,
        evaluation_mode=EVALUATION_MODE_MIXED,
        product_quality_evaluation=PRODUCT_QUALITY_PARTIALLY_AVAILABLE,
        real_validation_benchmark={
            "positive_class_definition": "invalid condition",
            "precision": 1.0,
            "recall": 1.0,
            "f1": 1.0,
            "true_positives": 1,
            "false_positives": 0,
            "false_negatives": 0,
        },
        real_normalization_benchmark={
            "normalization_accuracy": 1.0,
            "expected_transformations": 2,
            "correct_transformations": 2,
            "missed_transformations": 0,
            "incorrect_transformations": 0,
        },
    )
    output_path = tmp_path / "report.md"
    write_markdown_report(report, output_path)
    content = output_path.read_text(encoding="utf-8")

    assert "## Real Validation Benchmark" in content
    assert "## Real Deterministic Normalization Benchmark" in content
    assert "**Product Quality Evaluation:** PARTIALLY_AVAILABLE" in content
