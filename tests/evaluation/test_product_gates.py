from __future__ import annotations

import ast
from pathlib import Path

import pytest

from evaluation.product_metrics import collect_product_metrics, evaluate_product_gates
from evaluation.row_accounting import RowAccountingAuditResult, SourceRowAccounting
from evaluation.run import run_evaluation
from evaluation.source_b_mapping_benchmark import (
    DEFAULT_EXPECTED_MAPPINGS_PATH,
    run_source_b_mapping_benchmark,
)
from evaluation.threshold_sweep import (
    FORBIDDEN_CALIBRATION_SPLITS,
    _assert_calibration_split_allowed,
)


def test_product_gates_fail_closed_when_review_safety_metric_missing() -> None:
    metrics, _ = collect_product_metrics()
    with pytest.raises(KeyError, match="fail closed"):
        evaluate_product_gates(
            metrics=metrics,
            gate_config={
                "review_unresolved_unsafe_merge_violations": {
                    "metric": "review_unresolved_unsafe_merge_violations",
                    "operator": "lte",
                    "threshold": 0.0,
                }
            },
        )


def test_product_gates_fail_when_review_safety_violation_present() -> None:
    metrics = {
        "review_unresolved_unsafe_merge_violations": 1.0,
        "review_no_match_transitive_merge_violations": 0.0,
        "review_unauthorized_severe_conflict_merges": 0.0,
        "review_human_match_without_provenance_violations": 0.0,
    }
    results, _ = evaluate_product_gates(
        metrics=metrics,
        gate_config={
            "review_unresolved_unsafe_merge_violations": {
                "metric": "review_unresolved_unsafe_merge_violations",
                "operator": "lte",
                "threshold": 0.0,
            },
            "review_no_match_transitive_merge_violations": {
                "metric": "review_no_match_transitive_merge_violations",
                "operator": "lte",
                "threshold": 0.0,
            },
            "review_unauthorized_severe_conflict_merges": {
                "metric": "review_unauthorized_severe_conflict_merges",
                "operator": "lte",
                "threshold": 0.0,
            },
            "review_human_match_without_provenance_violations": {
                "metric": "review_human_match_without_provenance_violations",
                "operator": "lte",
                "threshold": 0.0,
            },
        },
    )
    assert any(
        result.name == "review_unresolved_unsafe_merge_violations" and not result.passed
        for result in results
    )


def test_product_gates_fail_closed_when_real_metric_missing() -> None:
    metrics, _ = collect_product_metrics(
        entity_resolution_benchmark=None,
        schema_mapping_benchmark=None,
        source_b_mapping_benchmark=None,
        normalization_benchmark=None,
        survivorship_benchmark=None,
        row_accounting_audit=None,
    )
    with pytest.raises(KeyError, match="fail closed"):
        evaluate_product_gates(
            metrics=metrics,
            gate_config={
                "entity_resolution_auto_match_precision": {
                    "metric": "entity_resolution_auto_match_precision",
                    "operator": "gte",
                    "threshold": 0.99,
                }
            },
        )


def test_row_accounting_detects_unaccounted_rows() -> None:
    audit = RowAccountingAuditResult(
        sources=[
            SourceRowAccounting(
                source_path="sources/source_a.csv",
                discovered_rows=10,
                accepted_rows=8,
                rejected_rows=1,
            )
        ]
    )
    assert audit.unaccounted_rows == 1
    assert audit.passed is False
    assert audit.silent_row_loss_rate == 0.1


def test_threshold_sweep_rejects_test_split() -> None:
    with pytest.raises(ValueError, match="forbidden"):
        _assert_calibration_split_allowed("test")


def test_threshold_sweep_rejects_final_holdout_split(tmp_path: Path) -> None:
    assert "final_holdout" in FORBIDDEN_CALIBRATION_SPLITS


def test_source_b_benchmark_does_not_import_generator_expected_mapping() -> None:
    source = Path("evaluation/source_b_mapping_benchmark.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = {
        node.names[0].name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "dataset.generator.sources"
        for alias in node.names
    }
    assert "source_b_expected_mapping" not in imports
    assert run_source_b_mapping_benchmark(
        expected_mappings_path=DEFAULT_EXPECTED_MAPPINGS_PATH
    ).ran_successfully


def test_run_evaluation_without_dataset_skips_product_gates(tmp_path: Path, capsys) -> None:
    config_file = tmp_path / "evaluation.yaml"
    report_directory = tmp_path / "reports"
    config_file.write_text(
        f"""
dataset:
  name: fixture
  version: "0.1.0"
infrastructure_gates:
  auto_merge_precision: {{operator: gte, threshold: 0.99}}
  false_merge_rate: {{operator: lte, threshold: 0.005}}
  candidate_recall: {{operator: gte, threshold: 0.94}}
  schema_mapping_accuracy: {{operator: gte, threshold: 0.98}}
  normalization_accuracy: {{operator: gte, threshold: 0.995}}
  review_routing_recall: {{operator: gte, threshold: 0.95}}
product_gates:
  enabled: true
  require_dataset: true
  gates:
    silent_row_loss_rate:
      metric: silent_row_loss_rate
      operator: lte
      threshold: 0.0
reporting:
  output_directory: "{report_directory.as_posix()}"
  json: false
  markdown: false
""".strip(),
        encoding="utf-8",
    )
    exit_code = run_evaluation(config_file)
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Product Hard Gates: SKIPPED" in captured.out
