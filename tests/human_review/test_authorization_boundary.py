from __future__ import annotations

import pytest

from entity_resolution.models import RecordPair, ResolutionResult, ResolutionSummary
from human_review.cases import generate_review_cases
from human_review.errors import HumanReviewAuthorizationError, HumanReviewContradictionError
from human_review.ids import stable_review_case_id
from human_review.integration import review_excluded_record_ids
from human_review.models import HumanReviewDecision, ReviewStatus
from human_review.workflow import ReviewWorkflow
from survivorship.engine import build_canonical_entities
from tests.human_review.conftest import (
    _auto_match_pair,
    make_bridge_resolution,
    make_record,
    make_review_resolution,
    make_triangle_review_resolution,
    match_authorization_kwargs,
)


def _records_by_id(*records):
    return {record.record_id: record for record in records}


def test_human_match_bridge_with_severe_transitive_conflict_blocked(resolution_config) -> None:
    records = (
        make_record("rec-a", first_name="Ali", last_name="Yilmaz", email="a@example.com"),
        make_record("rec-b", first_name="Ali", last_name="Yilmaz", email="a@example.com"),
        make_record("rec-c", first_name="Ali", last_name="Yilmaz", email="c@example.com"),
        make_record("rec-d", first_name="Ali", last_name="Yilmaz", email="d@example.com"),
    )
    resolution = make_bridge_resolution(
        left_ids=("rec-a", "rec-b"),
        right_ids=("rec-c", "rec-d"),
        bridge_ids=("rec-b", "rec-c"),
        records=records,
    )
    workflow = ReviewWorkflow(generate_review_cases(resolution, config=resolution_config))
    bridge_case = workflow.list_cases()[0]
    records_by_id = _records_by_id(*records)

    with pytest.raises(HumanReviewAuthorizationError):
        workflow.resolve_case(
            bridge_case.review_case_id,
            decision=HumanReviewDecision.MATCH,
            resolution=resolution,
            records_by_id=records_by_id,
            entity_resolution_config=resolution_config,
        )

    outcome = workflow.to_outcome()
    result = build_canonical_entities(
        resolution,
        human_review_outcome=outcome,
        entity_resolution_config=resolution_config,
    )
    assert all(len(entity.member_record_ids) == 1 for entity in result.entities)


def test_human_match_bridge_without_severe_conflict_allowed(resolution_config) -> None:
    records = (
        make_record("rec-a", first_name="Ali", last_name="Yilmaz", email="shared@example.com"),
        make_record("rec-b", first_name="Ali", last_name="Yilmaz", email="shared@example.com"),
        make_record("rec-c", first_name="Ali", last_name="Yilmaz", email="shared@example.com"),
        make_record("rec-d", first_name="Ali", last_name="Yilmaz", email="shared@example.com"),
    )
    resolution = make_bridge_resolution(
        left_ids=("rec-a", "rec-b"),
        right_ids=("rec-c", "rec-d"),
        bridge_ids=("rec-b", "rec-c"),
        records=records,
    )
    workflow = ReviewWorkflow(generate_review_cases(resolution, config=resolution_config))
    bridge_case = workflow.list_cases()[0]
    records_by_id = _records_by_id(*records)
    workflow.resolve_case(
        bridge_case.review_case_id,
        decision=HumanReviewDecision.MATCH,
        resolution=resolution,
        records_by_id=records_by_id,
        entity_resolution_config=resolution_config,
    )
    result = build_canonical_entities(
        resolution,
        human_review_outcome=workflow.to_outcome(),
        entity_resolution_config=resolution_config,
    )
    assert any(len(entity.member_record_ids) == 4 for entity in result.entities)


def test_mixed_auto_human_no_match_blocks_transitive_merge(resolution_config) -> None:
    records = (
        make_record(
            "rec-a",
            first_name="Ali",
            last_name="Yilmaz",
            email="shared@example.com",
        ),
        make_record(
            "rec-b",
            first_name="Ali",
            last_name="Yilmaz",
            email="shared@example.com",
        ),
        make_record(
            "rec-c",
            first_name="Ali",
            last_name="Yilmaz",
            email="shared@example.com",
        ),
    )
    auto_ab = _auto_match_pair("rec-a", "rec-b")
    bc_review = make_review_resolution("rec-b", "rec-c")
    ac_review = make_review_resolution("rec-a", "rec-c")
    resolution = ResolutionResult(
        source_label="mixed-auto-human",
        records=records,
        candidates=(),
        decisions=(auto_ab, bc_review.decisions[0], ac_review.decisions[0]),
        review_queue=(bc_review.review_queue[0], ac_review.review_queue[0]),
        clusters=(),
        summary=ResolutionSummary(
            record_count=3,
            possible_pair_count=3,
            candidate_pair_count=3,
            candidate_reduction_ratio=0.0,
            auto_match_count=1,
            review_count=2,
            no_match_count=0,
            cluster_count=0,
            conflict_guarded_clusters=0,
        ),
    )
    workflow = ReviewWorkflow(generate_review_cases(resolution, config=resolution_config))
    cases = {case.pair: case for case in workflow.list_cases()}
    workflow.resolve_case(
        cases[RecordPair.ordered("rec-a", "rec-c")].review_case_id,
        decision=HumanReviewDecision.NO_MATCH,
    )
    with pytest.raises(HumanReviewContradictionError):
        workflow.resolve_case(
            cases[RecordPair.ordered("rec-b", "rec-c")].review_case_id,
            decision=HumanReviewDecision.MATCH,
            resolution=resolution,
            records_by_id=_records_by_id(*records),
            entity_resolution_config=resolution_config,
        )


def test_partial_resolution_keeps_record_excluded_while_other_case_pending(
    resolution_config,
) -> None:
    resolution = make_triangle_review_resolution(("rec-a", "rec-b", "rec-c"))
    workflow = ReviewWorkflow(generate_review_cases(resolution, config=resolution_config))
    cases = {case.pair: case for case in workflow.list_cases()}
    workflow.resolve_case(
        cases[RecordPair.ordered("rec-a", "rec-b")].review_case_id,
        decision=HumanReviewDecision.MATCH,
        **match_authorization_kwargs(resolution, resolution_config),
    )
    outcome = workflow.to_outcome()
    excluded = review_excluded_record_ids(resolution, outcome)
    assert "rec-a" in excluded
    assert "rec-c" in excluded
    result = build_canonical_entities(
        resolution,
        human_review_outcome=outcome,
        entity_resolution_config=resolution_config,
    )
    assert all(len(entity.member_record_ids) == 1 for entity in result.entities)


def test_match_and_defer_prevents_merge(resolution_config) -> None:
    resolution = make_triangle_review_resolution(("rec-a", "rec-b", "rec-c"))
    workflow = ReviewWorkflow(generate_review_cases(resolution, config=resolution_config))
    cases = {case.pair: case for case in workflow.list_cases()}
    workflow.resolve_case(
        cases[RecordPair.ordered("rec-a", "rec-b")].review_case_id,
        decision=HumanReviewDecision.MATCH,
        **match_authorization_kwargs(resolution, resolution_config),
    )
    workflow.resolve_case(
        cases[RecordPair.ordered("rec-a", "rec-c")].review_case_id,
        decision=HumanReviewDecision.DEFER,
    )
    outcome = workflow.to_outcome()
    result = build_canonical_entities(
        resolution,
        human_review_outcome=outcome,
        entity_resolution_config=resolution_config,
    )
    assert all(len(entity.member_record_ids) == 1 for entity in result.entities)
    assert review_excluded_record_ids(resolution, outcome)


def test_defer_preserves_audit_and_exclusion(resolution_config) -> None:
    resolution = make_review_resolution("rec-a", "rec-b")
    workflow = ReviewWorkflow(generate_review_cases(resolution, config=resolution_config))
    case = workflow.list_cases()[0]
    original_summary = case.human_summary
    workflow.resolve_case(case.review_case_id, decision=HumanReviewDecision.DEFER, reviewer_id="r1")
    resolved = workflow.get_case(case.review_case_id)
    assert resolved.status == ReviewStatus.DEFERRED
    assert resolved.machine_decision.value == "REVIEW"
    assert resolved.human_summary == original_summary
    assert resolved.resolution is not None
    assert resolved.resolution.human_decision == HumanReviewDecision.DEFER
    audit = workflow.state.audit_trail[-1]
    assert audit.review_case_id == case.review_case_id
    assert audit.human_decision == "DEFER"
    assert audit.reviewer_id == "r1"
    outcome = workflow.to_outcome()
    assert review_excluded_record_ids(resolution, outcome) == frozenset({"rec-a", "rec-b"})
    result = build_canonical_entities(resolution, human_review_outcome=outcome)
    assert result.summary.review_excluded_record_count == 2


def test_audit_trail_preserves_machine_context(resolution_config) -> None:
    resolution = make_review_resolution("rec-a", "rec-b", score=0.84)
    workflow = ReviewWorkflow(generate_review_cases(resolution, config=resolution_config))
    case = workflow.list_cases()[0]
    workflow.resolve_case(
        case.review_case_id,
        decision=HumanReviewDecision.MATCH,
        reviewer_id="reviewer-42",
        **match_authorization_kwargs(resolution, resolution_config),
    )
    resolved = workflow.get_case(case.review_case_id)
    assert resolved.machine_score == pytest.approx(0.84)
    assert resolved.machine_reason
    assert resolved.supporting_evidence
    audit = workflow.state.audit_trail[-1]
    assert audit.machine_decision == "REVIEW"
    assert audit.machine_reason == resolved.machine_reason
    assert audit.resolution_sequence == 1
    assert audit.downstream_action == "eligible_for_human_confirmed_canonical_merge"


def test_stable_review_case_id_avoids_separator_collision() -> None:
    pair_one = RecordPair.ordered("a", "b--c")
    pair_two = RecordPair.ordered("a--b", "c")
    assert stable_review_case_id(pair_one) != stable_review_case_id(pair_two)
    assert stable_review_case_id(pair_one) == stable_review_case_id(RecordPair.ordered("a", "b--c"))


def test_oracle_metric_fields_renamed_in_benchmark_module() -> None:
    from evaluation.review_benchmark import ReviewBenchmarkResult

    result = ReviewBenchmarkResult(split_name="test")
    assert hasattr(result, "oracle_simulated_resolution_application_accuracy")
    assert hasattr(result, "oracle_simulated_match_application_safety_rate")
    assert hasattr(result, "authorization_blocked_oracle_matches")
    assert hasattr(result, "oracle_applied_labeled_pairs")
    assert not hasattr(result, "oracle_resolution_accuracy")
