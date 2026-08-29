from __future__ import annotations

import pytest

from human_review.authorization import assert_human_match_authorization_boundary
from human_review.cases import generate_review_cases
from human_review.errors import HumanReviewAuthorizationContextError
from human_review.models import HumanReviewDecision, ReviewStatus
from human_review.workflow import ReviewWorkflow
from tests.human_review.conftest import make_review_resolution, match_authorization_kwargs


def _pending_workflow(resolution_config):
    resolution = make_review_resolution("rec-a", "rec-b")
    workflow = ReviewWorkflow(generate_review_cases(resolution, config=resolution_config))
    case = workflow.list_cases()[0]
    return resolution, workflow, case


def test_match_without_authorization_context_is_rejected(resolution_config) -> None:
    _, workflow, case = _pending_workflow(resolution_config)
    with pytest.raises(HumanReviewAuthorizationContextError, match="missing"):
        workflow.resolve_case(case.review_case_id, decision=HumanReviewDecision.MATCH)
    assert workflow.get_case(case.review_case_id).status == ReviewStatus.PENDING
    assert workflow.get_case(case.review_case_id).resolution is None
    assert workflow.state.audit_trail == ()


def test_match_with_only_resolution_is_rejected(resolution_config) -> None:
    resolution, workflow, case = _pending_workflow(resolution_config)
    with pytest.raises(HumanReviewAuthorizationContextError, match="records_by_id"):
        workflow.resolve_case(
            case.review_case_id,
            decision=HumanReviewDecision.MATCH,
            resolution=resolution,
        )
    assert workflow.get_case(case.review_case_id).status == ReviewStatus.PENDING
    assert workflow.state.audit_trail == ()


def test_match_with_resolution_and_records_but_no_config_is_rejected(
    resolution_config,
) -> None:
    resolution, workflow, case = _pending_workflow(resolution_config)
    with pytest.raises(HumanReviewAuthorizationContextError, match="entity_resolution_config"):
        workflow.resolve_case(
            case.review_case_id,
            decision=HumanReviewDecision.MATCH,
            resolution=resolution,
            records_by_id={record.record_id: record for record in resolution.records},
        )
    assert workflow.get_case(case.review_case_id).status == ReviewStatus.PENDING
    assert workflow.state.audit_trail == ()


def test_match_with_full_context_executes_authorization_boundary(
    resolution_config,
    monkeypatch,
) -> None:
    resolution, workflow, case = _pending_workflow(resolution_config)
    called = {"value": False}

    def _spy(**kwargs):
        called["value"] = True
        return assert_human_match_authorization_boundary(**kwargs)

    monkeypatch.setattr(
        "human_review.workflow.assert_human_match_authorization_boundary",
        _spy,
    )
    workflow.resolve_case(
        case.review_case_id,
        decision=HumanReviewDecision.MATCH,
        reviewer_id="reviewer-1",
        **match_authorization_kwargs(resolution, resolution_config),
    )
    assert called["value"] is True
    resolved = workflow.get_case(case.review_case_id)
    assert resolved.status == ReviewStatus.MATCH
    assert resolved.resolution is not None
    assert resolved.resolution.human_decision == HumanReviewDecision.MATCH
    assert workflow.state.audit_trail[-1].human_decision == "MATCH"


def test_rejected_missing_context_match_does_not_mutate_state(resolution_config) -> None:
    _, workflow, case = _pending_workflow(resolution_config)
    before_cases = tuple(item.to_dict() for item in workflow.state.cases)
    before_sequence = workflow.state.next_resolution_sequence
    with pytest.raises(HumanReviewAuthorizationContextError):
        workflow.resolve_case(case.review_case_id, decision=HumanReviewDecision.MATCH)
    assert tuple(item.to_dict() for item in workflow.state.cases) == before_cases
    assert workflow.state.next_resolution_sequence == before_sequence
    assert workflow.state.audit_trail == ()


def test_no_match_does_not_require_er_context(resolution_config) -> None:
    _, workflow, case = _pending_workflow(resolution_config)
    workflow.resolve_case(case.review_case_id, decision=HumanReviewDecision.NO_MATCH)
    assert workflow.get_case(case.review_case_id).status == ReviewStatus.NO_MATCH
    assert workflow.state.audit_trail[-1].human_decision == "NO_MATCH"


def test_deferred_does_not_require_er_context(resolution_config) -> None:
    _, workflow, case = _pending_workflow(resolution_config)
    workflow.resolve_case(case.review_case_id, decision=HumanReviewDecision.DEFER)
    assert workflow.get_case(case.review_case_id).status == ReviewStatus.DEFERRED
    assert workflow.state.audit_trail[-1].human_decision == "DEFER"
