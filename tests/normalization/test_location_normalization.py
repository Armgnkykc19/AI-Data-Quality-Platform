from __future__ import annotations

from normalization.engine import NormalizationEngine
from validation.models import NormalizationEligibility


def test_city_alias_normalizes_istanbul_to_canonical_form(
    normalization_engine: NormalizationEngine,
) -> None:
    raw = "istanbul"
    normalized, transformations = normalization_engine.normalize_field(
        "city",
        raw,
        original_value=raw,
        normalization_eligibility=NormalizationEligibility.SAFE,
    )

    assert normalized == "İstanbul"
    assert any(
        item.rule_id in {"location.city_alias", "location.city_case"} for item in transformations
    )
