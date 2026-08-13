from __future__ import annotations

import random

from dataset.corruption.operators import apply_corruption


def test_apply_corruption_records_before_and_after() -> None:
    rng = random.Random(1)
    updated, record = apply_corruption(
        corruption_type="case_change",
        field_name="first_name",
        value="Ahmet",
        rng=rng,
        person_id="P-000001",
        source_record_id="source_a-000001",
        source_name="source_a",
        severity="low",
    )

    assert updated is not None
    assert record is not None
    assert record.before_value == "Ahmet"
    assert record.after_value == updated


def test_missing_value_corruption_sets_null() -> None:
    rng = random.Random(2)
    updated, record = apply_corruption(
        corruption_type="missing_value",
        field_name="email",
        value="test@example.test",
        rng=rng,
        person_id="P-000001",
        source_record_id="source_a-000001",
        source_name="source_a",
        severity="medium",
    )

    assert updated is None
    assert record is not None
    assert record.corruption_type == "missing_value"
