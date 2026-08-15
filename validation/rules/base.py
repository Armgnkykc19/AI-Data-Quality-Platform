from __future__ import annotations

from validation.config import ValidationConfig
from validation.eligibility import eligibility_for_rule
from validation.models import FieldValidationIssue, NormalizationEligibility, Severity


def is_blank(value: str | None) -> bool:
    return value is None or value.strip() == ""


def is_present(value: str | None) -> bool:
    return value is not None and value.strip() != ""


def severity_for(config: ValidationConfig, category: str) -> Severity:
    raw = config.default_severities.get(category, "error")
    try:
        return Severity(raw)
    except ValueError:
        return Severity.ERROR


def issue(
    *,
    field_name: str,
    rule_id: str,
    severity: Severity,
    code: str,
    message: str,
    value: str | None = None,
    normalization_eligibility: NormalizationEligibility | None = None,
) -> FieldValidationIssue:
    return FieldValidationIssue(
        field_name=field_name,
        rule_id=rule_id,
        severity=severity,
        code=code,
        message=message,
        value=value,
        normalization_eligibility=(
            normalization_eligibility
            if normalization_eligibility is not None
            else eligibility_for_rule(rule_id)
        ),
    )
