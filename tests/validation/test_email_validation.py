from __future__ import annotations

import pytest

from tests.conftest import make_valid_record
from validation.engine import ValidationEngine


@pytest.mark.parametrize(
    "email",
    [
        "ali@example.com",
        "user.name+tag@sub.example.co.uk",
    ],
)
def test_valid_email_passes_syntax_rule(
    validation_engine: ValidationEngine,
    email: str,
) -> None:
    record = make_valid_record(email=email)
    result = validation_engine.validate_record(record)

    email_issues = [issue for issue in result.issues if issue.rule_id == "email.syntax"]
    assert email_issues == []


@pytest.mark.parametrize(
    ("email", "code"),
    [
        ("ahmet@@gmail.com", "invalid_email_syntax"),
        ("missing-domain@", "invalid_email_syntax"),
        ("no-at-sign.com", "invalid_email_syntax"),
        ("@nodomain.com", "invalid_email_syntax"),
    ],
)
def test_malformed_email_fails_syntax_rule(
    validation_engine: ValidationEngine,
    email: str,
    code: str,
) -> None:
    record = make_valid_record(email=email)
    result = validation_engine.validate_record(record)

    email_issues = [issue for issue in result.issues if issue.rule_id == "email.syntax"]
    assert len(email_issues) == 1
    assert email_issues[0].code == code
    assert email_issues[0].field_name == "email"


def test_email_with_surrounding_whitespace_is_valid_before_normalization(
    validation_engine: ValidationEngine,
) -> None:
    record = make_valid_record(email="  ali@example.com  ")
    result = validation_engine.validate_record(record)

    email_issues = [issue for issue in result.issues if issue.rule_id == "email.syntax"]
    assert email_issues == []
