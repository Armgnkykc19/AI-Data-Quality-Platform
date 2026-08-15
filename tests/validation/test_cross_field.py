from __future__ import annotations

from tests.conftest import make_valid_record
from validation.engine import ValidationEngine


def test_matching_city_and_district_has_no_cross_field_issue(
    validation_engine: ValidationEngine,
) -> None:
    record = make_valid_record(city="İstanbul", district="Kadıköy")
    result = validation_engine.validate_record(record)

    cross_field_issues = [
        issue for issue in result.issues if issue.rule_id == "cross_field.city_district"
    ]
    assert cross_field_issues == []


def test_contradictory_city_and_district_raises_mismatch(
    validation_engine: ValidationEngine,
) -> None:
    record = make_valid_record(city="İstanbul", district="Çankaya")
    result = validation_engine.validate_record(record)

    cross_field_issues = [
        issue for issue in result.issues if issue.rule_id == "cross_field.city_district"
    ]

    assert len(cross_field_issues) == 1
    assert cross_field_issues[0].field_name == "district"
    assert cross_field_issues[0].code == "city_district_mismatch"
    assert "Çankaya" in cross_field_issues[0].message
    assert "İstanbul" in cross_field_issues[0].message


def test_blank_city_or_district_skips_cross_field_check(
    validation_engine: ValidationEngine,
) -> None:
    record = make_valid_record(city="", district="Çankaya")
    result = validation_engine.validate_record(record)

    cross_field_issues = [
        issue for issue in result.issues if issue.rule_id == "cross_field.city_district"
    ]
    assert cross_field_issues == []
