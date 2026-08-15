from __future__ import annotations

from normalization.engine import NormalizationEngine
from normalization.pipeline import field_eligibility_from_validation
from record_quality.pipeline import run_quality_pipeline
from tests.conftest import make_valid_record
from validation.engine import ValidationEngine
from validation.models import NormalizationEligibility


def test_invalid_email_is_not_auto_normalized(
    validation_engine: ValidationEngine,
    normalization_engine: NormalizationEngine,
) -> None:
    record = make_valid_record(email="test@@example.com")
    validation = validation_engine.validate_record(record)
    field_eligibility = field_eligibility_from_validation(validation)

    assert field_eligibility["email"] == NormalizationEligibility.AMBIGUOUS
    result = normalization_engine.normalize_record(
        record,
        field_eligibility=field_eligibility,
    )

    assert result.normalized_values["email"] == "test@@example.com"
    assert all(item.field_name != "email" for item in result.transformations)


def test_ambiguous_email_normalize_field_is_unchanged(
    normalization_engine: NormalizationEngine,
) -> None:
    raw = "test@@example.com"
    normalized, transformations = normalization_engine.normalize_field(
        "email",
        raw,
        original_value=raw,
        normalization_eligibility=NormalizationEligibility.AMBIGUOUS,
    )

    assert normalized == raw
    assert transformations == []


def test_unsupported_email_normalize_field_is_unchanged(
    normalization_engine: NormalizationEngine,
) -> None:
    raw = "ali@example.com"
    normalized, transformations = normalization_engine.normalize_field(
        "email",
        raw,
        original_value=raw,
        normalization_eligibility=NormalizationEligibility.UNSUPPORTED,
    )

    assert normalized == raw
    assert transformations == []


def test_not_applicable_phone_normalize_field_is_unchanged(
    normalization_engine: NormalizationEngine,
) -> None:
    raw = "05321234567"
    normalized, transformations = normalization_engine.normalize_field(
        "phone",
        raw,
        original_value=raw,
        normalization_eligibility=NormalizationEligibility.NOT_APPLICABLE,
    )

    assert normalized == raw
    assert transformations == []


def test_safe_phone_normalization_still_runs(
    validation_engine: ValidationEngine,
    normalization_engine: NormalizationEngine,
) -> None:
    record = make_valid_record(phone="0532 123 45 67")
    validation = validation_engine.validate_record(record)
    field_eligibility = field_eligibility_from_validation(validation)

    assert field_eligibility["phone"] == NormalizationEligibility.SAFE
    result = normalization_engine.normalize_record(
        record,
        field_eligibility=field_eligibility,
    )

    assert result.normalized_values["phone"] == "+905321234567"
    assert any(item.field_name == "phone" for item in result.transformations)


def test_safe_email_whitespace_normalizes(
    normalization_engine: NormalizationEngine,
) -> None:
    raw = "  ALI@EXAMPLE.COM  "
    normalized, transformations = normalization_engine.normalize_field(
        "email",
        raw,
        original_value=raw,
        normalization_eligibility=NormalizationEligibility.SAFE,
    )

    assert normalized == "ALI@example.com"
    assert transformations


def test_ambiguous_phone_is_not_auto_normalized(
    validation_engine: ValidationEngine,
    normalization_engine: NormalizationEngine,
) -> None:
    record = make_valid_record(phone="0532123")
    validation = validation_engine.validate_record(record)
    field_eligibility = field_eligibility_from_validation(validation)

    assert field_eligibility["phone"] == NormalizationEligibility.AMBIGUOUS
    result = normalization_engine.normalize_record(
        record,
        field_eligibility=field_eligibility,
    )

    assert result.normalized_values["phone"] == "0532123"


def test_normalize_record_without_eligibility_context_is_unchanged(
    normalization_engine: NormalizationEngine,
) -> None:
    record = make_valid_record(phone="05321234567", email="  ali@example.com  ")
    result = normalization_engine.normalize_record(record)

    assert result.normalized_values == record
    assert result.transformations == ()
    assert result.changed_field_count == 0


def test_whitespace_email_is_normalized_in_quality_pipeline(
    small_parsed_dataset,
) -> None:
    result = run_quality_pipeline(small_parsed_dataset)
    first = result.records[0]

    assert first.normalized_values["email"] == "ALI@example.com"


def test_broken_email_stays_unchanged_in_quality_pipeline(
    small_parsed_dataset,
) -> None:
    result = run_quality_pipeline(small_parsed_dataset)
    broken = result.records[2]

    assert broken.normalized_values["email"] == "broken@@example.com"
