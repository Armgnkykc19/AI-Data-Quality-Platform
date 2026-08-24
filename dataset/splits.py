from __future__ import annotations

import random
from typing import Any

LOCKED_SPLITS = frozenset({"final_holdout"})
FORBIDDEN_CALIBRATION_SPLITS = frozenset({"test", "final_holdout"})


def assign_person_splits(
    *,
    person_ids: list[str],
    train_ratio: float,
    validation_ratio: float,
    test_ratio: float,
    seed: int,
    final_holdout_ratio: float = 0.0,
) -> dict[str, list[str]]:
    total_ratio = train_ratio + validation_ratio + test_ratio + final_holdout_ratio
    if abs(total_ratio - 1.0) > 1e-6:
        raise ValueError(f"Split ratios must sum to 1.0, got {total_ratio:.4f}")

    rng = random.Random(seed)
    shuffled = list(person_ids)
    rng.shuffle(shuffled)

    count = len(shuffled)
    train_end = int(count * train_ratio)
    validation_end = train_end + int(count * validation_ratio)
    test_end = validation_end + int(count * test_ratio)

    splits = {
        "train": shuffled[:train_end],
        "validation": shuffled[train_end:validation_end],
        "test": shuffled[validation_end:test_end],
    }
    if final_holdout_ratio > 0:
        splits["final_holdout"] = shuffled[test_end:]
    else:
        splits["test"].extend(shuffled[test_end:])
    return splits


def _pair_person_units(
    person_ids: list[str],
    hard_negative_pairs: list[tuple[str, str]] | None,
) -> list[frozenset[str]]:
    parent: dict[str, str] = {person_id: person_id for person_id in person_ids}

    def find(node: str) -> str:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    def union(left: str, right: str) -> None:
        root_left = find(left)
        root_right = find(right)
        if root_left != root_right:
            parent[root_right] = root_left

    for person_id in person_ids:
        parent.setdefault(person_id, person_id)

    for left, right in hard_negative_pairs or []:
        if left in parent and right in parent:
            union(left, right)

    units: dict[str, set[str]] = {}
    for person_id in person_ids:
        root = find(person_id)
        units.setdefault(root, set()).add(person_id)
    return [frozenset(unit) for unit in units.values()]


def assign_splits_with_hard_negative_pairs(
    *,
    person_ids: list[str],
    hard_negative_pairs: list[tuple[str, str]] | None,
    train_ratio: float,
    validation_ratio: float,
    test_ratio: float,
    final_holdout_ratio: float,
    seed: int,
    minimum_hard_negative_pairs: dict[str, int] | None = None,
) -> dict[str, list[str]]:
    total_ratio = train_ratio + validation_ratio + test_ratio + final_holdout_ratio
    if abs(total_ratio - 1.0) > 1e-6:
        raise ValueError(f"Split ratios must sum to 1.0, got {total_ratio:.4f}")

    units = _pair_person_units(person_ids, hard_negative_pairs)
    rng = random.Random(seed)

    eval_splits = ["validation", "test"]
    if final_holdout_ratio > 0:
        eval_splits.append("final_holdout")

    minimums = minimum_hard_negative_pairs or {}
    pair_units = [unit for unit in units if len(unit) > 1]
    single_units = [unit for unit in units if len(unit) == 1]

    split_units: dict[str, list[frozenset[str]]] = {
        "train": [],
        "validation": [],
        "test": [],
    }
    if final_holdout_ratio > 0:
        split_units["final_holdout"] = []

    rng.shuffle(pair_units)
    rng.shuffle(single_units)

    for split_name in eval_splits:
        required = int(minimums.get(split_name, 0))
        for _ in range(required):
            if not pair_units:
                break
            split_units[split_name].append(pair_units.pop())

    remaining_units = pair_units + single_units
    rng.shuffle(remaining_units)

    count = len(remaining_units)
    train_end = int(count * train_ratio)
    validation_end = train_end + int(count * validation_ratio)
    test_end = validation_end + int(count * test_ratio)

    buckets = {
        "train": remaining_units[:train_end],
        "validation": split_units["validation"] + remaining_units[train_end:validation_end],
        "test": split_units["test"] + remaining_units[validation_end:test_end],
    }
    if final_holdout_ratio > 0:
        buckets["final_holdout"] = split_units["final_holdout"] + remaining_units[test_end:]
    else:
        buckets["test"].extend(remaining_units[test_end:])

    splits = {name: [] for name in buckets}
    for split_name, unit_list in buckets.items():
        for unit in unit_list:
            splits[split_name].extend(sorted(unit))
    return splits


def validate_no_split_leakage(splits: dict[str, list[str]]) -> None:
    seen: dict[str, str] = {}
    for split_name, ids in splits.items():
        for person_id in ids:
            if person_id in seen:
                raise ValueError(
                    f"Person {person_id} appears in both {seen[person_id]} and {split_name}"
                )
            seen[person_id] = split_name


def validate_hard_negative_pair_atomicity(
    *,
    splits: dict[str, list[str]],
    hard_negative_pairs: list[tuple[str, str]],
) -> None:
    split_by_person = {
        person_id: split_name
        for split_name, person_ids in splits.items()
        for person_id in person_ids
    }
    for left, right in hard_negative_pairs:
        left_split = split_by_person.get(left)
        right_split = split_by_person.get(right)
        if left_split is None or right_split is None:
            continue
        if left_split != right_split:
            raise ValueError(
                f"Hard-negative pair ({left}, {right}) spans splits {left_split} and {right_split}"
            )


def validate_hard_negative_coverage(
    *,
    splits: dict[str, list[str]],
    hard_negative_pairs: list[tuple[str, str]],
    minimum_per_split: dict[str, int] | None = None,
) -> None:
    minimums = minimum_per_split or {}
    split_sets = {name: set(values) for name, values in splits.items()}
    counts = {name: 0 for name in splits}
    for left, right in hard_negative_pairs:
        for split_name, person_ids in split_sets.items():
            if left in person_ids and right in person_ids:
                counts[split_name] += 1
    for split_name, minimum in minimums.items():
        if counts.get(split_name, 0) < minimum:
            raise ValueError(
                f"Split '{split_name}' has {counts.get(split_name, 0)} hard-negative pairs; "
                f"minimum required is {minimum}"
            )


def build_split_metadata(
    *,
    person_ids: list[str],
    split_config: dict[str, Any],
    hard_negative_pairs: list[tuple[str, str]] | None = None,
) -> dict[str, list[str]]:
    final_holdout_ratio = float(split_config.get("final_holdout_ratio", 0.0))
    if hard_negative_pairs:
        minimums = split_config.get("min_hard_negative_pairs_per_eval_split")
        minimum_map = (
            {str(key): int(value) for key, value in minimums.items()}
            if isinstance(minimums, dict)
            else None
        )
        splits = assign_splits_with_hard_negative_pairs(
            person_ids=person_ids,
            hard_negative_pairs=hard_negative_pairs,
            train_ratio=float(split_config.get("train_ratio", 0.70)),
            validation_ratio=float(split_config.get("validation_ratio", 0.15)),
            test_ratio=float(split_config.get("test_ratio", 0.15)),
            final_holdout_ratio=final_holdout_ratio,
            seed=int(split_config.get("holdout_seed", 99)),
            minimum_hard_negative_pairs=minimum_map,
        )
    else:
        splits = assign_person_splits(
            person_ids=person_ids,
            train_ratio=float(split_config.get("train_ratio", 0.70)),
            validation_ratio=float(split_config.get("validation_ratio", 0.15)),
            test_ratio=float(split_config.get("test_ratio", 0.15)),
            final_holdout_ratio=final_holdout_ratio,
            seed=int(split_config.get("holdout_seed", 99)),
        )

    validate_no_split_leakage(splits)
    if hard_negative_pairs:
        validate_hard_negative_pair_atomicity(
            splits=splits,
            hard_negative_pairs=hard_negative_pairs,
        )
        minimums = split_config.get("min_hard_negative_pairs_per_eval_split")
        if isinstance(minimums, dict):
            validate_hard_negative_coverage(
                splits=splits,
                hard_negative_pairs=hard_negative_pairs,
                minimum_per_split={str(key): int(value) for key, value in minimums.items()},
            )
    return splits
