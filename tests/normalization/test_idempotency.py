from __future__ import annotations

import pytest

from normalization.engine import NormalizationEngine
from validation.models import NormalizationEligibility


@pytest.mark.parametrize(
    ("field_name", "raw"),
    [
        ("phone", "05321234567"),
        ("first_name", "  Ali   Veli  "),
        ("city", "istanbul"),
    ],
)
def test_double_normalization_is_idempotent(
    normalization_engine: NormalizationEngine,
    field_name: str,
    raw: str,
) -> None:
    once, _ = normalization_engine.normalize_field(
        field_name,
        raw,
        original_value=raw,
        normalization_eligibility=NormalizationEligibility.SAFE,
    )
    twice, second_transformations = normalization_engine.normalize_field(
        field_name,
        once,
        original_value=raw,
        normalization_eligibility=NormalizationEligibility.SAFE,
    )

    assert twice == once
    assert second_transformations == []
