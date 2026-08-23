from __future__ import annotations

import csv
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from entity_resolution.engine import resolve_entities
from entity_resolution.failure_analysis import classify_pair_failure
from entity_resolution.models import MatchDecisionType
from entity_resolution.records import build_entity_records_from_quality_result
from evaluation.ground_truth import (
    EvaluationGroundTruth,
    filter_pairs_for_split,
    load_evaluation_ground_truth,
    pair_key,
)
from evaluation.metrics.classification import calculate_classification_metrics
from ingestion.parser import parse_file
from record_quality.pipeline import run_quality_pipeline

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@dataclass
class EntityResolutionBenchmarkResult:
    split_name: str
    record_count: int = 0
    possible_pair_count: int = 0
    candidate_pair_count: int = 0
    candidate_reduction_ratio: float = 0.0
    labeled_positive_pairs: int = 0
    labeled_negative_pairs: int = 0
    candidate_recall: float = 0.0
    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0
    true_negatives: int = 0
    precision: float = 0.0
    recall: float = 0.0
    f1: float = 0.0
    auto_match_total: int = 0
    auto_match_correct: int = 0
    auto_match_incorrect: int = 0
    auto_match_precision: float = 0.0
    auto_match_coverage: float = 0.0
    review_count: int = 0
    no_match_count: int = 0
    false_match_rate: float = 0.0
    hard_positive_total: int = 0
    hard_positive_auto_match: int = 0
    hard_positive_review: int = 0
    hard_positive_missed: int = 0
    hard_negative_total: int = 0
    hard_negative_false_auto_match: int = 0
    hard_negative_correct_no_match: int = 0
    candidate_miss_count: int = 0
    cluster_count: int = 0
    failures_by_kind: dict[str, int] = field(default_factory=dict)
    ran_successfully: bool = True
    error_message: str | None = None

    @property
    def auto_match_recall_on_labeled_positives(self) -> float:
        """AUTO_MATCH decisions on labeled positive pairs / all labeled positive pairs."""
        return self.auto_match_coverage

    @property
    def passed(self) -> bool:
        return (
            self.auto_match_incorrect == 0
            and self.candidate_recall >= 0.94
            and self.auto_match_precision >= 0.99
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


def _candidate_pair_set(result) -> set[tuple[str, str]]:
    return {
        (candidate.pair.record_a_id, candidate.pair.record_b_id)
        for candidate in result.candidates
    }


def _decision_map(result) -> dict[tuple[str, str], object]:
    mapping = {}
    for decision in result.decisions:
        key = (decision.pair.record_a_id, decision.pair.record_b_id)
        mapping[key] = decision
    return mapping


def run_entity_resolution_benchmark(
    *,
    dataset_path: Path,
    split_name: str = "test",
) -> EntityResolutionBenchmarkResult:
    result = EntityResolutionBenchmarkResult(split_name=split_name)
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

        result.record_count = resolution.summary.record_count
        result.possible_pair_count = resolution.summary.possible_pair_count
        result.candidate_pair_count = resolution.summary.candidate_pair_count
        result.candidate_reduction_ratio = resolution.summary.candidate_reduction_ratio
        result.review_count = resolution.summary.review_count
        result.no_match_count = resolution.summary.no_match_count
        result.cluster_count = resolution.summary.cluster_count

        candidate_pairs = _candidate_pair_set(resolution)
        decisions = _decision_map(resolution)

        positive_pairs = filter_pairs_for_split(
            ground_truth.positive_pairs,
            person_mappings=ground_truth.person_mappings,
            split_person_ids=split_person_ids,
        )
        negative_pairs = filter_pairs_for_split(
            ground_truth.hard_negative_pairs,
            person_mappings=ground_truth.person_mappings,
            split_person_ids=split_person_ids,
        )

        result.labeled_positive_pairs = len(positive_pairs)
        result.labeled_negative_pairs = len(negative_pairs)

        recalled = 0
        for pair in positive_pairs:
            key = pair_key(pair.source_record_id_a, pair.source_record_id_b)
            if key in candidate_pairs:
                recalled += 1
        result.candidate_recall = (
            recalled / len(positive_pairs) if positive_pairs else 1.0
        )

        tp = fp = fn = tn = 0
        failures: Counter[str] = Counter()
        candidate_misses = 0

        for pair in positive_pairs:
            key = pair_key(pair.source_record_id_a, pair.source_record_id_b)
            decision = decisions.get(key)
            in_candidates = key in candidate_pairs
            if not in_candidates:
                candidate_misses += 1
            failure = classify_pair_failure(
                expected_match=True,
                decision=decision,
                candidate_generated=in_candidates,
            )
            if failure is not None:
                failures[failure.value] += 1

            if decision and decision.decision == MatchDecisionType.AUTO_MATCH:
                tp += 1
            elif decision and decision.decision in {
                MatchDecisionType.REVIEW,
                MatchDecisionType.NO_MATCH,
            }:
                fn += 1
            elif not in_candidates:
                fn += 1

        for pair in negative_pairs:
            key = pair_key(pair.source_record_id_a, pair.source_record_id_b)
            decision = decisions.get(key)
            in_candidates = key in candidate_pairs
            failure = classify_pair_failure(
                expected_match=False,
                decision=decision,
                candidate_generated=in_candidates,
            )
            if failure is not None:
                failures[failure.value] += 1

            if decision and decision.decision == MatchDecisionType.AUTO_MATCH:
                fp += 1
            elif decision and decision.decision in {
                MatchDecisionType.REVIEW,
                MatchDecisionType.NO_MATCH,
            }:
                tn += 1
            elif not in_candidates:
                tn += 1

        metrics = calculate_classification_metrics(
            true_positives=tp,
            false_positives=fp,
            false_negatives=fn,
        )
        result.true_positives = tp
        result.false_positives = fp
        result.false_negatives = fn
        result.true_negatives = tn
        result.precision = metrics.precision
        result.recall = metrics.recall
        result.f1 = metrics.f1
        result.candidate_miss_count = candidate_misses

        auto_total = auto_correct = auto_incorrect = 0
        for decision in resolution.decisions:
            if decision.decision != MatchDecisionType.AUTO_MATCH:
                continue
            auto_total += 1
            person_a = ground_truth.person_mappings.get(decision.pair.record_a_id)
            person_b = ground_truth.person_mappings.get(decision.pair.record_b_id)
            if person_a and person_b and person_a == person_b:
                auto_correct += 1
            else:
                auto_incorrect += 1

        result.auto_match_total = auto_total
        result.auto_match_correct = auto_correct
        result.auto_match_incorrect = auto_incorrect
        result.auto_match_precision = (
            auto_correct / auto_total if auto_total else 1.0
        )
        result.auto_match_coverage = (
            sum(
                1
                for pair in positive_pairs
                if (decision := decisions.get(
                    pair_key(pair.source_record_id_a, pair.source_record_id_b)
                ))
                is not None
                and decision.decision == MatchDecisionType.AUTO_MATCH
            )
            / len(positive_pairs)
            if positive_pairs
            else 0.0
        )
        result.false_match_rate = (
            auto_incorrect / auto_total if auto_total else 0.0
        )

        hard_positive_pairs = [
            pair for pair in positive_pairs if pair.pair_type == "hard_positive"
        ]
        hard_negative_pairs = list(negative_pairs)
        result.hard_positive_total = len(hard_positive_pairs)
        result.hard_negative_total = len(hard_negative_pairs)

        for pair in hard_positive_pairs:
            key = pair_key(pair.source_record_id_a, pair.source_record_id_b)
            decision = decisions.get(key)
            if decision is None or key not in candidate_pairs:
                result.hard_positive_missed += 1
            elif decision.decision == MatchDecisionType.AUTO_MATCH:
                result.hard_positive_auto_match += 1
            elif decision.decision == MatchDecisionType.REVIEW:
                result.hard_positive_review += 1
            else:
                result.hard_positive_missed += 1

        for pair in hard_negative_pairs:
            key = pair_key(pair.source_record_id_a, pair.source_record_id_b)
            decision = decisions.get(key)
            if decision and decision.decision == MatchDecisionType.AUTO_MATCH:
                result.hard_negative_false_auto_match += 1
            elif decision and decision.decision == MatchDecisionType.NO_MATCH:
                result.hard_negative_correct_no_match += 1

        result.failures_by_kind = dict(sorted(failures.items()))
        return result
    except (OSError, ValueError, KeyError, csv.Error) as exc:
        result.ran_successfully = False
        result.error_message = str(exc)
        return result


def run_entity_resolution_candidate_benchmark(
    *,
    dataset_path: Path,
    split_name: str = "test",
) -> EntityResolutionBenchmarkResult:
    return run_entity_resolution_benchmark(
        dataset_path=dataset_path,
        split_name=split_name,
    )
