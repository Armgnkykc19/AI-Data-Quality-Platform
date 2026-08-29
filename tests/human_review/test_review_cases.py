from __future__ import annotations

import pytest

from entity_resolution.models import ConflictType, PairConflict, RecordPair
from human_review.cases import generate_review_cases
from human_review.errors import InvalidReviewTransitionError
from human_review.ids import stable_review_case_id
from human_review.models import HumanReviewDecision, ReviewStatus
from human_review.workflow import ReviewWorkflow
from tests.human_review.conftest import make_review_resolution, match_authorization_kwargs


def test_review_case_generation_from_review_queue(resolution_config) -> None:
    resolution = make_review_resolution("rec-0002", "rec-0001")
    state = generate_review_cases(resolution, config=resolution_config)
    assert len(state.cases) == 1
    case = state.cases[0]
    assert case.machine_decision.value == "REVIEW"
    assert case.status == ReviewStatus.PENDING
    assert case.supporting_evidence
    assert case.human_summary


def test_stable_review_case_id_is_order_invariant() -> None:
    pair_a = RecordPair.ordered("rec-b", "rec-a")
    pair_b = RecordPair.ordered("rec-a", "rec-b")
    assert stable_review_case_id(pair_a) == stable_review_case_id(pair_b)


def test_generate_review_cases_is_deterministic(resolution_config) -> None:
    resolution = make_review_resolution("rec-0002", "rec-0001")
    first = generate_review_cases(resolution, config=resolution_config)
    second = generate_review_cases(resolution, config=resolution_config)
    assert [case.to_dict() for case in first.cases] == [case.to_dict() for case in second.cases]


def test_explanation_mentions_conflicts(resolution_config) -> None:
    conflicts = (
        PairConflict(
            conflict_type=ConflictType.EMAIL_CONFLICT,
            field_name="email",
            severity="severe",
            penalty=0.55,
            description="Different email values.",
        ),
    )
    resolution = make_review_resolution("rec-a", "rec-b", conflicts=conflicts)
    case = generate_review_cases(resolution, config=resolution_config).cases[0]
    assert case.conflicting_evidence
    assert "email" in case.human_summary.lower()


def test_match_no_match_and_defer_resolutions(resolution_config) -> None:
    resolution = make_review_resolution("rec-a", "rec-b")
    workflow = ReviewWorkflow(generate_review_cases(resolution, config=resolution_config))
    case_id = workflow.list_cases()[0].review_case_id

    workflow.resolve_case(
        case_id,
        decision=HumanReviewDecision.MATCH,
        reviewer_id="reviewer-1",
        **match_authorization_kwargs(resolution, resolution_config),
    )
    with pytest.raises(InvalidReviewTransitionError):
        workflow.resolve_case(case_id, decision=HumanReviewDecision.NO_MATCH)

    resolution2 = make_review_resolution("rec-c", "rec-d")
    workflow2 = ReviewWorkflow(generate_review_cases(resolution2, config=resolution_config))
    case_id2 = workflow2.list_cases()[0].review_case_id
    workflow2.resolve_case(
        case_id2, decision=HumanReviewDecision.NO_MATCH, reviewer_id="reviewer-1"
    )

    resolution3 = make_review_resolution("rec-e", "rec-f")
    workflow3 = ReviewWorkflow(generate_review_cases(resolution3, config=resolution_config))
    case_id3 = workflow3.list_cases()[0].review_case_id
    workflow3.resolve_case(case_id3, decision=HumanReviewDecision.DEFER, reviewer_id="reviewer-1")
    assert workflow3.get_case(case_id3).status == ReviewStatus.DEFERRED


def test_machine_decision_preserved_after_resolution(resolution_config) -> None:
    resolution = make_review_resolution("rec-a", "rec-b")
    workflow = ReviewWorkflow(generate_review_cases(resolution, config=resolution_config))
    case = workflow.list_cases()[0]
    workflow.resolve_case(
        case.review_case_id,
        decision=HumanReviewDecision.MATCH,
        **match_authorization_kwargs(resolution, resolution_config),
    )
    resolved = workflow.get_case(case.review_case_id)
    assert resolved.machine_decision.value == "REVIEW"
    assert resolved.resolution is not None
    assert resolved.resolution.machine_decision.value == "REVIEW"
