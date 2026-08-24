from __future__ import annotations

from normalization.engine import NormalizationEngine
from validation.engine import ValidationEngine
from validation.models import NormalizationEligibility


def test_broken_email_is_not_repaired_by_normalization(
    normalization_engine: NormalizationEngine,
) -> None:
    raw = "ahmet@@gmail"
    normalized, transformations = normalization_engine.normalize_field(
        "email",
        raw,
        original_value=raw,
        normalization_eligibility=NormalizationEligibility.SAFE,
    )

    assert normalized == raw
    changed = [item for item in transformations if item.normalized_value != item.original_value]
    assert changed == []


def test_broken_email_remains_invalid_after_normalization_attempt(
    normalization_engine: NormalizationEngine,
    validation_engine: ValidationEngine,
) -> None:
    raw = "ahmet@@gmail"
    normalized, _ = normalization_engine.normalize_field(
        "email",
        raw,
        original_value=raw,
        normalization_eligibility=NormalizationEligibility.SAFE,
    )
    result = validation_engine.validate_record({"email": normalized})

    email_issues = [issue for issue in result.issues if issue.rule_id == "email.syntax"]
    assert normalized == raw
    assert len(email_issues) == 1
