from __future__ import annotations

import random
from typing import Any

from dataset.corruption.operators import corrupt_record_fields
from dataset.generator.sources import DATA_FIELDS, _format_source_record_id
from dataset.manifest import CorruptionRecord, MatchPair, SourceRecord

HARD_POSITIVE_COLUMNS = [
    "source_record_id",
    "source_name",
    "first_name",
    "last_name",
    "email",
    "phone",
    "company",
    "city",
    "district",
    "address",
]

HARD_NEGATIVE_COLUMNS = HARD_POSITIVE_COLUMNS


def _similarity_score(record_a: dict[str, str], record_b: dict[str, str]) -> float:
    score = 0.0
    if record_a["city"] == record_b["city"]:
        score += 0.25
    if record_a["district"] == record_b["district"]:
        score += 0.20
    if record_a["company"] == record_b["company"]:
        score += 0.25
    if record_a["last_name"] == record_b["last_name"]:
        score += 0.20
    if record_a["first_name"][:2] == record_b["first_name"][:2]:
        score += 0.10
    return score


def generate_hard_positives(
    *,
    canonical_records: list[dict[str, Any]],
    profile: dict[str, object],
    severities: dict[str, str],
    count: int,
    seed: int,
) -> tuple[list[dict[str, Any]], list[SourceRecord], list[CorruptionRecord], list[MatchPair]]:
    rng = random.Random(seed)
    selected = rng.sample(canonical_records, k=min(count, len(canonical_records)))

    rows: list[dict[str, Any]] = []
    source_records: list[SourceRecord] = []
    corruptions: list[CorruptionRecord] = []
    positive_pairs: list[MatchPair] = []

    for index, canonical in enumerate(selected, start=1):
        original_id = _format_source_record_id("hard_positive", index * 2 - 1)
        corrupted_id = _format_source_record_id("hard_positive", index * 2)

        original_fields = {field: canonical[field] for field in DATA_FIELDS}
        corrupted_fields, record_corruptions = corrupt_record_fields(
            canonical=canonical,
            profile=profile,
            severities=severities,
            rng=rng,
            person_id=canonical["person_id"],
            source_record_id=corrupted_id,
            source_name="hard_positive",
            allowed_fields=DATA_FIELDS,
        )

        for field_name in DATA_FIELDS:
            if rng.random() < 0.35:
                corrupted_fields[field_name] = None
                record_corruptions.append(
                    CorruptionRecord(
                        corruption_type="missing_value",
                        field_name=field_name,
                        before_value=original_fields[field_name],
                        after_value=None,
                        severity=severities.get("missing_value", "medium"),
                        person_id=canonical["person_id"],
                        source_record_id=corrupted_id,
                        source_name="hard_positive",
                    )
                )

        original_row = {
            "source_record_id": original_id,
            "source_name": "hard_positive",
            **original_fields,
        }
        corrupted_row = {
            "source_record_id": corrupted_id,
            "source_name": "hard_positive",
            **corrupted_fields,
        }
        rows.extend([original_row, corrupted_row])

        source_records.extend(
            [
                SourceRecord(
                    person_id=canonical["person_id"],
                    source_record_id=original_id,
                    source_name="hard_positive",
                    fields=original_fields,
                    corruptions=[],
                ),
                SourceRecord(
                    person_id=canonical["person_id"],
                    source_record_id=corrupted_id,
                    source_name="hard_positive",
                    fields=corrupted_fields,
                    corruptions=record_corruptions,
                ),
            ]
        )
        corruptions.extend(record_corruptions)
        positive_pairs.append(
            MatchPair(
                person_id_a=canonical["person_id"],
                person_id_b=canonical["person_id"],
                source_record_id_a=original_id,
                source_record_id_b=corrupted_id,
                source_name="hard_positive",
                pair_type="hard_positive",
            )
        )

    return rows, source_records, corruptions, positive_pairs


def generate_hard_negatives(
    *,
    canonical_records: list[dict[str, Any]],
    count: int,
    seed: int,
) -> tuple[list[dict[str, Any]], list[SourceRecord], list[MatchPair]]:
    rng = random.Random(seed)
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    indexed = list(canonical_records)
    seen_pair_keys: set[frozenset[str]] = set()

    def add_pair(left: dict[str, Any], right: dict[str, Any]) -> bool:
        if left["person_id"] == right["person_id"]:
            return False
        key = frozenset({left["person_id"], right["person_id"]})
        if key in seen_pair_keys:
            return False
        seen_pair_keys.add(key)
        pairs.append((left, right))
        return True

    by_city: dict[str, list[dict[str, Any]]] = {}
    for record in indexed:
        by_city.setdefault(record["city"], []).append(record)

    for city_records in by_city.values():
        rng.shuffle(city_records)
        for left in city_records:
            for right in city_records:
                if len(pairs) >= count:
                    break
                if _similarity_score(left, right) >= 0.25:
                    add_pair(left, right)
            if len(pairs) >= count:
                break
        if len(pairs) >= count:
            break

    attempts = 0
    max_attempts = max(count * 200, 1000)
    while len(pairs) < count and attempts < max_attempts:
        left, right = rng.sample(indexed, 2)
        attempts += 1
        if _similarity_score(left, right) >= 0.25:
            add_pair(left, right)

    if len(pairs) < count:
        raise ValueError(
            f"Could not generate enough hard-negative pairs: {len(pairs)} < {count}"
        )

    rows: list[dict[str, Any]] = []
    source_records: list[SourceRecord] = []
    hard_negative_pairs: list[MatchPair] = []

    for index, (left, right) in enumerate(pairs[:count], start=1):
        left_id = _format_source_record_id("hard_negative", index * 2 - 1)
        right_id = _format_source_record_id("hard_negative", index * 2)

        left_fields = {field: left[field] for field in DATA_FIELDS}
        right_fields = {field: right[field] for field in DATA_FIELDS}

        rows.append(
            {
                "source_record_id": left_id,
                "source_name": "hard_negative",
                **left_fields,
            }
        )
        rows.append(
            {
                "source_record_id": right_id,
                "source_name": "hard_negative",
                **right_fields,
            }
        )

        source_records.extend(
            [
                SourceRecord(
                    person_id=left["person_id"],
                    source_record_id=left_id,
                    source_name="hard_negative",
                    fields=left_fields,
                    corruptions=[],
                ),
                SourceRecord(
                    person_id=right["person_id"],
                    source_record_id=right_id,
                    source_name="hard_negative",
                    fields=right_fields,
                    corruptions=[],
                ),
            ]
        )
        hard_negative_pairs.append(
            MatchPair(
                person_id_a=left["person_id"],
                person_id_b=right["person_id"],
                source_record_id_a=left_id,
                source_record_id_b=right_id,
                source_name="hard_negative",
                pair_type="hard_negative",
            )
        )

    return rows, source_records, hard_negative_pairs
