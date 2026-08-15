from __future__ import annotations

from validation.config import ValidationConfig
from validation.models import FieldValidationIssue
from validation.registry import register_rule
from validation.rules.base import is_blank, issue, severity_for

MIN_COMPANY_LENGTH = 2


class CompanyMinLengthRule:
    rule_id = "company.min_length"

    def validate(
        self,
        record: dict[str, str | None],
        config: ValidationConfig,
    ) -> list[FieldValidationIssue]:
        value = record.get("company")
        if is_blank(value):
            return []

        severity = severity_for(config, "format")
        stripped = value.strip()
        if len(stripped) >= MIN_COMPANY_LENGTH:
            return []

        return [
            issue(
                field_name="company",
                rule_id=self.rule_id,
                severity=severity,
                code="company_too_short",
                message=f"Company name must be at least {MIN_COMPANY_LENGTH} characters",
                value=value,
            )
        ]


register_rule(CompanyMinLengthRule())
