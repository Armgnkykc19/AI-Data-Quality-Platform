#!/usr/bin/env python3
"""Evaluation-only full-scale entity resolution audit. Not used in production."""
from __future__ import annotations

import json
import statistics
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from entity_resolution.blocking import (
    _build_blocking_key,
    _pairs_from_bucket,
    _strategy_reason_type,
    candidate_reduction_ratio,
    generate_candidates,
    possible_pair_count,
)
from entity_resolution.clustering import build_entity_clusters
from entity_resolution.config import load_entity_resolution_config
from entity_resolution.engine import resolve_entities
from entity_resolution.models import MatchDecisionType
from entity_resolution.similarity import normalize_email, normalize_phone, normalize_text
from evaluation.entity_resolution_benchmark import (
    _candidate_pair_set,
    _decision_map,
    _load_entity_records_from_dataset,
    run_entity_resolution_benchmark,
)
from evaluation.ground_truth import (
    filter_pairs_for_split,
    load_evaluation_ground_truth,
    pair_key,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = PROJECT_ROOT / "datasets" / "golden" / "v0.1.0"


def _classify_candidate_miss(
    left_id: str,
    right_id: str,
    records_by_id: dict,
) -> str:
    left = records_by_id[left_id]
    right = records_by_id[right_id]

    left_email = normalize_email(left.get("email"))
    right_email = normalize_email(right.get("email"))
    left_phone = normalize_phone(left.get("phone"))
    right_phone = normalize_phone(right.get("phone"))

    if left_email is None or right_email is None:
        if left_phone is None or right_phone is None:
            return "MISSING_VALUES"
        return "PHONE_CORRUPTED_OR_CHANGED"
    if left_email != right_email:
        if left_phone and right_phone and left_phone == right_phone:
            return "EMAIL_CORRUPTED"
        return "EMAIL_CORRUPTED"

    if left_phone is None or right_phone is None:
        return "MISSING_VALUES"
    if left_phone != right_phone:
        return "PHONE_CORRUPTED_OR_CHANGED"

    left_name = normalize_text(left.get("last_name"))
    right_name = normalize_text(right.get("last_name"))
    left_city = normalize_text(left.get("city"))
    right_city = normalize_text(right.get("city"))
    if (
        left_name
        and right_name
        and left_name == right_name
        and left_city
        and right_city
        and left_city == right_city
    ):
        return "NO_BLOCKING_KEY"
    return "UNSUPPORTED_VARIATION"


def blocking_strategy_breakdown(
    records: list,
    config,
) -> dict[str, Any]:
    per_strategy_pairs: dict[str, set[tuple[str, str]]] = {}
    for strategy in config.blocking_strategies:
        buckets: dict[str, list[str]] = {}
        reason_type = _strategy_reason_type(strategy)
        for record in records:
            key = _build_blocking_key(record, strategy.fields, config=config)
            if key is None:
                continue
            buckets.setdefault(key, []).append(record.record_id)

        pairs: set[tuple[str, str]] = set()
        for blocking_key, record_ids in buckets.items():
            for candidate in _pairs_from_bucket(
                record_ids,
                reason_type=reason_type,
                blocking_key=blocking_key,
            ):
                pairs.add((candidate.pair.record_a_id, candidate.pair.record_b_id))
        per_strategy_pairs[strategy.reason] = pairs

    union_pairs: set[tuple[str, str]] = set()
    for pairs in per_strategy_pairs.values():
        union_pairs |= pairs

    overlap_counts: Counter[int] = Counter()
    pair_strategy_count: dict[tuple[str, str], int] = defaultdict(int)
    for pairs in per_strategy_pairs.values():
        for pair in pairs:
            pair_strategy_count[pair] += 1
    for count in pair_strategy_count.values():
        overlap_counts[count] += 1

    return {
        "pairs_by_strategy": {
            name: len(pairs) for name, pairs in sorted(per_strategy_pairs.items())
        },
        "union_pair_count": len(union_pairs),
        "overlap_histogram": dict(sorted(overlap_counts.items())),
    }


def cluster_entity_metrics(
    clusters: tuple,
    person_mappings: dict[str, str],
) -> dict[str, Any]:
    false_merged = 0
    split_entities = 0
    person_to_clusters: dict[str, set[str]] = defaultdict(set)

    for cluster in clusters:
        person_ids = {
            person_mappings.get(record_id)
            for record_id in cluster.member_record_ids
            if person_mappings.get(record_id)
        }
        if len(person_ids) > 1:
            false_merged += 1
        for person_id in person_ids:
            person_to_clusters[person_id].add(cluster.cluster_id)

    for _person_id, cluster_ids in person_to_clusters.items():
        if len(cluster_ids) > 1:
            split_entities += 1

    sizes = [len(cluster.member_record_ids) for cluster in clusters]
    return {
        "cluster_count": len(clusters),
        "largest_cluster_size": max(sizes) if sizes else 0,
        "false_merged_clusters": false_merged,
        "split_true_entities": split_entities,
        "clusters_with_internal_conflict_flag": sum(
            1 for cluster in clusters if cluster.has_internal_conflict
        ),
    }


def run_full_audit(
    *,
    dataset_path: Path = DEFAULT_DATASET,
    split_name: str = "test",
) -> dict[str, Any]:
    config = load_entity_resolution_config()
    ground_truth = load_evaluation_ground_truth(dataset_path)
    split_person_ids = set(ground_truth.splits.get(split_name, []))

    load_start = time.perf_counter()
    all_records = _load_entity_records_from_dataset(dataset_path)
    load_seconds = time.perf_counter() - load_start

    split_records = [
        record
        for record in all_records
        if ground_truth.person_mappings.get(record.record_id) in split_person_ids
    ]
    records_by_id = {record.record_id: record for record in split_records}

    candidate_start = time.perf_counter()
    candidates = generate_candidates(split_records, config=config)
    candidate_seconds = time.perf_counter() - candidate_start

    unique_pairs = {(c.pair.record_a_id, c.pair.record_b_id) for c in candidates}
    duplicate_pairs = len(candidates) - len(unique_pairs)

    resolve_start = time.perf_counter()
    resolution = resolve_entities(split_records, source_label=str(dataset_path), config=config)
    resolve_seconds = time.perf_counter() - resolve_start

    benchmark = run_entity_resolution_benchmark(
        dataset_path=dataset_path,
        split_name=split_name,
    )

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

    candidate_misses = []
    miss_taxonomy: Counter[str] = Counter()
    for pair in positive_pairs:
        key = pair_key(pair.source_record_id_a, pair.source_record_id_b)
        if key not in candidate_pairs:
            category = _classify_candidate_miss(
                pair.source_record_id_a,
                pair.source_record_id_b,
                records_by_id,
            )
            miss_taxonomy[category] += 1
            candidate_misses.append(
                {
                    "pair_type": pair.pair_type,
                    "source_record_id_a": pair.source_record_id_a,
                    "source_record_id_b": pair.source_record_id_b,
                    "category": category,
                }
            )

    hard_positive_details = []
    for pair in positive_pairs:
        if pair.pair_type != "hard_positive":
            continue
        key = pair_key(pair.source_record_id_a, pair.source_record_id_b)
        decision = decisions.get(key)
        in_candidates = key in candidate_pairs
        detail: dict[str, Any] = {
            "source_record_id_a": pair.source_record_id_a,
            "source_record_id_b": pair.source_record_id_b,
            "candidate_generated": in_candidates,
            "decision": decision.decision.value if decision else None,
            "score": round(decision.comparison.score, 6) if decision else None,
            "reason": decision.reason if decision else "CANDIDATE_MISS",
        }
        if decision:
            detail["blocking_reasons"] = [
                reason.reason_type.value for reason in decision.comparison.candidate_reasons
            ]
            detail["evidence"] = [
                item.evidence_type.value for item in decision.comparison.evidence
            ]
            detail["conflicts"] = [
                item.conflict_type.value for item in decision.comparison.conflicts
            ]
        hard_positive_details.append(detail)

    hard_negative_details = []
    hn_review = hn_no_match = hn_not_candidate = 0
    false_auto_matches = []
    for pair in negative_pairs:
        key = pair_key(pair.source_record_id_a, pair.source_record_id_b)
        decision = decisions.get(key)
        in_candidates = key in candidate_pairs
        if not in_candidates:
            hn_not_candidate += 1
        elif decision and decision.decision == MatchDecisionType.AUTO_MATCH:
            false_auto_matches.append(
                {
                    "source_record_id_a": pair.source_record_id_a,
                    "source_record_id_b": pair.source_record_id_b,
                    "score": round(decision.comparison.score, 6),
                    "evidence": [e.evidence_type.value for e in decision.comparison.evidence],
                }
            )
        elif decision and decision.decision == MatchDecisionType.REVIEW:
            hn_review += 1
        elif decision and decision.decision == MatchDecisionType.NO_MATCH:
            hn_no_match += 1
        hard_negative_details.append(
            {
                "in_candidates": in_candidates,
                "decision": decision.decision.value if decision else None,
            }
        )

    scores = [decision.comparison.score for decision in resolution.decisions]
    scores_by_decision: dict[str, list[float]] = defaultdict(list)
    for decision in resolution.decisions:
        scores_by_decision[decision.decision.value].append(decision.comparison.score)

    decision_counts = Counter(decision.decision.value for decision in resolution.decisions)
    total_decisions = len(resolution.decisions)

    clusters, conflict_guarded = build_entity_clusters(
        resolution.decisions,
        records_by_id,
        config=config,
    )
    cluster_metrics = cluster_entity_metrics(clusters, ground_truth.person_mappings)

    sum(
        1
        for pair in positive_pairs
        if (decision := decisions.get(
            pair_key(pair.source_record_id_a, pair.source_record_id_b)
        ))
        is not None
        and decision.decision == MatchDecisionType.AUTO_MATCH
    )

    return {
        "dataset": {
            "path": str(dataset_path),
            "split": split_name,
            "split_person_count": len(split_person_ids),
            "total_person_mappings": len(ground_truth.person_mappings),
            "total_positive_pairs_all_splits": len(ground_truth.positive_pairs),
            "total_hard_negative_pairs_all_splits": len(ground_truth.hard_negative_pairs),
        },
        "records": {
            "all_loaded_records": len(all_records),
            "split_record_count": len(split_records),
            "load_seconds": round(load_seconds, 3),
        },
        "candidate_generation": {
            "possible_all_pairs": possible_pair_count(len(split_records)),
            "generated_candidate_pairs": len(candidates),
            "candidate_reduction_ratio": round(
                candidate_reduction_ratio(
                    record_count=len(split_records),
                    candidate_count=len(candidates),
                ),
                6,
            ),
            "candidate_recall_on_labeled_positives": round(benchmark.candidate_recall, 6),
            "candidate_miss_count": benchmark.candidate_miss_count,
            "duplicate_candidate_pairs": duplicate_pairs,
            "candidate_generation_seconds": round(candidate_seconds, 3),
            "full_resolution_seconds": round(resolve_seconds, 3),
        },
        "blocking_breakdown": blocking_strategy_breakdown(split_records, config),
        "candidate_miss_taxonomy": dict(sorted(miss_taxonomy.items())),
        "candidate_miss_examples": candidate_misses[:20],
        "hard_positives": {
            "total": benchmark.hard_positive_total,
            "auto_match": benchmark.hard_positive_auto_match,
            "review": benchmark.hard_positive_review,
            "missed": benchmark.hard_positive_missed,
            "details": hard_positive_details,
        },
        "hard_negatives": {
            "total": benchmark.hard_negative_total,
            "in_candidate_set": benchmark.hard_negative_total - hn_not_candidate,
            "not_candidates": hn_not_candidate,
            "auto_match": benchmark.hard_negative_false_auto_match,
            "review": hn_review,
            "no_match": hn_no_match,
            "false_auto_match_details": false_auto_matches,
        },
        "decision_distribution": {
            "auto_match": decision_counts.get("AUTO_MATCH", 0),
            "review": decision_counts.get("REVIEW", 0),
            "no_match": decision_counts.get("NO_MATCH", 0),
            "auto_match_rate": round(
                decision_counts.get("AUTO_MATCH", 0) / total_decisions, 6
            )
            if total_decisions
            else 0.0,
            "review_rate": round(decision_counts.get("REVIEW", 0) / total_decisions, 6)
            if total_decisions
            else 0.0,
            "no_match_rate": round(decision_counts.get("NO_MATCH", 0) / total_decisions, 6)
            if total_decisions
            else 0.0,
        },
        "score_distribution": {
            "minimum": round(min(scores), 6) if scores else None,
            "median": round(statistics.median(scores), 6) if scores else None,
            "p90": round(statistics.quantiles(scores, n=10)[8], 6) if len(scores) >= 10 else None,
            "p95": round(statistics.quantiles(scores, n=20)[18], 6) if len(scores) >= 20 else None,
            "maximum": round(max(scores), 6) if scores else None,
            "by_decision": {
                name: {
                    "count": len(values),
                    "min": round(min(values), 6),
                    "median": round(statistics.median(values), 6),
                    "max": round(max(values), 6),
                }
                for name, values in sorted(scores_by_decision.items())
            },
        },
        "pair_metrics_auto_match_as_positive": {
            "true_positives": benchmark.true_positives,
            "false_positives": benchmark.false_positives,
            "false_negatives": benchmark.false_negatives,
            "true_negatives": benchmark.true_negatives,
            "precision": round(benchmark.precision, 6),
            "recall": round(benchmark.recall, 6),
            "f1": round(benchmark.f1, 6),
            "semantics": (
                "Positive prediction = AUTO_MATCH only among labeled candidate pairs "
                "from ground-truth positive and hard-negative sets in split."
            ),
        },
        "auto_match_metrics": {
            "auto_match_total": benchmark.auto_match_total,
            "auto_match_correct": benchmark.auto_match_correct,
            "auto_match_incorrect": benchmark.auto_match_incorrect,
            "auto_match_precision": round(benchmark.auto_match_precision, 6),
            "auto_match_recall_on_labeled_positives": round(benchmark.recall, 6),
            "auto_match_coverage_on_labeled_positives": round(
                benchmark.auto_match_coverage, 6
            ),
            "denominators": {
                "auto_match_precision": "correct AUTO_MATCH / all AUTO_MATCH in split",
                "auto_match_recall_on_labeled_positives": (
                    "AUTO_MATCH on labeled positive pairs / all labeled positive pairs"
                ),
                "auto_match_coverage_on_labeled_positives": (
                    "same as auto_match_recall_on_labeled_positives; "
                    "coverage label retained for reporting compatibility"
                ),
            },
        },
        "false_match_metric": {
            "formula": "incorrect AUTO_MATCH / all AUTO_MATCH in split",
            "value": round(benchmark.false_match_rate, 6),
            "not_equivalent_to_fixture": (
                "Sprint 01 fixture false_merge_rate uses different semantics"
            ),
        },
        "no_match_investigation": {
            "no_match_among_candidates": benchmark.no_match_count,
            "interpretation": (
                "Blocking generates plausible pairs; low scores still often exceed "
                "review threshold when email/phone conflicts route to REVIEW."
            ),
        },
        "clustering": {
            **cluster_metrics,
            "conflict_guard_blocked_unions": conflict_guarded,
        },
        "benchmark_passed": benchmark.passed,
    }


if __name__ == "__main__":
    report = run_full_audit()
    print(json.dumps(report, indent=2, ensure_ascii=False))
