from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class NormalizationEligibility(StrEnum):
    SAFE = "SAFE"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    AMBIGUOUS = "AMBIGUOUS"
    UNSUPPORTED = "UNSUPPORTED"


@dataclass(frozen=True)
class FieldValidationIssue:
    field_name: str
    rule_id: str
    severity: Severity
    code: str
    message: str
    value: str | None = None
    normalization_eligibility: NormalizationEligibility = NormalizationEligibility.NOT_APPLICABLE


@dataclass(frozen=True)
class RecordValidationResult:
    row_number: int
    issues: tuple[FieldValidationIssue, ...] = ()
    error_count: int = 0
    warning_count: int = 0
    info_count: int = 0

    @property
    def is_valid(self) -> bool:
        return self.error_count == 0


@dataclass
class ValidationSummary:
    total_records: int = 0
    valid_records: int = 0
    invalid_records: int = 0
    total_issues: int = 0
    error_count: int = 0
    warning_count: int = 0
    info_count: int = 0
    issues_by_rule: dict[str, int] = field(default_factory=dict)
    issues_by_field: dict[str, int] = field(default_factory=dict)


@dataclass
class DatasetValidationResult:
    source_path: str
    headers: list[str]
    records: list[RecordValidationResult] = field(default_factory=list)
    summary: ValidationSummary = field(default_factory=ValidationSummary)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_path": self.source_path,
            "headers": self.headers,
            "summary": {
                "total_records": self.summary.total_records,
                "valid_records": self.summary.valid_records,
                "invalid_records": self.summary.invalid_records,
                "total_issues": self.summary.total_issues,
                "error_count": self.summary.error_count,
                "warning_count": self.summary.warning_count,
                "info_count": self.summary.info_count,
                "issues_by_rule": self.summary.issues_by_rule,
                "issues_by_field": self.summary.issues_by_field,
            },
            "records": [
                {
                    "row_number": record.row_number,
                    "is_valid": record.is_valid,
                    "error_count": record.error_count,
                    "warning_count": record.warning_count,
                    "info_count": record.info_count,
                    "issues": [
                        {
                            "field_name": issue.field_name,
                            "rule_id": issue.rule_id,
                            "severity": issue.severity.value,
                            "code": issue.code,
                            "message": issue.message,
                            "value": issue.value,
                            "normalization_eligibility": issue.normalization_eligibility.value,
                        }
                        for issue in record.issues
                    ],
                }
                for record in self.records
            ],
        }
