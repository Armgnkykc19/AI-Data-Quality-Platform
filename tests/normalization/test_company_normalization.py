from __future__ import annotations

from normalization.engine import NormalizationEngine
from validation.models import NormalizationEligibility


def test_company_suffix_as_is_normalized_to_turkish_form(
    normalization_engine: NormalizationEngine,
) -> None:
    raw = "Acme A.S."
    normalized, transformations = normalization_engine.normalize_field(
        "company",
        raw,
        original_value=raw,
        normalization_eligibility=NormalizationEligibility.SAFE,
    )

    assert normalized == "Acme A.Ş."
    assert any(item.rule_id == "company.canonical_suffix" for item in transformations)
