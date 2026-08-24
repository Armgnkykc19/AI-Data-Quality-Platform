from __future__ import annotations

import pytest

from tests.conftest import make_valid_record
from validation.engine import ValidationEngine


def test_e164_tr_phone_is_valid(validation_engine: ValidationEngine) -> None:
    record = make_valid_record(phone="+905321234567")
    result = validation_engine.validate_record(record)

    phone_issues = [
        issue for issue in result.issues if issue.rule_id in {"phone.format", "phone.tr_e164"}
    ]
    assert phone_issues == []


def test_domestic_0532_format_passes_format_but_fails_e164(
    validation_engine: ValidationEngine,
) -> None:
    record = make_valid_record(phone="05321234567")
    result = validation_engine.validate_record(record)

    format_issues = [issue for issue in result.issues if issue.rule_id == "phone.format"]
    e164_issues = [issue for issue in result.issues if issue.rule_id == "phone.tr_e164"]

    assert format_issues == []
    assert len(e164_issues) == 1
    assert e164_issues[0].code == "invalid_tr_e164"


@pytest.mark.parametrize(
    "phone",
    [
        "abc-phone",
        "+90-532-abc-4567",
        "++905321234567",
        "phone:invalid!",
    ],
)
def test_phone_with_invalid_characters_fails_format_rule(
    validation_engine: ValidationEngine,
    phone: str,
) -> None:
    record = make_valid_record(phone=phone)
    result = validation_engine.validate_record(record)

    format_issues = [issue for issue in result.issues if issue.rule_id == "phone.format"]
    assert len(format_issues) == 1
    assert format_issues[0].code == "invalid_phone_format"
