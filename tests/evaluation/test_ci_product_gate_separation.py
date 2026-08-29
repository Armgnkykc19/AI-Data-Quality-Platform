from __future__ import annotations

from pathlib import Path

import yaml

from evaluation.evaluator.hard_gates import all_hard_gates_pass
from evaluation.product_metrics import evaluate_product_gates
from evaluation.run import DEFAULT_CONFIG_PATH

PROJECT_ROOT = Path(__file__).resolve().parents[2]
EVALUATION_YAML = PROJECT_ROOT / "configs" / "evaluation.yaml"
EVALUATION_CI_YAML = PROJECT_ROOT / "configs" / "evaluation.ci.yaml"
ER_BENCHMARK_TEST = PROJECT_ROOT / "tests" / "evaluation" / "test_entity_resolution_benchmark.py"


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_evaluation_yaml_keeps_certified_candidate_recall_094() -> None:
    config = _load(EVALUATION_YAML)
    assert config["acceptance_mode"] == "product"
    assert config["product_acceptance"] is True
    gate = config["product_gates"]["gates"]["entity_resolution_candidate_recall"]
    assert gate["operator"] == "gte"
    assert float(gate["threshold"]) == 0.94
    mapping = config["product_gates"]["gates"]["schema_mapping_accuracy"]
    assert float(mapping["threshold"]) == 0.95
    critical = config["product_gates"]["gates"]["critical_field_mapping_recall"]
    assert float(critical["threshold"]) == 0.95


def test_evaluation_ci_yaml_is_infrastructure_smoke_not_product_acceptance() -> None:
    text = EVALUATION_CI_YAML.read_text(encoding="utf-8")
    config = _load(EVALUATION_CI_YAML)
    assert "infrastructure smoke" in text.lower()
    assert "not product acceptance" in text.lower() or config["product_acceptance"] is False
    assert config["acceptance_mode"] == "infrastructure_smoke"
    assert config["product_acceptance"] is False
    gates = config["product_gates"]["gates"]
    assert float(gates["entity_resolution_candidate_recall"]["threshold"]) == 0.94


def test_ci_smoke_test_still_asserts_candidate_recall_094() -> None:
    source = ER_BENCHMARK_TEST.read_text(encoding="utf-8")
    assert "candidate_recall >= 0.94" in source


def _passing_product_metrics(*, candidate_recall: float) -> dict[str, float]:
    return {
        "entity_resolution_auto_match_precision": 1.0,
        "entity_resolution_false_match_rate": 0.0,
        "entity_resolution_candidate_recall": candidate_recall,
        "schema_mapping_accuracy": 1.0,
        "critical_field_mapping_recall": 1.0,
        "source_b_mapping_accuracy": 1.0,
        "normalization_accuracy": 1.0,
        "silent_row_loss_rate": 0.0,
        "survivorship_field_match_rate": 0.95,
        "survivorship_conflict_preservation_rate": 1.0,
        "review_unresolved_unsafe_merge_violations": 0.0,
        "review_no_match_transitive_merge_violations": 0.0,
        "review_unauthorized_severe_conflict_merges": 0.0,
        "review_human_match_without_provenance_violations": 0.0,
    }


def test_golden_candidate_recall_below_094_fails_product_gates() -> None:
    config = _load(EVALUATION_YAML)
    gates = config["product_gates"]["gates"]
    passing, _ = evaluate_product_gates(
        metrics=_passing_product_metrics(candidate_recall=0.94),
        gate_config=gates,
    )
    assert all_hard_gates_pass(passing)
    failing, _ = evaluate_product_gates(
        metrics=_passing_product_metrics(candidate_recall=0.93),
        gate_config=gates,
    )
    assert all_hard_gates_pass(failing) is False
    recall_gate = next(
        result for result in failing if result.name == "entity_resolution_candidate_recall"
    )
    assert recall_gate.passed is False
    assert DEFAULT_CONFIG_PATH.resolve() == EVALUATION_YAML.resolve()


def test_ci_smoke_gates_keep_candidate_recall_094() -> None:
    config = _load(EVALUATION_CI_YAML)
    results, _ = evaluate_product_gates(
        metrics=_passing_product_metrics(candidate_recall=0.94),
        gate_config=config["product_gates"]["gates"],
    )
    assert all_hard_gates_pass(results)
    failing, _ = evaluate_product_gates(
        metrics=_passing_product_metrics(candidate_recall=0.93),
        gate_config=config["product_gates"]["gates"],
    )
    assert all_hard_gates_pass(failing) is False
