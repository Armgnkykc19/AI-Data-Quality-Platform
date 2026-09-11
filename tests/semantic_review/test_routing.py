from __future__ import annotations

from dataclasses import replace

from entity_resolution.models import MatchDecisionType
from human_review.cases import generate_review_cases
from human_review.models import HumanReviewDecision, HumanReviewOutcome
from human_review.workflow import ReviewWorkflow
from semantic_review.models import SemanticSkipReason
from semantic_review.policy import evaluate_routing
from semantic_review.providers.fake_provider import match_provider
from semantic_review.service import SemanticReviewService
from tests.human_review.conftest import make_bridge_resolution, make_record


def test_review_case_is_eligible(review_bundle, enabled_semantic_config) -> None:
    resolution, state, records_by_id, er_config = review_bundle
    routing = evaluate_routing(
        state.cases[0],
        config=enabled_semantic_config,
        resolution=resolution,
        records_by_id=records_by_id,
        entity_resolution_config=er_config,
        require_enabled=True,
    )
    assert routing.eligible is True


def test_auto_match_rejected(review_bundle, enabled_semantic_config) -> None:
    _resolution, state, records_by_id, _er = review_bundle
    case = replace(state.cases[0], machine_decision=MatchDecisionType.AUTO_MATCH)
    routing = evaluate_routing(
        case,
        config=enabled_semantic_config,
        records_by_id=records_by_id,
        require_enabled=True,
    )
    assert routing.reason == SemanticSkipReason.NOT_REVIEW_DECISION


def test_no_match_machine_decision_rejected(review_bundle, enabled_semantic_config) -> None:
    _, state, records_by_id, _ = review_bundle
    case = replace(state.cases[0], machine_decision=MatchDecisionType.NO_MATCH)
    routing = evaluate_routing(case, config=enabled_semantic_config, records_by_id=records_by_id)
    assert routing.reason == SemanticSkipReason.NOT_REVIEW_DECISION


def test_missing_case_rejected(enabled_semantic_config) -> None:
    routing = evaluate_routing(None, config=enabled_semantic_config)
    assert routing.reason == SemanticSkipReason.CASE_NOT_FOUND


def test_resolved_and_deferred_rejected(review_bundle, enabled_semantic_config) -> None:
    _resolution, state, records_by_id, _er = review_bundle
    workflow = ReviewWorkflow(state)
    workflow.resolve_case(
        state.cases[0].review_case_id,
        decision=HumanReviewDecision.DEFER,
        reviewer_id="tester",
    )
    deferred = workflow.state.cases[0]
    routing = evaluate_routing(
        deferred,
        config=enabled_semantic_config,
        records_by_id=records_by_id,
        require_enabled=True,
    )
    assert routing.reason == SemanticSkipReason.CASE_TERMINAL


def test_disabled_integration_does_not_call_provider(review_bundle, semantic_config) -> None:
    resolution, state, records_by_id, er_config = review_bundle
    provider = match_provider(semantic_config.pricing, semantic_config.model)
    service = SemanticReviewService(semantic_config, provider, require_enabled=True)
    routing, suggestion = service.suggest_for_case(
        state.cases[0],
        records_by_id=records_by_id,
        resolution=resolution,
        entity_resolution_config=er_config,
    )
    assert suggestion is None
    assert routing.reason == SemanticSkipReason.LLM_DISABLED
    assert provider.calls == 0


def test_oversized_input_rejected_before_provider(review_bundle, enabled_semantic_config) -> None:
    resolution, state, records_by_id, er_config = review_bundle
    tiny = replace(enabled_semantic_config, max_input_tokens=1)
    provider = match_provider(tiny.pricing, tiny.model)
    service = SemanticReviewService(tiny, provider, require_enabled=True)
    routing, suggestion = service.suggest_for_case(
        state.cases[0],
        records_by_id=records_by_id,
        resolution=resolution,
        entity_resolution_config=er_config,
    )
    assert suggestion is None
    assert routing.reason == SemanticSkipReason.INPUT_TOO_LARGE
    assert provider.calls == 0


def test_authorization_blocked_case_rejected(enabled_semantic_config, resolution_config) -> None:
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
    state = generate_review_cases(resolution, config=resolution_config)
    records_by_id = {record.record_id: record for record in records}
    routing = evaluate_routing(
        state.cases[0],
        config=enabled_semantic_config,
        outcome=HumanReviewOutcome(workflow_state=state),
        resolution=resolution,
        records_by_id=records_by_id,
        entity_resolution_config=resolution_config,
        require_enabled=True,
    )
    assert routing.eligible is False
    assert routing.reason == SemanticSkipReason.AUTHORIZATION_BLOCKED
