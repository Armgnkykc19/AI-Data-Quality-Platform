import pytest

from evaluation.evaluator.hard_gates import (
    all_hard_gates_pass,
    evaluate_gate,
    evaluate_hard_gates,
)


def test_gte_gate_passes_when_actual_exceeds_threshold() -> None:
    result = evaluate_gate(
        name="auto_merge_precision",
        actual=0.995,
        threshold=0.99,
        operator="gte",
    )

    assert result.passed is True


def test_gte_gate_fails_when_actual_is_below_threshold() -> None:
    result = evaluate_gate(
        name="candidate_recall",
        actual=0.90,
        threshold=0.94,
        operator="gte",
    )

    assert result.passed is False


def test_lte_gate_passes_when_actual_is_below_threshold() -> None:
    result = evaluate_gate(
        name="false_merge_rate",
        actual=0.003,
        threshold=0.005,
        operator="lte",
    )

    assert result.passed is True


def test_lte_gate_fails_when_actual_exceeds_threshold() -> None:
    result = evaluate_gate(
        name="false_merge_rate",
        actual=0.01,
        threshold=0.005,
        operator="lte",
    )

    assert result.passed is False


def test_gate_passes_when_actual_equals_threshold() -> None:
    minimum_result = evaluate_gate(
        name="schema_mapping_accuracy",
        actual=0.98,
        threshold=0.98,
        operator="gte",
    )
    maximum_result = evaluate_gate(
        name="false_merge_rate",
        actual=0.005,
        threshold=0.005,
        operator="lte",
    )

    assert minimum_result.passed is True
    assert maximum_result.passed is True


def test_unsupported_operator_raises_error() -> None:
    with pytest.raises(ValueError, match="Unsupported hard-gate operator"):
        evaluate_gate(
            name="example",
            actual=0.5,
            threshold=0.5,
            operator="invalid",
        )


def test_evaluate_hard_gates_evaluates_all_configured_metrics() -> None:
    metrics = {
        "auto_merge_precision": 0.995,
        "false_merge_rate": 0.003,
    }
    gate_config = {
        "auto_merge_precision": {
            "operator": "gte",
            "threshold": 0.99,
        },
        "false_merge_rate": {
            "operator": "lte",
            "threshold": 0.005,
        },
    }

    results = evaluate_hard_gates(
        metrics=metrics,
        gate_config=gate_config,
    )

    assert len(results) == 2
    assert all_hard_gates_pass(results) is True


def test_all_hard_gates_pass_returns_false_when_one_gate_fails() -> None:
    metrics = {
        "auto_merge_precision": 0.98,
        "false_merge_rate": 0.003,
    }
    gate_config = {
        "auto_merge_precision": {
            "operator": "gte",
            "threshold": 0.99,
        },
        "false_merge_rate": {
            "operator": "lte",
            "threshold": 0.005,
        },
    }

    results = evaluate_hard_gates(
        metrics=metrics,
        gate_config=gate_config,
    )

    assert all_hard_gates_pass(results) is False


def test_missing_metric_raises_error() -> None:
    gate_config = {
        "auto_merge_precision": {
            "operator": "gte",
            "threshold": 0.99,
        }
    }

    with pytest.raises(KeyError, match="Metric required by hard gate is missing"):
        evaluate_hard_gates(
            metrics={},
            gate_config=gate_config,
        )
