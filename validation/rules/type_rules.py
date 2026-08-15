from __future__ import annotations

from validation.config import ValidationConfig
from validation.models import FieldValidationIssue
from validation.registry import register_rule
from validation.rules.base import issue, severity_for


class TypeStringRule:
    rule_id = "type.string"

    def validate(
        self,
        record: dict[str, str | None],
        config: ValidationConfig,
    ) -> list[FieldValidationIssue]:
        issues: list[FieldValidationIssue] = []
        severity = severity_for(config, "format")
        for field_name, value in record.items():
            if value is None:
                continue
            if not isinstance(value, str):
                issues.append(
                    issue(
                        field_name=field_name,
                        rule_id=self.rule_id,
                        severity=severity,
                        code="invalid_type",
                        message=f"Field must be a string, got {type(value).__name__}",
                        value=str(value),
                    )
                )
        return issues


register_rule(TypeStringRule())
