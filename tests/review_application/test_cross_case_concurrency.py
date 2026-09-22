"""Two writers, one queue, two different cases: the state neither may reach.

Sprint 08 MATCH authorization is a property of the *queue*, not of the reviewed
pair. ``assert_human_match_authorization_boundary`` projects a connected
component across every AUTO_MATCH edge and every recorded human decision, so a
merge that is safe in isolation can be forbidden by a decision recorded
elsewhere in the component.

Target-case ``expected_version`` cannot protect that. It proves the one row
being written has not changed, which is lost-update protection. Two writers
resolving two different PENDING cases each pass their own version check, and
the combined state can be one the domain would have refused had it been asked
once, in order.

Before Sprint 14 Phase A the only thing preventing that was the runtime: one
uvicorn worker, ``async`` routes, and a synchronous SQLite call that never
yields. That is a property of how the server happens to be deployed, not of the
persistence contract -- so it held by accident and would have stopped holding
the moment the concurrency model changed.

These tests use **two separate ``ReviewDatabase`` connections to one file**,
which is what two processes look like. They do not rely on workers=1, on the
event loop, or on a process-level lock, because none of those is the mechanism.
The mechanism is that the bundle is read under the write lock and the
resolution commits before that lock is released.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from entity_resolution.config import EntityResolutionConfig
from entity_resolution.models import RecordPair, ResolutionResult
from human_review.cases import generate_review_cases
from human_review.errors import HumanReviewContradictionError
from human_review.models import HumanReviewDecision, ReviewStatus, ReviewWorkflowState
from human_review.reporting import resolution_snapshot
from review_application.errors import ReviewConflictError
from review_application.queues import ReviewQueue
from review_application.service import ReviewQueueService
from review_persistence.config import ReviewPersistenceConfig
from review_persistence.sqlite.database import ReviewDatabase, open_review_database
from review_persistence.sqlite.review_repository import SqliteReviewCaseRepository
from tests.human_review.conftest import make_triangle_review_resolution

CONFIG_PATH = "configs/entity_resolution.yaml"


@pytest.fixture
def triangle(
    resolution_config: EntityResolutionConfig,
) -> tuple[ResolutionResult, ReviewWorkflowState]:
    """Three mutually reviewable records, so one component holds three cases."""
    resolution = make_triangle_review_resolution(("rec-a", "rec-b", "rec-c"))
    return resolution, generate_review_cases(resolution, config=resolution_config)


@pytest.fixture
def seeded_queue(
    repository: SqliteReviewCaseRepository,
    triangle: tuple[ResolutionResult, ReviewWorkflowState],
) -> ReviewWorkflowState:
    resolution, state = triangle
    repository.register_workflow(
        state,
        entity_records=resolution.records,
        resolution_snapshot=resolution_snapshot(resolution),
        entity_resolution_config_path=CONFIG_PATH,
    )
    return state


def _case_id(state: ReviewWorkflowState, left: str, right: str) -> str:
    pair = RecordPair.ordered(left, right)
    return next(case for case in state.cases if case.pair == pair).review_case_id


@pytest.fixture
def second_connection(
    persistence_config: ReviewPersistenceConfig,
    review_queue: ReviewQueue,
) -> Iterator[ReviewDatabase]:
    """A second connection to the same file, standing in for a second process.

    Opened after the fixture queue exists, so it validates the same schema and
    binds to the same tenant rows rather than creating anything.
    """
    database = open_review_database(persistence_config)
    try:
        yield database
    finally:
        database.close()


@pytest.fixture
def writer_b(
    second_connection: ReviewDatabase,
    review_queue: ReviewQueue,
) -> ReviewQueueService:
    """An independent service over its own connection to the same queue."""
    return ReviewQueueService(
        SqliteReviewCaseRepository(second_connection, review_queue_id=review_queue.review_queue_id)
    )


# --------------------------------------------------------------------------
# The incompatible pair
# --------------------------------------------------------------------------


def test_two_writers_cannot_both_commit_incompatible_cross_case_decisions(
    repository: SqliteReviewCaseRepository,
    service: ReviewQueueService,
    writer_b: ReviewQueueService,
    seeded_queue: ReviewWorkflowState,
) -> None:
    """The headline case. Two cases, one component, two decisions that conflict.

    Writer A records NO_MATCH on rec-a/rec-c. Writer B -- a different service,
    on a different connection -- then tries MATCH on rec-a/rec-b and
    rec-b/rec-c, which together would merge rec-a with rec-c and contradict A.

    Both writers hold version 1 for their own target case throughout, so every
    per-case version check passes. The refusal has to come from authorization
    being evaluated against the queue as it actually is, which is what the
    serialized scope guarantees.
    """
    ac = _case_id(seeded_queue, "rec-a", "rec-c")
    ab = _case_id(seeded_queue, "rec-a", "rec-b")
    bc = _case_id(seeded_queue, "rec-b", "rec-c")

    service.resolve_case(
        ac,
        decision=HumanReviewDecision.NO_MATCH,
        reviewer_id="reviewer-a",
        expected_version=1,
    )

    # Safe on its own, and B is allowed it.
    writer_b.resolve_case(
        ab,
        decision=HumanReviewDecision.MATCH,
        reviewer_id="reviewer-b",
        expected_version=1,
    )

    # The one that would complete the contradiction. B still holds version 1 for
    # this case and is still a REVIEWER; the decision is refused on the merits.
    with pytest.raises(HumanReviewContradictionError):
        writer_b.resolve_case(
            bc,
            decision=HumanReviewDecision.MATCH,
            reviewer_id="reviewer-b",
            expected_version=1,
        )

    stored = {case.review_case_id: case.status for case in repository.list_cases()}
    assert stored[ac] is ReviewStatus.NO_MATCH
    assert stored[ab] is ReviewStatus.MATCH
    assert stored[bc] is ReviewStatus.PENDING


def test_a_decision_computed_before_another_writer_committed_is_refused(
    repository: SqliteReviewCaseRepository,
    service: ReviewQueueService,
    writer_b: ReviewQueueService,
    seeded_queue: ReviewWorkflowState,
) -> None:
    """Staleness is eliminated rather than detected.

    Writer B loads the whole bundle first, while every case is still PENDING --
    the snapshot a pre-Phase-A service would have authorized against. Writer A
    then records a NO_MATCH that forbids B's merge.

    B's own ``resolve_case`` re-reads the bundle inside its write transaction,
    so the snapshot it captured earlier has no effect on the outcome. There is
    no interval in which an authorization decision can outlive the state it was
    computed from.
    """
    stale_bundle = writer_b._repository.load_workflow_bundle()
    assert all(case.status is ReviewStatus.PENDING for case in stale_bundle.cases())

    ac = _case_id(seeded_queue, "rec-a", "rec-c")
    ab = _case_id(seeded_queue, "rec-a", "rec-b")
    bc = _case_id(seeded_queue, "rec-b", "rec-c")

    service.resolve_case(
        ac,
        decision=HumanReviewDecision.NO_MATCH,
        reviewer_id="reviewer-a",
        expected_version=1,
    )
    service.resolve_case(
        ab,
        decision=HumanReviewDecision.MATCH,
        reviewer_id="reviewer-a",
        expected_version=1,
    )

    with pytest.raises(HumanReviewContradictionError):
        writer_b.resolve_case(
            bc,
            decision=HumanReviewDecision.MATCH,
            reviewer_id="reviewer-b",
            expected_version=1,
        )

    assert repository.get_case(bc).status is ReviewStatus.PENDING
    assert repository.list_events(bc) == ()


def test_the_final_history_is_valid_after_conflicting_attempts(
    repository: SqliteReviewCaseRepository,
    service: ReviewQueueService,
    writer_b: ReviewQueueService,
    seeded_queue: ReviewWorkflowState,
) -> None:
    """A refused attempt must leave a loadable queue with contiguous ordinals.

    This is the property A1 and A2 have to satisfy together: the serialized
    scope means two writers never compute the same ordinal, and the queue-global
    unique index means the database would refuse it if they somehow did. Either
    way the history stays 1, 2, 3 and the bundle keeps loading.
    """
    ac = _case_id(seeded_queue, "rec-a", "rec-c")
    ab = _case_id(seeded_queue, "rec-a", "rec-b")
    bc = _case_id(seeded_queue, "rec-b", "rec-c")

    service.resolve_case(
        ac, decision=HumanReviewDecision.NO_MATCH, reviewer_id="a", expected_version=1
    )
    writer_b.resolve_case(
        ab, decision=HumanReviewDecision.MATCH, reviewer_id="b", expected_version=1
    )
    with pytest.raises(HumanReviewContradictionError):
        service.resolve_case(
            bc, decision=HumanReviewDecision.MATCH, reviewer_id="a", expected_version=1
        )

    bundle = repository.load_workflow_bundle()

    assert [entry.resolution_sequence for entry in bundle.audit_entries] == [1, 2]
    assert bundle.next_resolution_sequence == 3


# --------------------------------------------------------------------------
# Lost-update protection is still there, and still separate
# --------------------------------------------------------------------------


def test_target_case_version_conflict_is_still_reported(
    repository: SqliteReviewCaseRepository,
    service: ReviewQueueService,
    writer_b: ReviewQueueService,
    seeded_queue: ReviewWorkflowState,
) -> None:
    """The serialized scope did not replace ``expected_version``.

    Two writers on the *same* case is a different failure from two writers on
    two cases, and it still answers ``ReviewConflictError`` so a reviewer is
    told to reload rather than being told their decision was unsafe.
    """
    ab = _case_id(seeded_queue, "rec-a", "rec-b")

    service.resolve_case(
        ab, decision=HumanReviewDecision.DEFER, reviewer_id="a", expected_version=1
    )

    with pytest.raises(ReviewConflictError):
        writer_b.resolve_case(
            ab, decision=HumanReviewDecision.DEFER, reviewer_id="b", expected_version=1
        )


# --------------------------------------------------------------------------
# The mechanism itself
# --------------------------------------------------------------------------


def test_the_resolution_scope_really_holds_a_write_transaction(
    repository: SqliteReviewCaseRepository,
    database: ReviewDatabase,
    second_connection: ReviewDatabase,
    seeded_queue: ReviewWorkflowState,
) -> None:
    """A second connection cannot write while the scope is open.

    Without this, the tests above would pass just as well against a service
    that had no scope at all, because they exercise it sequentially. This is the
    part that says the scope is a real lock rather than a comment: while it is
    held, another connection's ``BEGIN IMMEDIATE`` cannot acquire the file.
    """
    with repository.unit_of_work():
        assert database.connect().in_transaction is True
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            second_connection.connect().execute("BEGIN IMMEDIATE")

    assert database.connect().in_transaction is False


def test_a_refusal_inside_the_scope_releases_it_and_writes_nothing(
    repository: SqliteReviewCaseRepository,
    database: ReviewDatabase,
    service: ReviewQueueService,
    seeded_queue: ReviewWorkflowState,
) -> None:
    """A domain refusal must abandon the scope, not leave the lock held.

    Holding a write lock open after a refusal would wedge every later writer,
    which would turn a safe refusal into an outage.
    """
    ac = _case_id(seeded_queue, "rec-a", "rec-c")
    ab = _case_id(seeded_queue, "rec-a", "rec-b")
    bc = _case_id(seeded_queue, "rec-b", "rec-c")

    service.resolve_case(
        ac, decision=HumanReviewDecision.NO_MATCH, reviewer_id="a", expected_version=1
    )
    service.resolve_case(
        ab, decision=HumanReviewDecision.MATCH, reviewer_id="a", expected_version=1
    )

    with pytest.raises(HumanReviewContradictionError):
        service.resolve_case(
            bc, decision=HumanReviewDecision.MATCH, reviewer_id="a", expected_version=1
        )

    assert database.connect().in_transaction is False
    # And the queue is still writable afterwards.
    service.resolve_case(
        bc, decision=HumanReviewDecision.DEFER, reviewer_id="a", expected_version=1
    )
    assert repository.get_case(bc).status is ReviewStatus.DEFERRED


def test_the_bundle_load_inside_the_scope_sees_the_scope(
    repository: SqliteReviewCaseRepository,
    tmp_path: Path,
    seeded_queue: ReviewWorkflowState,
) -> None:
    """A read nested in the write scope joins it instead of opening a second one.

    If ``load_workflow_bundle`` opened its own DEFERRED transaction while an
    IMMEDIATE one was already held, SQLite would refuse the nested BEGIN -- so
    this is what makes the reentrant transaction load-bearing rather than tidy.
    """
    with repository.unit_of_work():
        first = repository.load_workflow_bundle()
        second = repository.load_workflow_bundle()

    assert first.cases() == second.cases() == seeded_queue.cases
