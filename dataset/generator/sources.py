from __future__ import annotations

import csv
import random
from pathlib import Path
from typing import Any

from dataset.config import CANONICAL_FIELDS
from dataset.corruption.operators import corrupt_record_fields
from dataset.manifest import (
    CorruptionRecord,
    DuplicateGroup,
    MatchPair,
    SourceRecord,
)

DATA_FIELDS = tuple(field for field in CANONICAL_FIELDS if field != "person_id")

SOURCE_A_COLUMNS = [
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

SOURCE_B_COLUMN_SETS = [
    [
        "source_record_id",
        "source_name",
        "ad",
        "soyad",
        "e_mail",
        "gsm",
        "sirket",
        "sehir",
        "ilce",
        "adres",
        "legacy_code",
    ],
    [
        "source_name",
        "source_record_id",
        "given_name",
        "surname",
        "email_address",
        "mobile",
        "organization",
        "il",
        "mahalle",
        "street",
        "import_batch",
        "notes",
    ],
    [
        "source_record_id",
        "first_name",
        "last_name",
        "email",
        "cep_telefonu",
        "company",
        "city",
        "district",
        "address",
        "source_name",
        "legacy_code",
    ],
]

SOURCE_B_UNMAPPED_COLUMNS = frozenset(
    {
        "legacy_code",
        "import_batch",
        "notes",
    }
)

SOURCE_B_FIELD_MAP = {
    "ad": "first_name",
    "given_name": "first_name",
    "first_name": "first_name",
    "soyad": "last_name",
    "surname": "last_name",
    "last_name": "last_name",
    "e_mail": "email",
    "email_address": "email",
    "email": "email",
    "gsm": "phone",
    "mobile": "phone",
    "cep_telefonu": "phone",
    "sirket": "company",
    "organization": "company",
    "company": "company",
    "sehir": "city",
    "il": "city",
    "city": "city",
    "ilce": "district",
    "mahalle": "district",
    "district": "district",
    "adres": "address",
    "street": "address",
    "address": "address",
}


def source_b_expected_mapping(column: str) -> tuple[str | None, str]:
    """Independent Source B ground truth from generator metadata (not mapper output)."""
    if column in {"source_record_id", "source_name"}:
        return None, "UNMAPPED"
    if column in SOURCE_B_UNMAPPED_COLUMNS:
        return None, "UNMAPPED"
    canonical_field = SOURCE_B_FIELD_MAP.get(column)
    if canonical_field is not None:
        return canonical_field, "AUTO_MAP"
    return None, "UNMAPPED"


def _format_source_record_id(source_name: str, index: int) -> str:
    return f"{source_name}-{index:06d}"


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_canonical_csv(path: Path, records: list[dict[str, Any]]) -> None:
    _write_csv(path, list(CANONICAL_FIELDS), records)


def generate_source_a(
    *,
    canonical_records: list[dict[str, Any]],
    profile: dict[str, object],
    severities: dict[str, str],
    seed: int,
) -> tuple[list[dict[str, Any]], list[SourceRecord], list[CorruptionRecord]]:
    rng = random.Random(seed)
    rows: list[dict[str, Any]] = []
    source_records: list[SourceRecord] = []
    all_corruptions: list[CorruptionRecord] = []

    for index, canonical in enumerate(canonical_records, start=1):
        source_record_id = _format_source_record_id("source_a", index)
        fields, corruptions = corrupt_record_fields(
            canonical=canonical,
            profile=profile,
            severities=severities,
            rng=rng,
            person_id=canonical["person_id"],
            source_record_id=source_record_id,
            source_name="source_a",
            allowed_fields=DATA_FIELDS,
        )
        row = {
            "source_record_id": source_record_id,
            "source_name": "source_a",
            **fields,
        }
        rows.append(row)
        source_records.append(
            SourceRecord(
                person_id=canonical["person_id"],
                source_record_id=source_record_id,
                source_name="source_a",
                fields=fields,
                corruptions=corruptions,
            )
        )
        all_corruptions.extend(corruptions)

    return rows, source_records, all_corruptions


def generate_source_b(
    *,
    canonical_records: list[dict[str, Any]],
    profile: dict[str, object],
    severities: dict[str, str],
    seed: int,
) -> tuple[list[dict[str, Any]], list[SourceRecord], list[CorruptionRecord], list[str]]:
    rng = random.Random(seed)
    column_set = rng.choice(SOURCE_B_COLUMN_SETS)
    rows: list[dict[str, Any]] = []
    source_records: list[SourceRecord] = []
    all_corruptions: list[CorruptionRecord] = []
    missing_rate = float(profile.get("missing_value_rate", 0.12))

    reverse_map: dict[str, str] = {}
    for column in column_set:
        if column in {"source_record_id", "source_name"}:
            continue
        canonical_field = SOURCE_B_FIELD_MAP.get(column, column)
        reverse_map[canonical_field] = column

    for index, canonical in enumerate(canonical_records, start=1):
        source_record_id = _format_source_record_id("source_b", index)
        fields, corruptions = corrupt_record_fields(
            canonical=canonical,
            profile={"field_rates": {}, "corruption_weights": {}, "max_corruptions_per_record": 0},
            severities=severities,
            rng=rng,
            person_id=canonical["person_id"],
            source_record_id=source_record_id,
            source_name="source_b",
            allowed_fields=DATA_FIELDS,
        )

        for field_name, value in list(fields.items()):
            if value is not None and rng.random() < missing_rate:
                before = value
                fields[field_name] = None
                corruptions.append(
                    CorruptionRecord(
                        corruption_type="missing_value",
                        field_name=field_name,
                        before_value=before,
                        after_value=None,
                        severity=severities.get("missing_value", "medium"),
                        person_id=canonical["person_id"],
                        source_record_id=source_record_id,
                        source_name="source_b",
                    )
                )

        row: dict[str, Any] = {
            "source_record_id": source_record_id,
            "source_name": "source_b",
        }
        for canonical_field, value in fields.items():
            column = reverse_map.get(canonical_field, canonical_field)
            row[column] = value

        if "legacy_code" in column_set:
            row["legacy_code"] = f"LEG-{index:05d}"
        if "import_batch" in column_set:
            row["import_batch"] = f"BATCH-{index % 17:03d}"
        if "notes" in column_set:
            row["notes"] = "imported record"

        rows.append(row)
        source_records.append(
            SourceRecord(
                person_id=canonical["person_id"],
                source_record_id=source_record_id,
                source_name="source_b",
                fields=fields,
                corruptions=corruptions,
            )
        )
        all_corruptions.extend(corruptions)

    return rows, source_records, all_corruptions, column_set


def generate_source_c(
    *,
    canonical_records: list[dict[str, Any]],
    profile: dict[str, object],
    severities: dict[str, str],
    seed: int,
) -> tuple[
    list[dict[str, Any]],
    list[SourceRecord],
    list[CorruptionRecord],
    list[DuplicateGroup],
    list[MatchPair],
]:
    rng = random.Random(seed)
    rows: list[dict[str, Any]] = []
    source_records: list[SourceRecord] = []
    all_corruptions: list[CorruptionRecord] = []
    duplicate_groups: list[DuplicateGroup] = []
    positive_pairs: list[MatchPair] = []

    duplicate_rate = float(profile.get("duplicate_rate", 0.08))
    next_index = 1

    for canonical in canonical_records:
        source_record_id = _format_source_record_id("source_c", next_index)
        next_index += 1

        fields, corruptions = corrupt_record_fields(
            canonical=canonical,
            profile=profile,
            severities=severities,
            rng=rng,
            person_id=canonical["person_id"],
            source_record_id=source_record_id,
            source_name="source_c",
            allowed_fields=DATA_FIELDS,
        )

        row = {
            "source_record_id": source_record_id,
            "source_name": "source_c",
            **fields,
        }
        rows.append(row)
        source_records.append(
            SourceRecord(
                person_id=canonical["person_id"],
                source_record_id=source_record_id,
                source_name="source_c",
                fields=fields,
                corruptions=corruptions,
            )
        )
        all_corruptions.extend(corruptions)

        if rng.random() < duplicate_rate:
            dup_source_record_id = _format_source_record_id("source_c", next_index)
            next_index += 1
            dup_fields, dup_corruptions = corrupt_record_fields(
                canonical=canonical,
                profile=profile,
                severities=severities,
                rng=rng,
                person_id=canonical["person_id"],
                source_record_id=dup_source_record_id,
                source_name="source_c",
                allowed_fields=DATA_FIELDS,
            )
            dup_row = {
                "source_record_id": dup_source_record_id,
                "source_name": "source_c",
                **dup_fields,
            }
            rows.append(dup_row)
            source_records.append(
                SourceRecord(
                    person_id=canonical["person_id"],
                    source_record_id=dup_source_record_id,
                    source_name="source_c",
                    fields=dup_fields,
                    corruptions=dup_corruptions,
                )
            )
            all_corruptions.extend(dup_corruptions)
            all_corruptions.append(
                CorruptionRecord(
                    corruption_type="duplicate",
                    field_name="*",
                    before_value=source_record_id,
                    after_value=dup_source_record_id,
                    severity=severities.get("duplicate", "high"),
                    person_id=canonical["person_id"],
                    source_record_id=dup_source_record_id,
                    source_name="source_c",
                )
            )
            duplicate_groups.append(
                DuplicateGroup(
                    person_id=canonical["person_id"],
                    source_record_ids=[source_record_id, dup_source_record_id],
                    source_name="source_c",
                )
            )
            positive_pairs.append(
                MatchPair(
                    person_id_a=canonical["person_id"],
                    person_id_b=canonical["person_id"],
                    source_record_id_a=source_record_id,
                    source_record_id_b=dup_source_record_id,
                    source_name="source_c",
                    pair_type="duplicate",
                )
            )

    return rows, source_records, all_corruptions, duplicate_groups, positive_pairs


def write_source_csv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    _write_csv(path, columns, rows)
