from __future__ import annotations

import re

from validation.config import ValidationConfig
from validation.models import FieldValidationIssue
from validation.registry import register_rule
from validation.rules.base import is_blank, issue, severity_for

WHITESPACE_FIELDS = (
    "first_name",
    "last_name",
    "email",
    "phone",
    "company",
    "city",
    "district",
    "address",
)

COLLAPSE_WHITESPACE_PATTERN = re.compile(r"\s{2,}")


class TextNoncanonicalWhitespaceRule:
    rule_id = "text.noncanonical_whitespace"

    def validate(
        self,
        record: dict[str, str | None],
        config: ValidationConfig,
    ) -> list[FieldValidationIssue]:
        issues: list[FieldValidationIssue] = []
        severity = severity_for(config, "whitespace")
        for field_name in WHITESPACE_FIELDS:
            value = record.get(field_name)
            if is_blank(value):
                continue
            if value != value.strip() or COLLAPSE_WHITESPACE_PATTERN.search(value):
                issues.append(
                    issue(
                        field_name=field_name,
                        rule_id=self.rule_id,
                        severity=severity,
                        code="noncanonical_whitespace",
                        message=(
                            "Field contains noncanonical whitespace that can be "
                            "deterministically normalized"
                        ),
                        value=value,
                    )
                )
        return issues


register_rule(TextNoncanonicalWhitespaceRule())
