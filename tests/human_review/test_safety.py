from __future__ import annotations

from dataclasses import replace

from human_review.cases import generate_review_cases
from human_review.models import HumanReviewDecision
from human_review.safety import (
    count_human_match_without_provenance,
    count_no_match_transitive_merges,
    count_severe_conflict_merges,
    count_unresolved_unsafe_merges,
)
from human_review.workflow import ReviewWorkflow
from survivorship.engine import build_canonical_entities
from survivorship.models import SurvivorshipResult
from tests.human_review.conftest import make_review_resolution, match_authorization_kwargs


def test_unresolved_review_does_not_merge(resolution_config) -> None:
    resolution = make_review_resolution("rec-a", "rec-b")
    result = build_canonical_entities(resolution, human_review_outcome=None)
    unresolved = frozenset({"rec-a", "rec-b"})
    assert count_unresolved_unsafe_merges(result, unresolved) == 0
    assert all(len(entity.member_record_ids) == 1 for entity in result.entities)


def test_no_match_and_severe_conflict_counters_are_zero_on_safe_outcome(
    resolution_config,
) -> None:
    resolution = make_review_resolution("rec-a", "rec-b")
    workflow = ReviewWorkflow(generate_review_cases(resolution, config=resolution_config))
    workflow.resolve_case(
        workflow.list_cases()[0].review_case_id,
        decision=HumanReviewDecision.NO_MATCH,
    )
    outcome = workflow.to_outcome()
    result = build_canonical_entities(
        resolution,
        human_review_outcome=outcome,
        entity_resolution_config=resolution_config,
    )
    assert count_no_match_transitive_merges(result, outcome.resolved_no_match_pairs()) == 0
    assert count_severe_conflict_merges(result) == 0
    assert count_human_match_without_provenance(result, outcome) == 0


def test_human_match_without_provenance_detects_stripped_lineage(resolution_config) -> None:
    resolution = make_review_resolution("rec-a", "rec-b")
    workflow = ReviewWorkflow(generate_review_cases(resolution, config=resolution_config))
    workflow.resolve_case(
        workflow.list_cases()[0].review_case_id,
        decision=HumanReviewDecision.MATCH,
        reviewer_id="reviewer-1",
        **match_authorization_kwargs(resolution, resolution_config),
    )
    outcome = workflow.to_outcome()
    result = build_canonical_entities(
        resolution,
        human_review_outcome=outcome,
        entity_resolution_config=resolution_config,
    )
    assert count_human_match_without_provenance(result, outcome) == 0

    stripped_entities = tuple(
        replace(entity, human_review_provenance=())
        if len(entity.member_record_ids) >= 2
        else entity
        for entity in result.entities
    )
    stripped_result = SurvivorshipResult(
        source_label=result.source_label,
        entities=stripped_entities,
        review_excluded_record_ids=result.review_excluded_record_ids,
        summary=result.summary,
    )
    assert count_human_match_without_provenance(stripped_result, outcome) >= 1
