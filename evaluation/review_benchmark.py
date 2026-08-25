from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from entity_resolution.config import load_entity_resolution_config
from entity_resolution.engine import resolve_entities
from entity_resolution.models import RecordPair
from evaluation.ground_truth import EvaluationGroundTruth, load_evaluation_ground_truth
from evaluation.survivorship_benchmark import _load_entity_records_from_dataset
from human_review.cases import generate_review_cases
from human_review.errors import HumanReviewContradictionError
from human_review.models import HumanReviewDecision, ReviewCase
from human_review.workflow import ReviewWorkflow
from survivorship.engine import build_canonical_entities


@dataclass
class ReviewBenchmarkResult:
    split_name: str
    review_case_count: int = 0
    pending_case_count: int = 0
    deferred_case_count: int = 0
    unique_records_in_review: int = 0
    review_cases_per_1000_records: float = 0.0
    records_in_multiple_review_cases: int = 0
    max_review_cases_for_single_record: int = 0
    largest_review_case_component: int = 0
    labeled_review_pairs: int = 0
    oracle_match_decisions: int = 0
    oracle_no_match_decisions: int = 0
    oracle_defer_decisions: int = 0
    oracle_simulated_resolution_application_accuracy: float = 0.0
    oracle_simulated_match_application_safety_rate: float = 1.0
    duplicate_membership_violations: int = 0
    unresolved_unsafe_merge_violations: int = 0
    contradiction_count: int = 0
    authorization_blocked_oracle_matches: int = 0
    messages: list[str] = field(default_factory=list)
    ran_successfully: bool = True
    error_message: str | None = None

    @property
    def passed(self) -> bool:
        return (
            self.ran_successfully
            and self.oracle_simulated_match_application_safety_rate >= 1.0
            and self.duplicate_membership_violations == 0
            and self.unresolved_unsafe_merge_violations == 0
            and self.contradiction_count == 0
        )


def _pair_person_truth(
    pair: RecordPair,
    *,
    ground_truth: EvaluationGroundTruth,
) -> tuple[str | None, str | None]:
    person_a = ground_truth.person_mappings.get(pair.record_a_id)
    person_b = ground_truth.person_mappings.get(pair.record_b_id)
    return person_a, person_b


def _oracle_decision_for_review_pair(
    pair: RecordPair,
    *,
    ground_truth: EvaluationGroundTruth,
) -> HumanReviewDecision | None:
    person_a, person_b = _pair_person_truth(pair, ground_truth=ground_truth)
    if person_a is None or person_b is None:
        return None
    if person_a == person_b:
        return HumanReviewDecision.MATCH
    return HumanReviewDecision.NO_MATCH


def _membership_violations(result) -> int:
    seen: dict[str, str] = {}
    violations = 0
    for entity in result.entities:
        for record_id in entity.member_record_ids:
            if record_id in seen and seen[record_id] != entity.entity_id:
                violations += 1
            seen[record_id] = entity.entity_id
    return violations


def _review_workload_stats(
    cases: tuple[ReviewCase, ...],
    *,
    record_count: int,
) -> dict[str, int | float]:
    record_case_counts: Counter[str] = Counter()
    for case in cases:
        record_case_counts[case.pair.record_a_id] += 1
        record_case_counts[case.pair.record_b_id] += 1

    parent = {record_id: record_id for record_id in record_case_counts}

    def find(node: str) -> str:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    def union(left: str, right: str) -> None:
        root_left = find(left)
        root_right = find(right)
        if root_left == root_right:
            return
        if root_left < root_right:
            parent[root_right] = root_left
        else:
            parent[root_left] = root_right

    for case in cases:
        union(case.pair.record_a_id, case.pair.record_b_id)

    components: dict[str, list[str]] = defaultdict(list)
    for record_id in record_case_counts:
        components[find(record_id)].append(record_id)

    largest_component = max((len(values) for values in components.values()), default=0)
    cases_per_1000 = (len(cases) / record_count * 1000) if record_count else 0.0

    return {
        "unique_records_in_review": len(record_case_counts),
        "review_cases_per_1000_records": cases_per_1000,
        "records_in_multiple_review_cases": sum(
            1 for count in record_case_counts.values() if count > 1
        ),
        "max_review_cases_for_single_record": max(record_case_counts.values(), default=0),
        "largest_review_case_component": largest_component,
    }


def run_review_benchmark(
    *,
    dataset_path: Path,
    split_name: str = "test",
) -> ReviewBenchmarkResult:
    result = ReviewBenchmarkResult(split_name=split_name)
    try:
        ground_truth = load_evaluation_ground_truth(dataset_path)
        split_person_ids = set(ground_truth.splits.get(split_name, []))
        if not split_person_ids:
            raise ValueError(f"Split '{split_name}' not found or empty.")

        all_records = _load_entity_records_from_dataset(dataset_path)
        records = [
            record
            for record in all_records
            if ground_truth.person_mappings.get(record.record_id) in split_person_ids
        ]
        resolution_config = load_entity_resolution_config()
        resolution = resolve_entities(records, source_label=f"review-benchmark-{split_name}")
        workflow_state = generate_review_cases(resolution, config=resolution_config)
        workflow = ReviewWorkflow(workflow_state)

        result.review_case_count = len(workflow_state.cases)
        result.pending_case_count = len(workflow_state.pending_cases())
        result.deferred_case_count = len(workflow_state.deferred_cases())
        workload = _review_workload_stats(workflow_state.cases, record_count=len(records))
        result.unique_records_in_review = int(workload["unique_records_in_review"])
        result.review_cases_per_1000_records = float(workload["review_cases_per_1000_records"])
        result.records_in_multiple_review_cases = int(workload["records_in_multiple_review_cases"])
        result.max_review_cases_for_single_record = int(
            workload["max_review_cases_for_single_record"]
        )
        result.largest_review_case_component = int(workload["largest_review_case_component"])

        correct = 0
        labeled = 0
        false_human_match = 0
        human_match_total = 0

        for case in workflow_state.cases:
            oracle_decision = _oracle_decision_for_review_pair(case.pair, ground_truth=ground_truth)
            if oracle_decision is None:
                continue
            labeled += 1
            result.labeled_review_pairs = labeled
            if oracle_decision == HumanReviewDecision.MATCH:
                result.oracle_match_decisions += 1
            elif oracle_decision == HumanReviewDecision.NO_MATCH:
                result.oracle_no_match_decisions += 1
            else:
                result.oracle_defer_decisions += 1

            try:
                workflow.resolve_case(
                    case.review_case_id,
                    decision=oracle_decision,
                    reviewer_id="oracle-simulator",
                )
            except HumanReviewContradictionError:
                result.authorization_blocked_oracle_matches += 1
                continue

            if oracle_decision == HumanReviewDecision.MATCH:
                human_match_total += 1
                person_a, person_b = _pair_person_truth(case.pair, ground_truth=ground_truth)
                if person_a != person_b:
                    false_human_match += 1
                else:
                    correct += 1
            elif oracle_decision == HumanReviewDecision.NO_MATCH:
                person_a, person_b = _pair_person_truth(case.pair, ground_truth=ground_truth)
                if person_a != person_b:
                    correct += 1

        outcome = workflow.to_outcome()
        survivorship = build_canonical_entities(
            resolution,
            human_review_outcome=outcome,
            entity_resolution_config=resolution_config,
        )
        result.duplicate_membership_violations = _membership_violations(survivorship)
        unresolved_ids = outcome.unresolved_record_ids()
        for entity in survivorship.entities:
            if len(entity.member_record_ids) < 2:
                continue
            if any(record_id in unresolved_ids for record_id in entity.member_record_ids):
                result.unresolved_unsafe_merge_violations += 1

        if human_match_total:
            result.oracle_simulated_match_application_safety_rate = 1.0 - (
                false_human_match / human_match_total
            )
        if labeled:
            result.oracle_simulated_resolution_application_accuracy = correct / labeled
    except (OSError, ValueError, TypeError, KeyError) as exc:
        result.ran_successfully = False
        result.error_message = str(exc)

    return result
