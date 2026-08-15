from __future__ import annotations

import pytest

from normalization.engine import NormalizationEngine
from validation.models import NormalizationEligibility


@pytest.mark.parametrize(
    ("raw", "expected", "expected_rule"),
    [
        ("  Ali  ", "Ali", "text.trim_whitespace"),
        ("Şişli   Mahallesi", "Şişli Mahallesi", "text.collapse_whitespace"),
        ("  Çok   boşluk  ", "Çok boşluk", "text.trim_whitespace"),
    ],
)
def test_text_fields_trim_and_collapse_whitespace(
    normalization_engine: NormalizationEngine,
    raw: str,
    expected: str,
    expected_rule: str,
) -> None:
    normalized, transformations = normalization_engine.normalize_field(
        "first_name",
        raw,
        original_value=raw,
        normalization_eligibility=NormalizationEligibility.SAFE,
    )

    assert normalized == expected
    assert any(item.rule_id == expected_rule for item in transformations)


def test_turkish_characters_are_preserved_in_text_normalization(
    normalization_engine: NormalizationEngine,
) -> None:
    raw = "  Şişli  "
    normalized, _ = normalization_engine.normalize_field(
        "district",
        raw,
        original_value=raw,
        normalization_eligibility=NormalizationEligibility.SAFE,
    )

    assert normalized == "Şişli"
    assert "ş" not in normalized.lower() or "Ş" in normalized
