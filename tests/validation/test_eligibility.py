from __future__ import annotations

import pytest

from tests.conftest import make_valid_record
from validation.engine import ValidationEngine
from validation.models import NormalizationEligibility


def test_whitespace_email_issue_is_safe(validation_engine: ValidationEngine) -> None:
    record = make_valid_record(email="  test@example.com  ")
    result = validation_engine.validate_record(record)

    issues = [issue for issue in result.issues if issue.rule_id == "text.noncanonical_whitespace"]
    assert len(issues) == 1
    assert issues[0].normalization_eligibility == NormalizationEligibility.SAFE


def test_invalid_email_syntax_is_ambiguous(validation_engine: ValidationEngine) -> None:
    record = make_valid_record(email="test@@example.com")
    result = validation_engine.validate_record(record)

    issues = [issue for issue in result.issues if issue.rule_id == "email.syntax"]
    assert len(issues) == 1
    assert issues[0].normalization_eligibility == NormalizationEligibility.AMBIGUOUS


def test_noncanonical_tr_phone_is_safe(validation_engine: ValidationEngine) -> None:
    record = make_valid_record(phone="0532 123 45 67")
    result = validation_engine.validate_record(record)

    issues = [issue for issue in result.issues if issue.rule_id == "phone.tr_e164"]
    assert len(issues) == 1
    assert issues[0].normalization_eligibility == NormalizationEligibility.SAFE


def test_incomplete_phone_is_ambiguous(validation_engine: ValidationEngine) -> None:
    record = make_valid_record(phone="0532123")
    result = validation_engine.validate_record(record)

    issues = [issue for issue in result.issues if issue.rule_id == "phone.tr_e164"]
    assert len(issues) == 1
    assert issues[0].normalization_eligibility == NormalizationEligibility.AMBIGUOUS


def test_canonical_record_has_no_blocking_eligibility_issues(
    validation_engine: ValidationEngine,
) -> None:
    result = validation_engine.validate_record(make_valid_record())

    blocking = [
        issue
        for issue in result.issues
        if issue.normalization_eligibility
        in {
            NormalizationEligibility.AMBIGUOUS,
            NormalizationEligibility.UNSUPPORTED,
        }
        and issue.severity.value == "error"
    ]
    assert blocking == []


@pytest.mark.parametrize(
    ("rule_id", "expected"),
    [
        ("required.missing", NormalizationEligibility.NOT_APPLICABLE),
        ("text.noncanonical_whitespace", NormalizationEligibility.SAFE),
        ("email.syntax", NormalizationEligibility.AMBIGUOUS),
        ("location.city_known", NormalizationEligibility.UNSUPPORTED),
    ],
)
def test_default_rule_eligibility_mapping(rule_id: str, expected: NormalizationEligibility) -> None:
    from validation.eligibility import eligibility_for_rule

    assert eligibility_for_rule(rule_id) == expected
