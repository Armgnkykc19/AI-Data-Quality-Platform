from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path

from dataset.manifest import MatchPair


@dataclass(frozen=True)
class EvaluationGroundTruth:
    person_mappings: dict[str, str]
    duplicate_groups: tuple[dict[str, object], ...]
    positive_pairs: tuple[MatchPair, ...]
    hard_negative_pairs: tuple[MatchPair, ...]
    splits: dict[str, list[str]]


@dataclass(frozen=True)
class CanonicalOracleRecord:
    person_id: str
    field_values: dict[str, str | None]


def load_evaluation_ground_truth(dataset_path: Path) -> EvaluationGroundTruth:
    summary_path = dataset_path / "ground_truth" / "summary.json"
    if not summary_path.exists():
        raise FileNotFoundError(f"Ground truth summary not found: {summary_path}")

    with summary_path.open("r", encoding="utf-8") as file:
        summary = json.load(file)

    positive_pairs = tuple(MatchPair(**item) for item in summary.get("positive_pairs", []))
    hard_negative_pairs = tuple(
        MatchPair(**item) for item in summary.get("hard_negative_pairs", [])
    )
    duplicate_groups = tuple(summary.get("duplicate_groups", []))
    return EvaluationGroundTruth(
        person_mappings={
            str(key): str(value) for key, value in summary.get("person_mappings", {}).items()
        },
        duplicate_groups=duplicate_groups,
        positive_pairs=positive_pairs,
        hard_negative_pairs=hard_negative_pairs,
        splits={
            str(key): [str(value) for value in values]
            for key, values in summary.get("splits", {}).items()
        },
    )


def load_canonical_oracle(dataset_path: Path) -> dict[str, CanonicalOracleRecord]:
    canonical_path = dataset_path / "clean" / "canonical.csv"
    if not canonical_path.exists():
        raise FileNotFoundError(f"Canonical oracle not found: {canonical_path}")

    oracle: dict[str, CanonicalOracleRecord] = {}
    with canonical_path.open("r", encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        for row in reader:
            person_id = str(row["person_id"])
            field_values = {
                key: (value if value != "" else None)
                for key, value in row.items()
                if key != "person_id"
            }
            oracle[person_id] = CanonicalOracleRecord(
                person_id=person_id,
                field_values=field_values,
            )
    return oracle


def pair_key(left_id: str, right_id: str) -> tuple[str, str]:
    if left_id <= right_id:
        return left_id, right_id
    return right_id, left_id


def expected_match(pair: MatchPair) -> bool:
    return pair.person_id_a == pair.person_id_b


def filter_pairs_for_split(
    pairs: tuple[MatchPair, ...],
    *,
    person_mappings: dict[str, str],
    split_person_ids: set[str],
) -> tuple[MatchPair, ...]:
    filtered: list[MatchPair] = []
    for pair in pairs:
        person_a = person_mappings.get(pair.source_record_id_a)
        person_b = person_mappings.get(pair.source_record_id_b)
        if person_a in split_person_ids and person_b in split_person_ids:
            filtered.append(pair)
    return tuple(filtered)
