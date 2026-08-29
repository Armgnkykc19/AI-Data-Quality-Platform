from __future__ import annotations

from entity_resolution.models import ResolutionResult, ResolutionSummary
from human_review.cases import generate_review_cases
from human_review.models import HumanReviewDecision
from human_review.workflow import ReviewWorkflow
from survivorship.engine import build_canonical_entities
from tests.human_review.conftest import (
    _auto_match_pair,
    make_bridge_resolution,
    make_record,
    make_review_resolution,
    match_authorization_kwargs,
)


def test_pure_auto_match_cluster_has_no_human_provenance(resolution_config) -> None:
    records = (
        make_record("rec-a", first_name="Ali", last_name="Yilmaz", email="shared@example.com"),
        make_record("rec-b", first_name="Ali", last_name="Yilmaz", email="shared@example.com"),
    )
    auto = _auto_match_pair("rec-a", "rec-b")
    resolution = ResolutionResult(
        source_label="auto-only",
        records=records,
        candidates=(),
        decisions=(auto,),
        review_queue=(),
        clusters=(),
        summary=ResolutionSummary(
            record_count=2,
            possible_pair_count=1,
            candidate_pair_count=1,
            candidate_reduction_ratio=0.0,
            auto_match_count=1,
            review_count=0,
            no_match_count=0,
            cluster_count=0,
            conflict_guarded_clusters=0,
        ),
    )
    result = build_canonical_entities(resolution, entity_resolution_config=resolution_config)
    merged = [entity for entity in result.entities if len(entity.member_record_ids) > 1]
    assert merged
    assert all(entity.cluster_id and entity.cluster_id.startswith("C-") for entity in merged)
    assert all(entity.human_review_provenance == () for entity in merged)


def test_pure_human_match_cluster_preserves_provenance(resolution_config) -> None:
    resolution = make_review_resolution("rec-a", "rec-b")
    workflow = ReviewWorkflow(generate_review_cases(resolution, config=resolution_config))
    workflow.resolve_case(
        workflow.list_cases()[0].review_case_id,
        decision=HumanReviewDecision.MATCH,
        reviewer_id="reviewer-1",
        **match_authorization_kwargs(resolution, resolution_config),
    )
    result = build_canonical_entities(
        resolution,
        human_review_outcome=workflow.to_outcome(),
        entity_resolution_config=resolution_config,
    )
    merged = [entity for entity in result.entities if len(entity.member_record_ids) > 1]
    assert len(merged) == 1
    assert merged[0].cluster_id and merged[0].cluster_id.startswith("HR-")
    assert merged[0].human_review_provenance
    assert merged[0].human_review_provenance[0].human_decision == "MATCH"


def test_mixed_auto_human_match_cluster_preserves_provenance(resolution_config) -> None:
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
    workflow.resolve_case(
        bridge_case.review_case_id,
        decision=HumanReviewDecision.MATCH,
        reviewer_id="reviewer-1",
        resolution=resolution,
        records_by_id={record.record_id: record for record in records},
        entity_resolution_config=resolution_config,
    )
    first = build_canonical_entities(
        resolution,
        human_review_outcome=workflow.to_outcome(),
        entity_resolution_config=resolution_config,
    )
    second = build_canonical_entities(
        resolution,
        human_review_outcome=workflow.to_outcome(),
        entity_resolution_config=resolution_config,
    )
    merged = [entity for entity in first.entities if len(entity.member_record_ids) == 4]
    assert len(merged) == 1
    entity = merged[0]
    assert entity.cluster_id and entity.cluster_id.startswith("C-")
    assert entity.human_review_provenance
    covered = {
        (item.record_a_id, item.record_b_id, item.human_decision)
        for item in entity.human_review_provenance
    }
    pair = tuple(sorted((bridge_case.pair.record_a_id, bridge_case.pair.record_b_id)))
    assert (pair[0], pair[1], "MATCH") in covered or (pair[1], pair[0], "MATCH") in covered
    first_payload = [item.to_dict() for item in first.entities]
    second_payload = [item.to_dict() for item in second.entities]
    assert first_payload == second_payload


def test_unresolved_mixed_bridge_does_not_merge(resolution_config) -> None:
    records = (
        make_record("rec-a", first_name="Ali", last_name="Yilmaz", email="shared@example.com"),
        make_record("rec-b", first_name="Ali", last_name="Yilmaz", email="shared@example.com"),
        make_record("rec-c", first_name="Ali", last_name="Yilmaz", email="other@example.com"),
        make_record("rec-d", first_name="Ali", last_name="Yilmaz", email="other@example.com"),
    )
    resolution = make_bridge_resolution(
        left_ids=("rec-a", "rec-b"),
        right_ids=("rec-c", "rec-d"),
        bridge_ids=("rec-b", "rec-c"),
        records=records,
    )
    workflow = ReviewWorkflow(generate_review_cases(resolution, config=resolution_config))
    result = build_canonical_entities(
        resolution,
        human_review_outcome=workflow.to_outcome(),
        entity_resolution_config=resolution_config,
    )
    assert all(len(entity.member_record_ids) == 1 for entity in result.entities)
    assert all(entity.human_review_provenance == () for entity in result.entities)
