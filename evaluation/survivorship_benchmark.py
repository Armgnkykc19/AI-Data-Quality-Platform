from __future__ import annotations

import csv
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from entity_resolution.engine import resolve_entities
from entity_resolution.records import build_entity_records_from_quality_result
from entity_resolution.similarity import normalize_email, normalize_phone, normalize_text
from evaluation.ground_truth import (
    EvaluationGroundTruth,
    load_canonical_oracle,
    load_evaluation_ground_truth,
)
from ingestion.parser import parse_file
from record_quality.pipeline import run_quality_pipeline
from survivorship.engine import build_canonical_entities
from survivorship.failure_analysis import (
    classify_conflict_preservation_failure,
    classify_field_mismatch,
    classify_merge_coherence_failure,
)
from survivorship.models import FailureKind

PROJECT_ROOT = Path(__file__).resolve().parents[1]
IDENTITY_FIELDS = (
    "first_name",
    "last_name",
    "email",
    "phone",
    "company",
    "city",
    "district",
    "address",
)


def _normalize_field(field_name: str, value: str | None) -> str | None:
    if field_name == "email":
        return normalize_email(value)
    if field_name == "phone":
        return normalize_phone(value)
    return normalize_text(value)


@dataclass
class SurvivorshipBenchmarkResult:
    split_name: str
    record_count: int = 0
    canonical_entity_count: int = 0
    merged_entity_count: int = 0
    singleton_entity_count: int = 0
    review_excluded_record_count: int = 0
    preserved_conflict_count: int = 0
    cluster_person_purity_total: int = 0
    cluster_person_purity_correct: int = 0
    cluster_person_purity_rate: float = 0.0
    merge_coherence_total: int = 0
    merge_coherence_correct: int = 0
    merge_coherence_rate: float = 0.0
    field_comparisons: int = 0
    field_matches: int = 0
    field_match_rate: float = 0.0
    conflict_cases: int = 0
    conflict_preserved: int = 0
    conflict_preservation_rate: float = 0.0
    failures_by_kind: dict[str, int] = field(default_factory=dict)
    ran_successfully: bool = True
    error_message: str | None = None

    @property
    def passed(self) -> bool:
        return (
            self.ran_successfully
            and self.cluster_person_purity_rate >= 0.99
            and self.merge_coherence_rate >= 0.90
            and self.field_match_rate >= 0.85
            and self.conflict_preservation_rate >= 0.95
        )


def _load_entity_records_from_dataset(dataset_path: Path) -> list:
    records = []
    source_paths: list[Path] = []
    sources_dir = dataset_path / "sources"
    hard_cases_dir = dataset_path / "hard_cases"
    if sources_dir.exists():
        source_paths.extend(sorted(sources_dir.glob("*.csv")))
    if hard_cases_dir.exists():
        source_paths.extend(sorted(hard_cases_dir.glob("*.csv")))

    for source_path in source_paths:
        parsed = parse_file(source_path)
        quality = run_quality_pipeline(parsed)
        records.extend(build_entity_records_from_quality_result(parsed, quality))
    return records


def _records_by_person(
    *,
    record_ids: list[str],
    person_mappings: dict[str, str],
) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = defaultdict(list)
    for record_id in record_ids:
        person_id = person_mappings.get(record_id)
        if person_id is not None:
            grouped[person_id].append(record_id)
    return grouped


def run_survivorship_benchmark(
    *,
    dataset_path: Path,
    split_name: str = "test",
) -> SurvivorshipBenchmarkResult:
    result = SurvivorshipBenchmarkResult(split_name=split_name)
    try:
        ground_truth: EvaluationGroundTruth = load_evaluation_ground_truth(dataset_path)
        split_person_ids = set(ground_truth.splits.get(split_name, []))
        if not split_person_ids:
            raise ValueError(f"Split '{split_name}' not found or empty in ground truth.")

        entity_records = _load_entity_records_from_dataset(dataset_path)
        split_records = [
            record
            for record in entity_records
            if ground_truth.person_mappings.get(record.record_id) in split_person_ids
        ]
        resolution = resolve_entities(split_records, source_label=str(dataset_path))
        survivorship = build_canonical_entities(resolution)

        result.record_count = len(split_records)
        result.canonical_entity_count = survivorship.summary.canonical_entity_count
        result.merged_entity_count = survivorship.summary.merged_entity_count
        result.singleton_entity_count = survivorship.summary.singleton_entity_count
        result.review_excluded_record_count = survivorship.summary.review_excluded_record_count
        result.preserved_conflict_count = survivorship.summary.preserved_conflict_count

        review_excluded = set(survivorship.review_excluded_record_ids)
        split_record_ids = [record.record_id for record in split_records]
        grouped = _records_by_person(
            record_ids=split_record_ids,
            person_mappings=ground_truth.person_mappings,
        )

        failures: Counter[str] = Counter()
        purity_total = purity_correct = 0
        for entity in survivorship.entities:
            if len(entity.member_record_ids) < 2:
                continue
            person_ids = {
                ground_truth.person_mappings.get(record_id)
                for record_id in entity.member_record_ids
            }
            person_ids.discard(None)
            purity_total += 1
            if len(person_ids) <= 1:
                purity_correct += 1
            else:
                failures[FailureKind.SPLIT_ENTITY.value] += 1

        result.cluster_person_purity_total = purity_total
        result.cluster_person_purity_correct = purity_correct
        result.cluster_person_purity_rate = purity_correct / purity_total if purity_total else 1.0

        merge_total = merge_correct = 0

        for person_id, record_ids in grouped.items():
            evaluable = [record_id for record_id in record_ids if record_id not in review_excluded]
            if len(evaluable) < 2:
                continue
            entity_ids = []
            for record_id in evaluable:
                entity = survivorship.entity_for_record(record_id)
                if entity is None:
                    continue
                entity_ids.append(entity.entity_id)
            if len(entity_ids) < 2:
                continue
            merge_total += 1
            failure = classify_merge_coherence_failure(
                person_id=person_id,
                record_ids=tuple(evaluable),
                entity_ids=tuple(entity_ids),
            )
            if failure is None:
                merge_correct += 1
            else:
                failures[failure.value] += 1

        result.merge_coherence_total = merge_total
        result.merge_coherence_correct = merge_correct
        result.merge_coherence_rate = merge_correct / merge_total if merge_total else 1.0

        canonical_oracle = load_canonical_oracle(dataset_path)
        field_comparisons = field_matches = 0
        for entity in survivorship.entities:
            person_ids = {
                ground_truth.person_mappings.get(record_id)
                for record_id in entity.member_record_ids
            }
            person_ids.discard(None)
            if len(person_ids) != 1:
                continue
            person_id = next(iter(person_ids))
            if person_id not in split_person_ids:
                continue
            oracle = canonical_oracle.get(person_id)
            if oracle is None:
                continue
            for field_name in IDENTITY_FIELDS:
                expected = _normalize_field(field_name, oracle.field_values.get(field_name))
                actual = _normalize_field(field_name, entity.field_values.get(field_name))
                field_comparisons += 1
                mismatch = classify_field_mismatch(
                    field_name=field_name,
                    expected_normalized=expected,
                    actual_normalized=actual,
                )
                if mismatch is None:
                    field_matches += 1
                else:
                    failures[mismatch.value] += 1

        result.field_comparisons = field_comparisons
        result.field_matches = field_matches
        result.field_match_rate = field_matches / field_comparisons if field_comparisons else 1.0

        conflict_cases = conflict_preserved = 0
        records_by_id = {record.record_id: record for record in split_records}
        for entity in survivorship.entities:
            if len(entity.member_record_ids) < 2:
                continue
            for field_name in IDENTITY_FIELDS:
                collected = [
                    _normalize_field(field_name, records_by_id[record_id].get(field_name))
                    for record_id in entity.member_record_ids
                    if record_id in records_by_id
                ]
                distinct = {value for value in collected if value is not None}
                if len(distinct) <= 1:
                    continue
                conflict_cases += 1
                failure = classify_conflict_preservation_failure(
                    field_name=field_name,
                    normalized_values=tuple(sorted(distinct)),
                    preserved_conflicts=entity.preserved_conflicts,
                )
                if failure is None:
                    conflict_preserved += 1
                else:
                    failures[failure.value] += 1

        result.conflict_cases = conflict_cases
        result.conflict_preserved = conflict_preserved
        result.conflict_preservation_rate = (
            conflict_preserved / conflict_cases if conflict_cases else 1.0
        )
        result.failures_by_kind = dict(sorted(failures.items()))
        return result
    except (OSError, ValueError, KeyError, csv.Error) as exc:
        result.ran_successfully = False
        result.error_message = str(exc)
        return result
