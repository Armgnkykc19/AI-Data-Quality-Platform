"""Tenancy at the application layer: the service is unchanged, and still safe.

``ReviewQueueService`` gained nothing in Sprint 13 -- no tenant parameter, no
scope argument, not one new line about organizations. It is safe because the
repository it was handed can only see one queue. These tests prove that the
substitution actually holds end to end: two services over two queues in one
database, driving the real Sprint 08 workflow, with none of the Sprint 10
guarantees weakened.
"""

from __future__ import annotations

import pytest

from entity_resolution.models import ResolutionResult
from human_review.errors import (
    HumanReviewAuthorizationError,
    InvalidReviewTransitionError,
)
from human_review.models import HumanReviewDecision, ReviewStatus, ReviewWorkflowState
from review_application import ReviewQueueService, register_review_workflow
from review_application.errors import ReviewConflictError
from review_persistence.sqlite.review_repository import SqliteReviewCaseRepository
from tests.review_application.conftest import register
from tests.review_persistence.conftest import conflicting_bridge_resolution


@pytest.fixture
def shared_case_id(
    repository: SqliteReviewCaseRepository,
    second_repository: SqliteReviewCaseRepository,
    review_state: ReviewWorkflowState,
    resolution: ResolutionResult,
) -> str:
    """One deterministic case id, registered into two tenants' queues."""
    register(repository, review_state, resolution)
    register(second_repository, review_state, resolution)
    return review_state.cases[0].review_case_id


# --------------------------------------------------------------------------
# Resolution through the service
# --------------------------------------------------------------------------


def test_a_decision_in_one_queue_does_not_reach_the_other(
    service: ReviewQueueService,
    second_repository: SqliteReviewCaseRepository,
    repository: SqliteReviewCaseRepository,
    shared_case_id: str,
) -> None:
    service.resolve_case(
        shared_case_id,
        decision=HumanReviewDecision.NO_MATCH,
        reviewer_id="reviewer-1",
        expected_version=1,
    )

    assert repository.get_case(shared_case_id).status is ReviewStatus.NO_MATCH

    untouched = second_repository.get_case(shared_case_id)
    assert untouched.status is ReviewStatus.PENDING
    assert untouched.version == 1
    assert second_repository.list_events(shared_case_id) == ()


def test_the_other_queue_can_still_decide_the_same_case_id(
    service: ReviewQueueService,
    second_service: ReviewQueueService,
    shared_case_id: str,
) -> None:
    """Terminal in one tenant, still PENDING and decidable in the other."""
    service.resolve_case(
        shared_case_id,
        decision=HumanReviewDecision.NO_MATCH,
        reviewer_id="reviewer-1",
        expected_version=1,
    )

    result = second_service.resolve_case(
        shared_case_id,
        decision=HumanReviewDecision.DEFER,
        reviewer_id="reviewer-2",
        expected_version=1,
    )

    assert result.persisted_case.status is ReviewStatus.DEFERRED
    assert result.version == 2


def test_a_second_decision_in_the_same_queue_is_still_refused(
    service: ReviewQueueService,
    shared_case_id: str,
) -> None:
    """Terminal state is unchanged by tenancy."""
    service.resolve_case(
        shared_case_id,
        decision=HumanReviewDecision.NO_MATCH,
        reviewer_id="reviewer-1",
        expected_version=1,
    )

    with pytest.raises(InvalidReviewTransitionError):
        service.resolve_case(
            shared_case_id,
            decision=HumanReviewDecision.DEFER,
            reviewer_id="reviewer-1",
            expected_version=2,
        )


def test_optimistic_concurrency_is_unchanged(
    service: ReviewQueueService,
    shared_case_id: str,
) -> None:
    """A stale expected_version is still a conflict, with nothing written."""
    with pytest.raises(ReviewConflictError):
        service.resolve_case(
            shared_case_id,
            decision=HumanReviewDecision.NO_MATCH,
            reviewer_id="reviewer-1",
            expected_version=7,
        )


def test_a_version_bump_in_one_queue_does_not_stale_the_other(
    service: ReviewQueueService,
    second_service: ReviewQueueService,
    shared_case_id: str,
) -> None:
    """Versions are per row, and the rows are in different queues.

    If the compare-and-swap were not queue-scoped, the first decision would
    have advanced the second tenant's case too, and this would be a conflict.
    """
    service.resolve_case(
        shared_case_id,
        decision=HumanReviewDecision.NO_MATCH,
        reviewer_id="reviewer-1",
        expected_version=1,
    )

    result = second_service.resolve_case(
        shared_case_id,
        decision=HumanReviewDecision.NO_MATCH,
        reviewer_id="reviewer-2",
        expected_version=1,
    )

    assert result.version == 2


def test_a_case_in_another_queue_is_simply_not_found(
    second_service: ReviewQueueService,
    second_repository: SqliteReviewCaseRepository,
    repository: SqliteReviewCaseRepository,
    review_state: ReviewWorkflowState,
    resolution: ResolutionResult,
) -> None:
    """A fully populated second queue still cannot see the first queue's case.

    Both queues hold a registered workflow, so the second one is capable of
    deciding -- it simply has no such case. The failure is the ordinary
    not-found, indistinguishable from an id that exists nowhere.
    """
    from entity_resolution.config import load_entity_resolution_config
    from human_review.cases import generate_review_cases
    from human_review.errors import ReviewCaseNotFoundError
    from tests.human_review.conftest import make_review_resolution

    register(repository, review_state, resolution)

    other = make_review_resolution("b-1", "b-2")
    other_state = generate_review_cases(other, config=load_entity_resolution_config())
    register(second_repository, other_state, other)

    with pytest.raises(ReviewCaseNotFoundError):
        second_service.resolve_case(
            review_state.cases[0].review_case_id,
            decision=HumanReviewDecision.NO_MATCH,
            reviewer_id="reviewer-1",
            expected_version=1,
        )


# --------------------------------------------------------------------------
# Sprint 08 authorization is untouched
# --------------------------------------------------------------------------


def test_match_authorization_still_refuses_an_unsafe_merge_in_a_tenant_queue(
    repository: SqliteReviewCaseRepository,
    service: ReviewQueueService,
) -> None:
    """The Sprint 08 boundary check runs against this queue's own bundle.

    The bridge fixture is unsafe only because AUTO_MATCH edges pull two
    conflicting records into one component. Tenancy narrowed what the bundle
    contains; it must not have narrowed it below the queue, or this would pass.
    """
    from entity_resolution.config import load_entity_resolution_config
    from human_review.cases import generate_review_cases

    bridge = conflicting_bridge_resolution()
    config = load_entity_resolution_config()
    state = generate_review_cases(bridge, config=config)
    register(repository, state, bridge)

    with pytest.raises(HumanReviewAuthorizationError):
        service.resolve_case(
            state.cases[0].review_case_id,
            decision=HumanReviewDecision.MATCH,
            reviewer_id="reviewer-1",
            expected_version=1,
        )


def test_a_refused_match_writes_nothing_in_either_queue(
    repository: SqliteReviewCaseRepository,
    second_repository: SqliteReviewCaseRepository,
    service: ReviewQueueService,
) -> None:
    from entity_resolution.config import load_entity_resolution_config
    from human_review.cases import generate_review_cases

    bridge = conflicting_bridge_resolution()
    state = generate_review_cases(bridge, config=load_entity_resolution_config())
    register(repository, state, bridge)
    register(second_repository, state, bridge)
    case_id = state.cases[0].review_case_id

    with pytest.raises(HumanReviewAuthorizationError):
        service.resolve_case(
            case_id,
            decision=HumanReviewDecision.MATCH,
            reviewer_id="reviewer-1",
            expected_version=1,
        )

    for reader in (repository, second_repository):
        persisted = reader.get_case(case_id)
        assert persisted.status is ReviewStatus.PENDING
        assert persisted.version == 1
        assert reader.list_events(case_id) == ()


def test_a_no_match_in_one_queue_does_not_constrain_a_match_in_another(
    service: ReviewQueueService,
    second_service: ReviewQueueService,
    shared_case_id: str,
) -> None:
    """Two queues are two authorization graphs, which is why the queue owns the context.

    Sprint 08 reaches a contradiction verdict by union-find across every
    recorded NO_MATCH in the bundle it is handed. If that bundle spanned
    tenants, the NO_MATCH below would make the other organization's MATCH a
    ``HumanReviewContradictionError`` -- one customer's decision silently
    forbidding another's merge.
    """
    service.resolve_case(
        shared_case_id,
        decision=HumanReviewDecision.NO_MATCH,
        reviewer_id="reviewer-1",
        expected_version=1,
    )

    result = second_service.resolve_case(
        shared_case_id,
        decision=HumanReviewDecision.MATCH,
        reviewer_id="reviewer-2",
        expected_version=1,
    )

    assert result.persisted_case.status is ReviewStatus.MATCH


# --------------------------------------------------------------------------
# Bootstrap
# --------------------------------------------------------------------------


def test_registration_counts_only_the_queue_it_targets(
    repository: SqliteReviewCaseRepository,
    second_repository: SqliteReviewCaseRepository,
    review_state: ReviewWorkflowState,
    resolution: ResolutionResult,
) -> None:
    """A busy installation must not make a fresh queue look pre-populated."""
    first = register_review_workflow(
        repository,
        state=review_state,
        entity_records=resolution.records,
        resolution=resolution,
        entity_resolution_config_path="configs/entity_resolution.yaml",
    )
    second = register_review_workflow(
        second_repository,
        state=review_state,
        entity_records=resolution.records,
        resolution=resolution,
        entity_resolution_config_path="configs/entity_resolution.yaml",
    )

    assert first.newly_registered == len(review_state.cases)
    assert first.already_present == 0
    # The second queue is also a first registration, not a replay of the first.
    assert second.newly_registered == len(review_state.cases)
    assert second.already_present == 0


def test_re_registration_reports_idempotence_per_queue(
    repository: SqliteReviewCaseRepository,
    review_state: ReviewWorkflowState,
    resolution: ResolutionResult,
) -> None:
    register_review_workflow(
        repository,
        state=review_state,
        entity_records=resolution.records,
        resolution=resolution,
        entity_resolution_config_path="configs/entity_resolution.yaml",
    )
    again = register_review_workflow(
        repository,
        state=review_state,
        entity_records=resolution.records,
        resolution=resolution,
        entity_resolution_config_path="configs/entity_resolution.yaml",
    )

    assert again.was_idempotent
    assert again.newly_registered == 0
