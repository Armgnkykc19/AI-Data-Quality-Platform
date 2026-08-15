from __future__ import annotations

from validation.config import ValidationConfig
from validation.models import FieldValidationIssue
from validation.registry import register_rule
from validation.rules.base import is_blank, issue, severity_for

MIN_ADDRESS_LENGTH = 5


class AddressMinLengthRule:
    rule_id = "address.min_length"

    def validate(
        self,
        record: dict[str, str | None],
        config: ValidationConfig,
    ) -> list[FieldValidationIssue]:
        value = record.get("address")
        if is_blank(value):
            return []

        severity = severity_for(config, "format")
        stripped = value.strip()
        if len(stripped) >= MIN_ADDRESS_LENGTH:
            return []

        return [
            issue(
                field_name="address",
                rule_id=self.rule_id,
                severity=severity,
                code="address_too_short",
                message=f"Address must be at least {MIN_ADDRESS_LENGTH} characters",
                value=value,
            )
        ]


register_rule(AddressMinLengthRule())
