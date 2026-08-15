from __future__ import annotations

import re

from validation.config import ValidationConfig
from validation.models import FieldValidationIssue
from validation.registry import register_rule
from validation.rules.base import is_blank, issue, severity_for

EMAIL_SYNTAX_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class EmailSyntaxRule:
    rule_id = "email.syntax"

    def validate(
        self,
        record: dict[str, str | None],
        config: ValidationConfig,
    ) -> list[FieldValidationIssue]:
        value = record.get("email")
        if is_blank(value):
            return []

        severity = severity_for(config, "format")
        normalized = value.strip()
        if EMAIL_SYNTAX_PATTERN.match(normalized):
            return []

        return [
            issue(
                field_name="email",
                rule_id=self.rule_id,
                severity=severity,
                code="invalid_email_syntax",
                message="Email address has invalid syntax",
                value=value,
            )
        ]


register_rule(EmailSyntaxRule())
