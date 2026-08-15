from __future__ import annotations

from evaluation.validation_benchmark import (
    ValidationBenchmarkFailureKind,
    load_validation_benchmark_cases,
    run_validation_benchmark,
)


def test_validation_benchmark_cases_are_loaded() -> None:
    cases = load_validation_benchmark_cases()

    assert len(cases) >= 10
    assert any(case.case_id == "email_syntax_invalid_double_at" for case in cases)


def test_validation_benchmark_computes_required_metrics() -> None:
    result = run_validation_benchmark()

    assert result.ran_successfully is True
    assert result.labeled_case_count > 0
    assert result.true_positives > 0
    assert result.true_negatives > 0
    assert 0.0 <= result.precision <= 1.0
    assert 0.0 <= result.recall <= 1.0
    assert 0.0 <= result.f1 <= 1.0
    assert result.passed is True
    assert result.false_positives == 0
    assert result.false_negatives == 0


def test_validation_benchmark_case_results_are_auditable() -> None:
    result = run_validation_benchmark()

    assert result.case_results
    sample = result.case_results[0]
    assert "case_id" in sample
    assert "input" in sample
    assert "expect_issue" in sample
    assert "detected" in sample
    assert "target_rule_id" in sample


def test_validation_benchmark_failure_taxonomy_values() -> None:
    assert ValidationBenchmarkFailureKind.FALSE_POSITIVE.value == "FALSE_POSITIVE"
    assert ValidationBenchmarkFailureKind.FALSE_NEGATIVE.value == "FALSE_NEGATIVE"
    assert ValidationBenchmarkFailureKind.RULE_ERROR.value == "RULE_ERROR"
