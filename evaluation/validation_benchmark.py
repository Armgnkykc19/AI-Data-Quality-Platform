from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from validation.config import load_validation_config
from validation.engine import ValidationEngine

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES_PATH = (
    PROJECT_ROOT / "evaluation" / "fixtures" / "validation_benchmark_cases.json"
)

POSITIVE_CLASS_DEFINITION = (
    "A labeled invalid condition that the deterministic validation engine is "
    "expected to detect via the target rule."
)


class ValidationBenchmarkFailureKind(StrEnum):
    FALSE_POSITIVE = "FALSE_POSITIVE"
    FALSE_NEGATIVE = "FALSE_NEGATIVE"
    RULE_ERROR = "RULE_ERROR"


@dataclass(frozen=True)
class ValidationBenchmarkCase:
    case_id: str
    category: str
    target_rule_id: str
    expect_issue: bool
    record: dict[str, str | None]


@dataclass
class ValidationBenchmarkFailure:
    case_id: str
    category: str
    target_rule_id: str
    failure_kind: ValidationBenchmarkFailureKind
    expect_issue: bool
    detected: bool
    input_record: dict[str, str | None]
    actual_rule_ids: tuple[str, ...]
    message: str


@dataclass
class ValidationBenchmarkResult:
    positive_class_definition: str = POSITIVE_CLASS_DEFINITION
    labeled_case_count: int = 0
    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0
    true_negatives: int = 0
    precision: float = 0.0
    recall: float = 0.0
    f1: float = 0.0
    accuracy: float = 0.0
    failures: list[ValidationBenchmarkFailure] = field(default_factory=list)
    case_results: list[dict[str, Any]] = field(default_factory=list)
    ran_successfully: bool = False
    error_message: str | None = None

    @property
    def passed(self) -> bool:
        return self.false_positives == 0 and self.false_negatives == 0


def _safe_divide(numerator: float, denominator: float) -> float:
    if denominator == 0:
        return 0.0
    return numerator / denominator


def _compute_classification_metrics(
    *,
    true_positives: int,
    false_positives: int,
    false_negatives: int,
    true_negatives: int,
) -> tuple[float, float, float, float]:
    precision = _safe_divide(true_positives, true_positives + false_positives)
    recall = _safe_divide(true_positives, true_positives + false_negatives)
    if precision + recall == 0:
        f1 = 0.0
    else:
        f1 = 2 * precision * recall / (precision + recall)
    accuracy = _safe_divide(
        true_positives + true_negatives,
        true_positives + false_positives + false_negatives + true_negatives,
    )
    return precision, recall, f1, accuracy


def load_validation_benchmark_cases(
    cases_path: Path = DEFAULT_CASES_PATH,
) -> list[ValidationBenchmarkCase]:
    raw_cases = json.loads(cases_path.read_text(encoding="utf-8"))
    cases: list[ValidationBenchmarkCase] = []
    for item in raw_cases:
        record = {str(key): value for key, value in item["record"].items()}
        cases.append(
            ValidationBenchmarkCase(
                case_id=str(item["case_id"]),
                category=str(item["category"]),
                target_rule_id=str(item["target_rule_id"]),
                expect_issue=bool(item["expect_issue"]),
                record=record,
            )
        )
    return cases


def run_validation_benchmark(
    *,
    cases_path: Path = DEFAULT_CASES_PATH,
) -> ValidationBenchmarkResult:
    result = ValidationBenchmarkResult()
    try:
        cases = load_validation_benchmark_cases(cases_path)
        engine = ValidationEngine(load_validation_config())
        result.labeled_case_count = len(cases)

        for case in cases:
            try:
                validation = engine.validate_record(case.record)
                detected = any(
                    issue.rule_id == case.target_rule_id for issue in validation.issues
                )
                actual_rule_ids = tuple(sorted({issue.rule_id for issue in validation.issues}))

                if case.expect_issue and detected:
                    result.true_positives += 1
                elif case.expect_issue and not detected:
                    result.false_negatives += 1
                    result.failures.append(
                        ValidationBenchmarkFailure(
                            case_id=case.case_id,
                            category=case.category,
                            target_rule_id=case.target_rule_id,
                            failure_kind=ValidationBenchmarkFailureKind.FALSE_NEGATIVE,
                            expect_issue=True,
                            detected=False,
                            input_record=case.record,
                            actual_rule_ids=actual_rule_ids,
                            message=(
                                f"Expected rule {case.target_rule_id} to detect invalid "
                                f"condition for case {case.case_id}"
                            ),
                        )
                    )
                elif not case.expect_issue and detected:
                    result.false_positives += 1
                    result.failures.append(
                        ValidationBenchmarkFailure(
                            case_id=case.case_id,
                            category=case.category,
                            target_rule_id=case.target_rule_id,
                            failure_kind=ValidationBenchmarkFailureKind.FALSE_POSITIVE,
                            expect_issue=False,
                            detected=True,
                            input_record=case.record,
                            actual_rule_ids=actual_rule_ids,
                            message=(
                                f"Rule {case.target_rule_id} fired unexpectedly for "
                                f"case {case.case_id}"
                            ),
                        )
                    )
                else:
                    result.true_negatives += 1

                result.case_results.append(
                    {
                        "case_id": case.case_id,
                        "category": case.category,
                        "target_rule_id": case.target_rule_id,
                        "expect_issue": case.expect_issue,
                        "detected": detected,
                        "input": case.record,
                        "actual_rule_ids": list(actual_rule_ids),
                        "passed": (case.expect_issue == detected),
                    }
                )
            except Exception as exc:  # noqa: BLE001 - benchmark must surface rule failures
                result.failures.append(
                    ValidationBenchmarkFailure(
                        case_id=case.case_id,
                        category=case.category,
                        target_rule_id=case.target_rule_id,
                        failure_kind=ValidationBenchmarkFailureKind.RULE_ERROR,
                        expect_issue=case.expect_issue,
                        detected=False,
                        input_record=case.record,
                        actual_rule_ids=(),
                        message=str(exc),
                    )
                )

        (
            result.precision,
            result.recall,
            result.f1,
            result.accuracy,
        ) = _compute_classification_metrics(
            true_positives=result.true_positives,
            false_positives=result.false_positives,
            false_negatives=result.false_negatives,
            true_negatives=result.true_negatives,
        )
        result.ran_successfully = True
        return result
    except Exception as exc:  # noqa: BLE001 - return structured benchmark failure
        result.error_message = str(exc)
        return result


def failures_to_dict(failures: list[ValidationBenchmarkFailure]) -> list[dict[str, Any]]:
    return [
        {
            "case_id": failure.case_id,
            "category": failure.category,
            "target_rule_id": failure.target_rule_id,
            "failure_kind": failure.failure_kind.value,
            "expect_issue": failure.expect_issue,
            "detected": failure.detected,
            "input": failure.input_record,
            "actual_rule_ids": list(failure.actual_rule_ids),
            "message": failure.message,
        }
        for failure in failures
    ]
