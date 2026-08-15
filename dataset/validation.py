from __future__ import annotations

import csv
import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dataset.config import CANONICAL_FIELDS, load_schema_config
from dataset.generator.malformed import MALFORMED_FIXTURES
from dataset.manifest import compute_file_sha256
from dataset.splits import validate_no_split_leakage


@dataclass(frozen=True)
class ValidationIssue:
    severity: str
    code: str
    message: str


@dataclass(frozen=True)
class ValidationResult:
    passed: bool
    issues: list[ValidationIssue]


PERSON_ID_PATTERN = re.compile(r"^P-\d{6}$")


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def validate_dataset(dataset_path: Path) -> ValidationResult:
    issues: list[ValidationIssue] = []

    manifest_path = dataset_path / "manifest.json"
    if not manifest_path.exists():
        issues.append(
            ValidationIssue("error", "missing_manifest", "manifest.json is required")
        )
        return ValidationResult(passed=False, issues=issues)

    manifest = _load_json(manifest_path)
    expected_counts = manifest.get("expected_counts", {})
    files = manifest.get("files", {})

    for name, metadata in files.items():
        relative = metadata.get("path")
        if not relative:
            continue
        file_path = dataset_path / relative
        if not file_path.exists():
            issues.append(
                ValidationIssue(
                    "error",
                    "missing_file",
                    f"Manifest file missing on disk: {name}",
                )
            )
            continue
        actual_hash = compute_file_sha256(file_path)
        expected_hash = metadata.get("sha256")
        if expected_hash and actual_hash != expected_hash:
            issues.append(
                ValidationIssue(
                    "error",
                    "hash_mismatch",
                    f"Hash mismatch for {name}",
                )
            )

    summary_path = dataset_path / "ground_truth" / "summary.json"
    if not summary_path.exists():
        issues.append(
            ValidationIssue(
                "error",
                "missing_ground_truth",
                "ground_truth/summary.json is required",
            )
        )
        return ValidationResult(passed=False, issues=issues)

    summary = _load_json(summary_path)
    person_mappings: dict[str, str] = summary.get("person_mappings", {})
    duplicate_groups = summary.get("duplicate_groups", [])
    hard_negative_pairs = summary.get("hard_negative_pairs", [])
    splits = summary.get("splits", {})

    try:
        validate_no_split_leakage(splits)
    except ValueError as exc:
        issues.append(ValidationIssue("error", "split_leakage", str(exc)))

    canonical_path = dataset_path / "clean" / "canonical.csv"
    if canonical_path.exists():
        issues.extend(_validate_canonical_csv(canonical_path))

    seen_source_ids: set[str] = set()
    for source_file in ["source_a", "source_b", "source_c"]:
        relative = files.get(source_file, {}).get("path")
        if not relative:
            continue
        source_path = dataset_path / relative
        if source_path.exists():
            issues.extend(
                _validate_source_csv(
                    source_path=source_path,
                    person_mappings=person_mappings,
                    seen_source_ids=seen_source_ids,
                )
            )

    for group in duplicate_groups:
        person_ids = {person_mappings.get(record_id) for record_id in group["source_record_ids"]}
        person_ids.discard(None)
        if len(person_ids) != 1:
            issues.append(
                ValidationIssue(
                    "error",
                    "invalid_duplicate_group",
                    f"Duplicate group does not map to one person: {group}",
                )
            )

    for pair in hard_negative_pairs:
        if pair["person_id_a"] == pair["person_id_b"]:
            issues.append(
                ValidationIssue(
                    "error",
                    "invalid_hard_negative",
                    f"Hard-negative pair references same person: {pair}",
                )
            )

    positive_pairs = summary.get("positive_pairs", [])
    for pair in positive_pairs:
        pair_type = pair.get("pair_type", "")
        if (
            pair_type in {"hard_positive", "duplicate"}
            and pair["person_id_a"] != pair["person_id_b"]
        ):
            issues.append(
                ValidationIssue(
                    "error",
                    "invalid_hard_positive",
                    f"Hard-positive pair references different persons: {pair}",
                )
            )

    hard_cases_dir = dataset_path / "hard_cases"
    if hard_cases_dir.exists():
        issues.extend(
            _validate_hard_cases_csv(
                csv_path=hard_cases_dir / "hard_positives.csv",
                person_mappings=person_mappings,
                pair_type="hard_positive",
                positive_pairs=positive_pairs,
            )
        )
        issues.extend(
            _validate_hard_cases_csv(
                csv_path=hard_cases_dir / "hard_negatives.csv",
                person_mappings=person_mappings,
                pair_type="hard_negative",
                positive_pairs=[],
                hard_negative_pairs=hard_negative_pairs,
            )
        )

    manifest_corruptions = Counter(manifest.get("corruption_counts", {}))
    log_path = dataset_path / "ground_truth" / "corruption_log.jsonl"
    if log_path.exists():
        log_counter: Counter[str] = Counter()
        with log_path.open("r", encoding="utf-8") as file:
            for line in file:
                record = json.loads(line)
                log_counter[record["corruption_type"]] += 1
        if dict(log_counter) != dict(manifest_corruptions):
            issues.append(
                ValidationIssue(
                    "error",
                    "corruption_count_mismatch",
                    "Manifest corruption counts do not match corruption log",
                )
            )

    malformed_dir = dataset_path / "malformed"
    if malformed_dir.exists():
        issues.extend(_validate_malformed_fixtures(malformed_dir))

    schema_path = dataset_path / "schema" / "canonical_schema.json"
    if schema_path.exists():
        try:
            load_schema_config(schema_path)
        except (ValueError, FileNotFoundError) as exc:
            issues.append(
                ValidationIssue("error", "invalid_schema", str(exc))
            )

    for key, expected in expected_counts.items():
        if key.endswith("_records") and key != "corruption_events":
            continue
        summary_expected = summary.get("expected_counts", {})
        if key in summary_expected and summary_expected[key] != expected:
            issues.append(
                ValidationIssue(
                    "error",
                    "count_mismatch",
                    f"Expected count mismatch for {key}",
                )
            )

    passed = not any(issue.severity == "error" for issue in issues)
    return ValidationResult(passed=passed, issues=issues)


def _validate_canonical_csv(path: Path) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    seen_person_ids: set[str] = set()
    seen_emails: set[str] = set()
    seen_phones: set[str] = set()

    with path.open("r", encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        if reader.fieldnames != list(CANONICAL_FIELDS):
            issues.append(
                ValidationIssue(
                    "error",
                    "canonical_header",
                    "Canonical CSV header does not match schema",
                )
            )
        for row in reader:
            person_id = row.get("person_id", "")
            if not PERSON_ID_PATTERN.match(person_id):
                issues.append(
                    ValidationIssue(
                        "error",
                        "invalid_person_id",
                        f"Invalid person_id: {person_id}",
                    )
                )
            if person_id in seen_person_ids:
                issues.append(
                    ValidationIssue(
                        "error",
                        "duplicate_person_id",
                        f"Duplicate person_id in canonical base: {person_id}",
                    )
                )
            seen_person_ids.add(person_id)

            email = row.get("email")
            phone = row.get("phone")
            if email in seen_emails:
                issues.append(
                    ValidationIssue(
                        "error",
                        "duplicate_email",
                        f"Duplicate email in canonical base: {email}",
                    )
                )
            if phone in seen_phones:
                issues.append(
                    ValidationIssue(
                        "error",
                        "duplicate_phone",
                        f"Duplicate phone in canonical base: {phone}",
                    )
                )
            if email:
                seen_emails.add(email)
            if phone:
                seen_phones.add(phone)

            if any(row.get(field) in (None, "") for field in CANONICAL_FIELDS):
                issues.append(
                    ValidationIssue(
                        "error",
                        "unexpected_null",
                        f"Canonical record has missing required field: {person_id}",
                    )
                )

    return issues


def _validate_source_csv(
    *,
    source_path: Path,
    person_mappings: dict[str, str],
    seen_source_ids: set[str],
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    with source_path.open("r", encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        for row in reader:
            source_record_id = row.get("source_record_id")
            if not source_record_id:
                issues.append(
                    ValidationIssue(
                        "error",
                        "missing_source_record_id",
                        f"Missing source_record_id in {source_path.name}",
                    )
                )
                continue

            if source_record_id in seen_source_ids:
                issues.append(
                    ValidationIssue(
                        "error",
                        "duplicate_source_record_id",
                        f"Duplicate source_record_id: {source_record_id}",
                    )
                )
            seen_source_ids.add(source_record_id)

            if source_record_id not in person_mappings:
                issues.append(
                    ValidationIssue(
                        "error",
                        "unmapped_source_record",
                        f"No ground-truth mapping for {source_record_id}",
                    )
                )
                continue

            person_id = person_mappings[source_record_id]
            if not PERSON_ID_PATTERN.match(person_id):
                issues.append(
                    ValidationIssue(
                        "error",
                        "invalid_mapped_person_id",
                        f"Invalid mapped person_id for {source_record_id}",
                    )
                )

            if "person_id" in row:
                issues.append(
                    ValidationIssue(
                        "error",
                        "ground_truth_leakage",
                        "person_id must not appear in source CSV files",
                    )
                )

    return issues


def _validate_hard_cases_csv(
    *,
    csv_path: Path,
    person_mappings: dict[str, str],
    pair_type: str,
    positive_pairs: list[dict[str, object]],
    hard_negative_pairs: list[dict[str, object]] | None = None,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if not csv_path.exists():
        issues.append(
            ValidationIssue(
                "error",
                "missing_hard_cases_csv",
                f"Missing hard cases CSV: {csv_path.name}",
            )
        )
        return issues

    seen_source_ids: set[str] = set()
    with csv_path.open("r", encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        for row in reader:
            source_record_id = row.get("source_record_id")
            if not source_record_id:
                issues.append(
                    ValidationIssue(
                        "error",
                        "missing_source_record_id",
                        f"Missing source_record_id in {csv_path.name}",
                    )
                )
                continue
            if source_record_id in seen_source_ids:
                issues.append(
                    ValidationIssue(
                        "error",
                        "duplicate_source_record_id",
                        f"Duplicate source_record_id in {csv_path.name}: {source_record_id}",
                    )
                )
            seen_source_ids.add(source_record_id)

            if source_record_id not in person_mappings:
                issues.append(
                    ValidationIssue(
                        "error",
                        "unmapped_hard_case_record",
                        f"No ground-truth mapping for hard case record: {source_record_id}",
                    )
                )
            if "person_id" in row and row.get("person_id"):
                issues.append(
                    ValidationIssue(
                        "error",
                        "ground_truth_leakage",
                        f"person_id must not appear in hard cases CSV: {csv_path.name}",
                    )
                )

    if pair_type == "hard_positive":
        for pair in positive_pairs:
            if pair.get("pair_type") != "hard_positive":
                continue
            for source_id in (pair["source_record_id_a"], pair["source_record_id_b"]):
                if source_id not in seen_source_ids:
                    issues.append(
                        ValidationIssue(
                            "error",
                            "missing_hard_positive_record",
                            f"Hard-positive pair references missing CSV record: {source_id}",
                        )
                    )
            if pair["person_id_a"] != pair["person_id_b"]:
                issues.append(
                    ValidationIssue(
                        "error",
                        "invalid_hard_positive",
                        f"Hard-positive pair references different persons: {pair}",
                    )
                )

    if pair_type == "hard_negative" and hard_negative_pairs is not None:
        for pair in hard_negative_pairs:
            for source_id in (pair["source_record_id_a"], pair["source_record_id_b"]):
                if source_id not in seen_source_ids:
                    issues.append(
                        ValidationIssue(
                            "error",
                            "missing_hard_negative_record",
                            f"Hard-negative pair references missing CSV record: {source_id}",
                        )
                    )
            if pair["person_id_a"] == pair["person_id_b"]:
                issues.append(
                    ValidationIssue(
                        "error",
                        "invalid_hard_negative",
                        f"Hard-negative pair references same person: {pair}",
                    )
                )

    return issues


def _validate_malformed_fixtures(malformed_dir: Path) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for filename, spec in MALFORMED_FIXTURES.items():
        path = malformed_dir / filename
        if not path.exists():
            issues.append(
                ValidationIssue(
                    "error",
                    "missing_malformed_fixture",
                    f"Missing malformed fixture: {filename}",
                )
            )
            continue
        expected_category = str(spec["category"])
        readme = malformed_dir / "README.md"
        if readme.exists() and expected_category not in readme.read_text(encoding="utf-8"):
            issues.append(
                ValidationIssue(
                    "warning",
                    "undocumented_malformed_fixture",
                    f"Fixture category not documented: {filename}",
                )
            )
    return issues
