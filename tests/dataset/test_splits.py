from __future__ import annotations

import pytest

from dataset.splits import assign_person_splits, validate_no_split_leakage


def test_assign_person_splits_has_no_leakage() -> None:
    person_ids = [f"P-{index:06d}" for index in range(1, 101)]
    splits = assign_person_splits(
        person_ids=person_ids,
        train_ratio=0.70,
        validation_ratio=0.15,
        test_ratio=0.15,
        seed=99,
    )
    validate_no_split_leakage(splits)
    assert len(splits["train"]) == 70
    assert len(splits["validation"]) == 15
    assert len(splits["test"]) == 15


def test_validate_no_split_leakage_detects_duplicates() -> None:
    with pytest.raises(ValueError, match="appears in both"):
        validate_no_split_leakage(
            {
                "train": ["P-000001"],
                "test": ["P-000001"],
            }
        )
