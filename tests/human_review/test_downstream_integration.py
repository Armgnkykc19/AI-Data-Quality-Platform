from __future__ import annotations

import pytest

from human_review.cases import generate_review_cases
from human_review.constraints import assert_human_match_allowed
from human_review.errors import HumanReviewContradictionError
from human_review.integration import review_excluded_record_ids
from human_review.models import HumanReviewDecision
from human_review.workflow import ReviewWorkflow
from survivorship.engine import build_canonical_entities
from tests.human_review.conftest import make_chain_review_resolution, make_review_resolution


def test_unresolved_review_remains_excluded(resolution_config) -> None:
    resolution = make_review_resolution("rec-a", "rec-b")
    excluded = review_excluded_record_ids(resolution, None)
    assert excluded == frozenset({"rec-a", "rec-b"})

    result = build_canonical_entities(resolution, human_review_outcome=None)
    assert result.summary.review_excluded_record_count == 2
    assert all(len(entity.member_record_ids) == 1 for entity in result.entities)


def test_human_match_enables_canonical_merge(resolution_config) -> None:
    resolution = make_review_resolution("rec-a", "rec-b")
    workflow = ReviewWorkflow(generate_review_cases(resolution, config=resolution_config))
    case_id = workflow.list_cases()[0].review_case_id
    workflow.resolve_case(case_id, decision=HumanReviewDecision.MATCH, reviewer_id="reviewer-1")
    outcome = workflow.to_outcome()

    excluded = review_excluded_record_ids(resolution, outcome)
    assert not excluded

    result = build_canonical_entities(
        resolution,
        human_review_outcome=outcome,
        entity_resolution_config=resolution_config,
    )
    merged = [entity for entity in result.entities if len(entity.member_record_ids) > 1]
    assert len(merged) == 1
    assert merged[0].human_review_provenance[0].human_decision == "MATCH"
    assert merged[0].human_review_provenance[0].machine_decision == "REVIEW"


def test_human_no_match_does_not_merge(resolution_config) -> None:
    resolution = make_review_resolution("rec-a", "rec-b")
    workflow = ReviewWorkflow(generate_review_cases(resolution, config=resolution_config))
    workflow.resolve_case(
        workflow.list_cases()[0].review_case_id,
        decision=HumanReviewDecision.NO_MATCH,
    )
    result = build_canonical_entities(
        resolution,
        human_review_outcome=workflow.to_outcome(),
        entity_resolution_config=resolution_config,
    )
    assert all(len(entity.member_record_ids) == 1 for entity in result.entities)


def test_deferred_review_remains_excluded(resolution_config) -> None:
    resolution = make_review_resolution("rec-a", "rec-b")
    workflow = ReviewWorkflow(generate_review_cases(resolution, config=resolution_config))
    workflow.resolve_case(
        workflow.list_cases()[0].review_case_id,
        decision=HumanReviewDecision.DEFER,
    )
    result = build_canonical_entities(resolution, human_review_outcome=workflow.to_outcome())
    assert result.summary.review_excluded_record_count == 2


def test_transitive_human_match(resolution_config) -> None:
    resolution = make_chain_review_resolution(("rec-a", "rec-b", "rec-c"))
    workflow = ReviewWorkflow(generate_review_cases(resolution, config=resolution_config))
    for case in workflow.list_cases():
        workflow.resolve_case(case.review_case_id, decision=HumanReviewDecision.MATCH)

    result = build_canonical_entities(
        resolution,
        human_review_outcome=workflow.to_outcome(),
        entity_resolution_config=resolution_config,
    )
    assert any(len(entity.member_record_ids) >= 2 for entity in result.entities)


def test_no_match_blocks_transitive_human_match(resolution_config) -> None:
    resolution = make_review_resolution("rec-a", "rec-b")
    workflow = ReviewWorkflow(generate_review_cases(resolution, config=resolution_config))
    case_ab = workflow.list_cases()[0]
    workflow.resolve_case(case_ab.review_case_id, decision=HumanReviewDecision.NO_MATCH)

    resolution2 = make_review_resolution("rec-a", "rec-c")
    workflow2 = ReviewWorkflow(generate_review_cases(resolution2, config=resolution_config))
    case_ac = workflow2.list_cases()[0]
    workflow2.resolve_case(case_ac.review_case_id, decision=HumanReviewDecision.MATCH)

    resolution3 = make_review_resolution("rec-b", "rec-c")
    workflow3 = ReviewWorkflow(generate_review_cases(resolution3, config=resolution_config))
    case_bc = workflow3.list_cases()[0]

    outcome_ab = workflow.to_outcome()
    outcome_ac = workflow2.to_outcome()
    combined_matches = outcome_ab.resolved_match_pairs() | outcome_ac.resolved_match_pairs()
    combined_no_matches = outcome_ab.resolved_no_match_pairs()
    with pytest.raises(HumanReviewContradictionError):
        assert_human_match_allowed(
            pair=case_bc.pair,
            outcome=workflow3.to_outcome(),
            existing_match_pairs=combined_matches,
            existing_no_match_pairs=combined_no_matches,
        )


def test_no_duplicate_canonical_membership(resolution_config) -> None:
    resolution = make_review_resolution("rec-a", "rec-b")
    workflow = ReviewWorkflow(generate_review_cases(resolution, config=resolution_config))
    workflow.resolve_case(
        workflow.list_cases()[0].review_case_id,
        decision=HumanReviewDecision.MATCH,
    )
    result = build_canonical_entities(
        resolution,
        human_review_outcome=workflow.to_outcome(),
        entity_resolution_config=resolution_config,
    )
    seen: set[str] = set()
    for entity in result.entities:
        for record_id in entity.member_record_ids:
            assert record_id not in seen
            seen.add(record_id)
