from __future__ import annotations

import pytest

from normalization.engine import NormalizationEngine
from validation.models import NormalizationEligibility

TARGET = "+905321234567"


@pytest.mark.parametrize(
    "raw",
    [
        "05321234567",
        "5321234567",
        "+905321234567",
        "90 532 123 45 67",
        "(0532) 123-45-67",
    ],
)
def test_various_tr_phone_formats_normalize_to_e164(
    normalization_engine: NormalizationEngine,
    raw: str,
) -> None:
    normalized, transformations = normalization_engine.normalize_field(
        "phone",
        raw,
        original_value=raw,
        normalization_eligibility=NormalizationEligibility.SAFE,
    )

    assert normalized == TARGET
    if raw != TARGET:
        assert any(item.rule_id == "phone.tr_e164" for item in transformations)
