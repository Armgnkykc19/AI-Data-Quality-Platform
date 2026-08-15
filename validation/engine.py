from __future__ import annotations

import validation.rules  # noqa: F401  — register built-in rules
from validation.config import ValidationConfig
from validation.models import FieldValidationIssue, RecordValidationResult, Severity
from validation.registry import get_registered_rules


class ValidationEngine:
    def __init__(self, config: ValidationConfig) -> None:
        self._config = config
        self._rules = get_registered_rules()

    def validate_record(
        self,
        record: dict[str, str | None],
        *,
        row_number: int = 0,
    ) -> RecordValidationResult:
        issues: list[FieldValidationIssue] = []
        for rule_id, rule in self._rules.items():
            if not self._config.enabled_rules.get(rule_id, True):
                continue
            issues.extend(rule.validate(record, self._config))

        error_count = sum(1 for item in issues if item.severity == Severity.ERROR)
        warning_count = sum(1 for item in issues if item.severity == Severity.WARNING)
        info_count = sum(1 for item in issues if item.severity == Severity.INFO)

        return RecordValidationResult(
            row_number=row_number,
            issues=tuple(issues),
            error_count=error_count,
            warning_count=warning_count,
            info_count=info_count,
        )
