from __future__ import annotations

import random
from typing import Any


def assign_person_splits(
    *,
    person_ids: list[str],
    train_ratio: float,
    validation_ratio: float,
    test_ratio: float,
    seed: int,
) -> dict[str, list[str]]:
    total = train_ratio + validation_ratio + test_ratio
    if abs(total - 1.0) > 1e-6:
        raise ValueError(
            f"Split ratios must sum to 1.0, got {total:.4f}"
        )

    rng = random.Random(seed)
    shuffled = list(person_ids)
    rng.shuffle(shuffled)

    count = len(shuffled)
    train_end = int(count * train_ratio)
    validation_end = train_end + int(count * validation_ratio)

    return {
        "train": shuffled[:train_end],
        "validation": shuffled[train_end:validation_end],
        "test": shuffled[validation_end:],
    }


def validate_no_split_leakage(splits: dict[str, list[str]]) -> None:
    seen: dict[str, str] = {}
    for split_name, person_ids in splits.items():
        for person_id in person_ids:
            if person_id in seen:
                raise ValueError(
                    f"Person {person_id} appears in both "
                    f"{seen[person_id]} and {split_name}"
                )
            seen[person_id] = split_name


def build_split_metadata(
    *,
    person_ids: list[str],
    split_config: dict[str, Any],
) -> dict[str, list[str]]:
    splits = assign_person_splits(
        person_ids=person_ids,
        train_ratio=float(split_config.get("train_ratio", 0.70)),
        validation_ratio=float(split_config.get("validation_ratio", 0.15)),
        test_ratio=float(split_config.get("test_ratio", 0.15)),
        seed=int(split_config.get("holdout_seed", 99)),
    )
    validate_no_split_leakage(splits)
    return splits
