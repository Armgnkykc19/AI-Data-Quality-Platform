from __future__ import annotations

from validation.config import ValidationConfig
from validation.models import FieldValidationIssue
from validation.registry import register_rule
from validation.rules.base import is_blank, issue, severity_for

TEXT_FIELDS = ("first_name", "last_name", "company", "city", "district", "address")


class TextMaxLengthRule:
    rule_id = "text.max_length"

    def validate(
        self,
        record: dict[str, str | None],
        config: ValidationConfig,
    ) -> list[FieldValidationIssue]:
        issues: list[FieldValidationIssue] = []
        severity = severity_for(config, "length")
        for field_name in TEXT_FIELDS:
            value = record.get(field_name)
            if is_blank(value):
                continue
            max_length = config.text_max_lengths.get(field_name)
            if max_length is None:
                continue
            if len(value) > max_length:
                issues.append(
                    issue(
                        field_name=field_name,
                        rule_id=self.rule_id,
                        severity=severity,
                        code="text_too_long",
                        message=(f"Field exceeds maximum length of {max_length} characters"),
                        value=value,
                    )
                )
        return issues


class TextBlankRule:
    rule_id = "text.blank"

    def validate(
        self,
        record: dict[str, str | None],
        config: ValidationConfig,
    ) -> list[FieldValidationIssue]:
        issues: list[FieldValidationIssue] = []
        severity = severity_for(config, "format")
        for field_name in TEXT_FIELDS:
            value = record.get(field_name)
            if value is not None and value.strip() == "":
                issues.append(
                    issue(
                        field_name=field_name,
                        rule_id=self.rule_id,
                        severity=severity,
                        code="blank_text",
                        message=f"Field contains only whitespace: {field_name}",
                        value=value,
                    )
                )
        return issues


register_rule(TextMaxLengthRule())
register_rule(TextBlankRule())
