from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from normalization.models import NormalizationTransformation
from validation.models import FieldValidationIssue, RecordValidationResult, ValidationSummary


@dataclass
class RecordQualityState:
    row_number: int
    original_values: dict[str, str | None]
    normalized_values: dict[str, str | None]
    pre_validation: RecordValidationResult | None = None
    post_validation: RecordValidationResult | None = None
    validation_issues: list[FieldValidationIssue] = field(default_factory=list)
    transformations: list[NormalizationTransformation] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        if self.post_validation is not None:
            return self.post_validation.is_valid
        if self.pre_validation is not None:
            return self.pre_validation.is_valid
        return True

    @property
    def changed_field_count(self) -> int:
        return sum(
            1
            for field_name, original in self.original_values.items()
            if self.normalized_values.get(field_name) != original
        )


@dataclass
class DatasetQualityResult:
    source_path: str
    headers: list[str]
    records: list[RecordQualityState] = field(default_factory=list)
    pre_validation_summary: ValidationSummary = field(default_factory=ValidationSummary)
    post_validation_summary: ValidationSummary = field(default_factory=ValidationSummary)
    total_transformations: int = 0
    changed_records: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_path": self.source_path,
            "headers": self.headers,
            "pre_validation_summary": {
                "total_records": self.pre_validation_summary.total_records,
                "valid_records": self.pre_validation_summary.valid_records,
                "invalid_records": self.pre_validation_summary.invalid_records,
                "total_issues": self.pre_validation_summary.total_issues,
                "error_count": self.pre_validation_summary.error_count,
                "warning_count": self.pre_validation_summary.warning_count,
            },
            "post_validation_summary": {
                "total_records": self.post_validation_summary.total_records,
                "valid_records": self.post_validation_summary.valid_records,
                "invalid_records": self.post_validation_summary.invalid_records,
                "total_issues": self.post_validation_summary.total_issues,
                "error_count": self.post_validation_summary.error_count,
                "warning_count": self.post_validation_summary.warning_count,
            },
            "total_transformations": self.total_transformations,
            "changed_records": self.changed_records,
            "records": [
                {
                    "row_number": record.row_number,
                    "is_valid": record.is_valid,
                    "changed_field_count": record.changed_field_count,
                    "original_values": record.original_values,
                    "normalized_values": record.normalized_values,
                    "validation_issues": [
                        {
                            "field_name": issue.field_name,
                            "rule_id": issue.rule_id,
                            "severity": issue.severity.value,
                            "code": issue.code,
                            "message": issue.message,
                            "value": issue.value,
                            "normalization_eligibility": issue.normalization_eligibility.value,
                        }
                        for issue in record.validation_issues
                    ],
                    "transformations": [
                        {
                            "field_name": item.field_name,
                            "rule_id": item.rule_id,
                            "original_value": item.original_value,
                            "normalized_value": item.normalized_value,
                            "status": item.status.value,
                        }
                        for item in record.transformations
                    ],
                }
                for record in self.records
            ],
        }
