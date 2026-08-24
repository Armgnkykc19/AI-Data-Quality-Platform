from __future__ import annotations

import pytest

from dataset.splits import (
    assign_splits_with_hard_negative_pairs,
    build_split_metadata,
    validate_hard_negative_pair_atomicity,
    validate_no_split_leakage,
)


def test_four_way_split_has_no_leakage() -> None:
    person_ids = [f"P-{index:06d}" for index in range(1, 101)]
    splits = build_split_metadata(
        person_ids=person_ids,
        split_config={
            "train_ratio": 0.60,
            "validation_ratio": 0.15,
            "test_ratio": 0.15,
            "final_holdout_ratio": 0.10,
            "holdout_seed": 99,
        },
    )
    validate_no_split_leakage(splits)
    assert set(splits) == {"train", "validation", "test", "final_holdout"}
    assert len(splits["train"]) == 60
    assert len(splits["validation"]) == 15
    assert len(splits["test"]) == 15
    assert len(splits["final_holdout"]) == 10


def test_hard_negative_pairs_remain_atomic() -> None:
    person_ids = [f"P-{index:06d}" for index in range(1, 21)]
    pairs = [(person_ids[0], person_ids[1]), (person_ids[2], person_ids[3])]
    splits = assign_splits_with_hard_negative_pairs(
        person_ids=person_ids,
        hard_negative_pairs=pairs,
        train_ratio=0.60,
        validation_ratio=0.15,
        test_ratio=0.15,
        final_holdout_ratio=0.10,
        seed=99,
    )
    validate_hard_negative_pair_atomicity(splits=splits, hard_negative_pairs=pairs)


def test_hard_negative_pair_atomicity_detects_split_violation() -> None:
    with pytest.raises(ValueError, match="spans splits"):
        validate_hard_negative_pair_atomicity(
            splits={"train": ["P-000001"], "test": ["P-000002"]},
            hard_negative_pairs=[("P-000001", "P-000002")],
        )
