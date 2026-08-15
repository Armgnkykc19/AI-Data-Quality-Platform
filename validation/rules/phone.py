from __future__ import annotations

import re

from validation.config import ValidationConfig
from validation.models import FieldValidationIssue, NormalizationEligibility
from validation.registry import register_rule
from validation.rules.base import is_blank, issue, severity_for

PHONE_FORMAT_PATTERN = re.compile(r"^\+?[0-9\s().-]{7,}$")
TR_E164_PATTERN = re.compile(r"^\+90[1-9][0-9]{9}$")


def phone_tr_e164_eligibility(value: str) -> NormalizationEligibility:
    candidate = value.strip()
    if TR_E164_PATTERN.match(candidate):
        return NormalizationEligibility.NOT_APPLICABLE

    digits = re.sub(r"\D", "", candidate)
    if digits.startswith("0") and len(digits) == 11:
        return NormalizationEligibility.SAFE
    if digits.startswith("90") and len(digits) == 12:
        return NormalizationEligibility.SAFE
    if len(digits) == 10 and digits[0] in "3456789":
        return NormalizationEligibility.SAFE
    if PHONE_FORMAT_PATTERN.match(candidate) and len(digits) >= 10:
        return NormalizationEligibility.SAFE
    return NormalizationEligibility.AMBIGUOUS


class PhoneFormatRule:
    rule_id = "phone.format"

    def validate(
        self,
        record: dict[str, str | None],
        config: ValidationConfig,
    ) -> list[FieldValidationIssue]:
        value = record.get("phone")
        if is_blank(value):
            return []

        severity = severity_for(config, "format")
        if PHONE_FORMAT_PATTERN.match(value.strip()):
            return []

        return [
            issue(
                field_name="phone",
                rule_id=self.rule_id,
                severity=severity,
                code="invalid_phone_format",
                message="Phone number has invalid format",
                value=value,
            )
        ]


class PhoneTrE164Rule:
    rule_id = "phone.tr_e164"

    def validate(
        self,
        record: dict[str, str | None],
        config: ValidationConfig,
    ) -> list[FieldValidationIssue]:
        value = record.get("phone")
        if is_blank(value):
            return []

        severity = severity_for(config, "format")
        candidate = value.strip()
        if TR_E164_PATTERN.match(candidate):
            return []

        return [
            issue(
                field_name="phone",
                rule_id=self.rule_id,
                severity=severity,
                code="invalid_tr_e164",
                message="Phone number is not valid Turkish E.164 (+90XXXXXXXXXX)",
                value=value,
                normalization_eligibility=phone_tr_e164_eligibility(value),
            )
        ]


register_rule(PhoneFormatRule())
register_rule(PhoneTrE164Rule())
