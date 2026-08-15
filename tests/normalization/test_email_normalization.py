from __future__ import annotations

from normalization.engine import NormalizationEngine
from validation.models import NormalizationEligibility


def test_email_trim_removes_surrounding_whitespace(
    normalization_engine: NormalizationEngine,
) -> None:
    raw = "  ALI@EXAMPLE.COM  "
    normalized, transformations = normalization_engine.normalize_field(
        "email",
        raw,
        original_value=raw,
        normalization_eligibility=NormalizationEligibility.SAFE,
    )

    trim_rules = [
        item
        for item in transformations
        if item.rule_id in {"email.trim", "text.trim_whitespace"}
    ]
    assert trim_rules
    assert normalized == normalized.strip()
    assert normalized == "ALI@example.com"


def test_email_lower_domain_lowercases_domain_only(
    normalization_engine: NormalizationEngine,
) -> None:
    raw = "ALI@EXAMPLE.COM"
    normalized, transformations = normalization_engine.normalize_field(
        "email",
        raw,
        original_value=raw,
        normalization_eligibility=NormalizationEligibility.SAFE,
    )

    assert normalized == "ALI@example.com"
    assert any(item.rule_id == "email.lower_domain" for item in transformations)


def test_email_normalization_does_not_repair_double_at_sign(
    normalization_engine: NormalizationEngine,
) -> None:
    raw = "ahmet@@gmail.com"
    normalized, _ = normalization_engine.normalize_field(
        "email",
        raw,
        original_value=raw,
        normalization_eligibility=NormalizationEligibility.SAFE,
    )

    assert normalized == "ahmet@@gmail.com"
