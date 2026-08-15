from __future__ import annotations

from validation.config import ValidationConfig
from validation.models import FieldValidationIssue
from validation.registry import register_rule
from validation.rules.base import is_blank, issue, severity_for


class RequiredMissingRule:
    rule_id = "required.missing"

    def validate(
        self,
        record: dict[str, str | None],
        config: ValidationConfig,
    ) -> list[FieldValidationIssue]:
        issues: list[FieldValidationIssue] = []
        severity = severity_for(config, "required")
        for field_name in config.required_fields:
            value = record.get(field_name)
            if is_blank(value):
                issues.append(
                    issue(
                        field_name=field_name,
                        rule_id=self.rule_id,
                        severity=severity,
                        code="required_missing",
                        message=f"Required field is missing or blank: {field_name}",
                        value=value,
                    )
                )
        return issues


register_rule(RequiredMissingRule())
