from __future__ import annotations

import pytest

from tests.conftest import make_valid_record
from validation.engine import ValidationEngine
from validation.models import Severity


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("first_name", None),
        ("last_name", ""),
        ("email", "   "),
        ("phone", None),
        ("company", ""),
        ("city", "  \t  "),
        ("district", None),
        ("address", ""),
    ],
)
def test_required_field_null_blank_or_whitespace_is_invalid(
    validation_engine: ValidationEngine,
    field: str,
    value: str | None,
) -> None:
    record = make_valid_record(**{field: value})
    result = validation_engine.validate_record(record)

    required_issues = [
        issue
        for issue in result.issues
        if issue.rule_id == "required.missing" and issue.field_name == field
    ]

    assert result.is_valid is False
    assert len(required_issues) == 1
    assert required_issues[0].severity == Severity.ERROR
    assert required_issues[0].code == "required_missing"


def test_optional_field_may_be_blank(validation_engine: ValidationEngine) -> None:
    record = make_valid_record()
    record["person_id"] = None

    result = validation_engine.validate_record(record)

    person_id_issues = [issue for issue in result.issues if issue.field_name == "person_id"]
    assert person_id_issues == []


def test_optional_field_may_be_whitespace_only(validation_engine: ValidationEngine) -> None:
    record = make_valid_record()
    record["person_id"] = "   "

    result = validation_engine.validate_record(record)

    person_id_issues = [issue for issue in result.issues if issue.field_name == "person_id"]
    assert person_id_issues == []


def test_fully_populated_required_record_has_no_missing_errors(
    validation_engine: ValidationEngine,
    valid_record: dict[str, str | None],
) -> None:
    result = validation_engine.validate_record(valid_record)

    missing_issues = [issue for issue in result.issues if issue.rule_id == "required.missing"]
    assert missing_issues == []
