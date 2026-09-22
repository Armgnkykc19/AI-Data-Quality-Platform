"""Append-only history, and the workflow rebuilt from it.

Three things are established here.

``list_events`` returns domain values in append order, and the history it reads
is append-only in fact, not only by convention.

``load_workflow_bundle`` reconciles two independent stored records of the same
decision -- the resolved case and its event -- and fails closed when they
disagree. Every tampering test below writes SQL directly, because that is the
only way to produce a state the repository itself cannot create.

And the reconstructed ``ReviewWorkflowState`` is the Sprint 08 state: what
``workflow_state_to_dict`` produces from persisted material must equal what it
produces from the in-memory workflow, or the review report would depend on
whether the queue had been through a database.
"""

from __future__ import annotations

import json

import pytest

from entity_resolution.config import EntityResolutionConfig
from entity_resolution.models import RecordPair, ResolutionResult
from human_review.cases import generate_review_cases
from human_review.models import (
    HumanReviewDecision,
    ReviewStatus,
    ReviewWorkflowState,
)
from human_review.reporting import resolution_snapshot, workflow_state_to_dict
from human_review.workflow import ReviewWorkflow
from review_application.errors import ReviewEventIntegrityError
from review_application.models import ReviewEventType
from review_application.service import ReviewQueueService
from review_persistence.config import ReviewPersistenceConfig
from review_persistence.schema import (
    DATABASE_SCHEMA_VERSION,
    REVIEW_CASE_EVENTS_TABLE,
    REVIEW_CASES_TABLE,
)
from review_persistence.sqlite.database import ReviewDatabase, open_review_database
from review_persistence.sqlite.review_repository import SqliteReviewCaseRepository
from tests.human_review.conftest import (
    make_chain_review_resolution,
    make_triangle_review_resolution,
)
from tests.review_persistence.conftest import (
    FrozenClock,
    bound_repository,
    seed_eventless_resolved_case,
)

CONFIG_PATH = "configs/entity_resolution.yaml"


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


@pytest.fixture
def service(repository: SqliteReviewCaseRepository, clock: FrozenClock) -> ReviewQueueService:
    return ReviewQueueService(repository, clock=clock)


def register(
    repository: SqliteReviewCaseRepository,
    state: ReviewWorkflowState,
    resolution: ResolutionResult,
) -> None:
    repository.register_workflow(
        state,
        entity_records=resolution.records,
        resolution_snapshot=resolution_snapshot(resolution),
        entity_resolution_config_path=CONFIG_PATH,
    )


@pytest.fixture
def chain(
    repository: SqliteReviewCaseRepository,
    resolution_config: EntityResolutionConfig,
) -> tuple[ReviewWorkflowState, ResolutionResult]:
    """Three records, two PENDING review cases. Room for two resolutions."""
    resolution = make_chain_review_resolution(("rec-a", "rec-b", "rec-c"))
    state = generate_review_cases(resolution, config=resolution_config)
    assert len(state.cases) == 2
    register(repository, state, resolution)
    return state, resolution


def case_payload(database: ReviewDatabase, review_case_id: str) -> dict:
    row = (
        database.connect()
        .execute(
            f"SELECT case_payload_json FROM {REVIEW_CASES_TABLE} WHERE review_case_id = ?",
            (review_case_id,),
        )
        .fetchone()
    )
    return json.loads(row["case_payload_json"])


def overwrite_case_payload(
    database: ReviewDatabase,
    review_case_id: str,
    payload: dict,
) -> None:
    """Rewrite a stored case out from under the repository.

    Only a test does this. It is how a "case and event disagree" state is
    produced at all, since no repository method can create one.
    """
    database.connect().execute(
        f"UPDATE {REVIEW_CASES_TABLE} SET case_payload_json = ?, status = ? "
        "WHERE review_case_id = ?",
        (
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
            payload["status"],
            review_case_id,
        ),
    )


# --------------------------------------------------------------------------
# list_events
# --------------------------------------------------------------------------


def test_list_events_returns_domain_values_not_rows(
    service: ReviewQueueService,
    repository: SqliteReviewCaseRepository,
    chain: tuple[ReviewWorkflowState, ResolutionResult],
) -> None:
    state, _ = chain
    case_id = state.cases[0].review_case_id
    result = service.resolve_case(
        case_id,
        decision=HumanReviewDecision.NO_MATCH,
        reviewer_id="reviewer-1",
        expected_version=1,
    )

    (event,) = repository.list_events(case_id)
    assert isinstance(event.event_type, ReviewEventType)
    assert event.is_resolution
    assert event.audit_entry_payload == result.audit_entry.to_dict()
    assert event.schema_version == DATABASE_SCHEMA_VERSION
    assert event.resolution_sequence == 1
    assert event.suggestion_id is None


def test_events_are_returned_in_append_order(
    service: ReviewQueueService,
    repository: SqliteReviewCaseRepository,
    database: ReviewDatabase,
    chain: tuple[ReviewWorkflowState, ResolutionResult],
) -> None:
    """Ordering is by event_id, which is the only thing a shared second cannot blur."""
    state, _ = chain
    case_id = state.cases[0].review_case_id

    # A lifecycle row appended before the resolution, at the same timestamp.
    database.connect().execute(
        f"INSERT INTO {REVIEW_CASE_EVENTS_TABLE} "
        "(review_queue_id, review_case_id, event_type, resolution_sequence, reviewer_id, "
        "audit_entry_json, suggestion_id, occurred_at_utc, schema_version) "
        "VALUES (?, ?, 'CASE_CREATED', NULL, NULL, NULL, NULL, ?, ?)",
        (
            repository.review_queue_id,
            case_id,
            "2026-09-12T08:00:00Z",
            DATABASE_SCHEMA_VERSION,
        ),
    )
    service.resolve_case(
        case_id,
        decision=HumanReviewDecision.DEFER,
        reviewer_id="reviewer-1",
        expected_version=1,
    )

    events = repository.list_events(case_id)
    assert [event.event_type for event in events] == [
        ReviewEventType.CASE_CREATED,
        ReviewEventType.DEFERRED,
    ]
    assert [event.event_id for event in events] == sorted(
        event.event_id for event in events if event.event_id is not None
    )


def test_list_events_is_scoped_to_one_case(
    service: ReviewQueueService,
    repository: SqliteReviewCaseRepository,
    chain: tuple[ReviewWorkflowState, ResolutionResult],
) -> None:
    state, _ = chain
    first, second = state.cases
    service.resolve_case(
        first.review_case_id,
        decision=HumanReviewDecision.DEFER,
        reviewer_id="reviewer-1",
        expected_version=1,
    )

    assert len(repository.list_events(first.review_case_id)) == 1
    assert repository.list_events(second.review_case_id) == ()


# --------------------------------------------------------------------------
# Workflow reconstruction
# --------------------------------------------------------------------------


def test_the_audit_trail_is_rebuilt_from_events(
    service: ReviewQueueService,
    repository: SqliteReviewCaseRepository,
    chain: tuple[ReviewWorkflowState, ResolutionResult],
) -> None:
    state, _ = chain
    first, second = state.cases

    service.resolve_case(
        first.review_case_id,
        decision=HumanReviewDecision.NO_MATCH,
        reviewer_id="reviewer-1",
        expected_version=1,
    )
    second_result = service.resolve_case(
        second.review_case_id,
        decision=HumanReviewDecision.DEFER,
        reviewer_id="reviewer-2",
        expected_version=1,
    )

    bundle = repository.load_workflow_bundle()
    assert bundle.next_resolution_sequence == 3
    assert [entry.resolution_sequence for entry in bundle.audit_entries] == [1, 2]
    assert bundle.audit_entries == second_result.workflow_state.audit_trail


def test_a_queue_with_no_events_still_derives_its_next_sequence(
    repository: SqliteReviewCaseRepository,
    database: ReviewDatabase,
    resolution_config: EntityResolutionConfig,
) -> None:
    """Bootstrap compatibility: a Phase C database has resolved cases and no events.

    The sequence and the audit entry are both recoverable from the resolution
    the domain already stamped on the case, so an imported queue stays usable
    rather than failing to load.

    The eventless row is seeded in SQL because no supported operation produces
    one -- see ``seed_eventless_resolved_case``. That is the shape being
    covered: a database written before the event table existed.
    """
    resolution = make_triangle_review_resolution(("rec-a", "rec-b", "rec-c"))
    state = generate_review_cases(resolution, config=resolution_config)
    ac_case = next(
        case for case in state.cases if case.pair == RecordPair.ordered("rec-a", "rec-c")
    )
    resolved = ReviewWorkflow(state).resolve_case(
        ac_case.review_case_id,
        decision=HumanReviewDecision.NO_MATCH,
        reviewer_id="reviewer-1",
    )
    seed_eventless_resolved_case(
        repository,
        database,
        pending_state=state,
        resolved_state=resolved,
        review_case_id=ac_case.review_case_id,
        resolution=resolution,
        entity_resolution_config_path=CONFIG_PATH,
    )

    bundle = repository.load_workflow_bundle()

    assert bundle.next_resolution_sequence == 2
    assert bundle.audit_entries == resolved.audit_trail


def test_imported_and_newly_resolved_decisions_share_one_trail(
    repository: SqliteReviewCaseRepository,
    database: ReviewDatabase,
    service: ReviewQueueService,
    resolution_config: EntityResolutionConfig,
) -> None:
    """A legacy eventless decision and one resolved after it must interleave.

    The imported decision left no event; the new one did. Both belong to the
    same audit trail, and the next sequence must account for both.
    """
    resolution = make_triangle_review_resolution(("rec-a", "rec-b", "rec-c"))
    state = generate_review_cases(resolution, config=resolution_config)
    ac_case = next(
        case for case in state.cases if case.pair == RecordPair.ordered("rec-a", "rec-c")
    )
    seed_eventless_resolved_case(
        repository,
        database,
        pending_state=state,
        resolved_state=ReviewWorkflow(state).resolve_case(
            ac_case.review_case_id,
            decision=HumanReviewDecision.NO_MATCH,
            reviewer_id="reviewer-1",
        ),
        review_case_id=ac_case.review_case_id,
        resolution=resolution,
        entity_resolution_config_path=CONFIG_PATH,
    )

    ab_case = next(
        case for case in state.cases if case.pair == RecordPair.ordered("rec-a", "rec-b")
    )
    service.resolve_case(
        ab_case.review_case_id,
        decision=HumanReviewDecision.MATCH,
        reviewer_id="reviewer-2",
        expected_version=1,
    )

    bundle = repository.load_workflow_bundle()
    assert bundle.next_resolution_sequence == 3
    assert [entry.resolution_sequence for entry in bundle.audit_entries] == [1, 2]
    assert [entry.human_decision for entry in bundle.audit_entries] == ["NO_MATCH", "MATCH"]


# --------------------------------------------------------------------------
# Disagreement between the two stored records fails closed
# --------------------------------------------------------------------------


def test_a_case_relabelled_behind_its_event_is_rejected(
    service: ReviewQueueService,
    repository: SqliteReviewCaseRepository,
    database: ReviewDatabase,
    chain: tuple[ReviewWorkflowState, ResolutionResult],
) -> None:
    """The event says NO_MATCH; the case now says DEFERRED. One of them is a lie."""
    state, _ = chain
    case_id = state.cases[0].review_case_id
    service.resolve_case(
        case_id,
        decision=HumanReviewDecision.NO_MATCH,
        reviewer_id="reviewer-1",
        expected_version=1,
    )

    payload = case_payload(database, case_id)
    payload["status"] = ReviewStatus.DEFERRED.value
    payload["resolution"]["human_decision"] = HumanReviewDecision.DEFER.value
    overwrite_case_payload(database, case_id, payload)

    with pytest.raises(ReviewEventIntegrityError, match="resolution event records"):
        repository.load_workflow_bundle()


def test_a_sequence_that_disagrees_with_its_event_is_rejected(
    service: ReviewQueueService,
    repository: SqliteReviewCaseRepository,
    database: ReviewDatabase,
    chain: tuple[ReviewWorkflowState, ResolutionResult],
) -> None:
    state, _ = chain
    case_id = state.cases[0].review_case_id
    service.resolve_case(
        case_id,
        decision=HumanReviewDecision.NO_MATCH,
        reviewer_id="reviewer-1",
        expected_version=1,
    )

    payload = case_payload(database, case_id)
    payload["resolution"]["resolution_sequence"] = 5
    overwrite_case_payload(database, case_id, payload)

    with pytest.raises(ReviewEventIntegrityError, match="sequence"):
        repository.load_workflow_bundle()


def test_a_reviewer_that_disagrees_with_its_event_is_rejected(
    service: ReviewQueueService,
    repository: SqliteReviewCaseRepository,
    database: ReviewDatabase,
    chain: tuple[ReviewWorkflowState, ResolutionResult],
) -> None:
    state, _ = chain
    case_id = state.cases[0].review_case_id
    service.resolve_case(
        case_id,
        decision=HumanReviewDecision.NO_MATCH,
        reviewer_id="reviewer-1",
        expected_version=1,
    )

    payload = case_payload(database, case_id)
    payload["resolution"]["reviewer_id"] = "someone-else"
    overwrite_case_payload(database, case_id, payload)

    with pytest.raises(ReviewEventIntegrityError, match="reviewer"):
        repository.load_workflow_bundle()


def test_a_second_resolution_event_for_one_case_is_rejected(
    service: ReviewQueueService,
    repository: SqliteReviewCaseRepository,
    database: ReviewDatabase,
    chain: tuple[ReviewWorkflowState, ResolutionResult],
) -> None:
    """A case can only be resolved once, so it can only have one such event."""
    state, _ = chain
    case_id = state.cases[0].review_case_id
    result = service.resolve_case(
        case_id,
        decision=HumanReviewDecision.NO_MATCH,
        reviewer_id="reviewer-1",
        expected_version=1,
    )

    payload = dict(result.audit_entry.to_dict())
    payload["resolution_sequence"] = 2
    database.connect().execute(
        f"INSERT INTO {REVIEW_CASE_EVENTS_TABLE} "
        "(review_queue_id, review_case_id, event_type, resolution_sequence, reviewer_id, "
        "audit_entry_json, suggestion_id, occurred_at_utc, schema_version) "
        "VALUES (?, ?, 'NO_MATCH', 2, 'reviewer-1', ?, NULL, ?, ?)",
        (
            repository.review_queue_id,
            case_id,
            json.dumps(payload),
            "2026-09-12T10:00:00Z",
            DATABASE_SCHEMA_VERSION,
        ),
    )

    with pytest.raises(ReviewEventIntegrityError, match="more than one resolution"):
        repository.load_workflow_bundle()


def test_a_gap_in_the_resolution_sequence_is_rejected(
    service: ReviewQueueService,
    repository: SqliteReviewCaseRepository,
    database: ReviewDatabase,
    chain: tuple[ReviewWorkflowState, ResolutionResult],
) -> None:
    """Sprint 08 stamps 1, 2, 3 with no gaps. A gap means a resolution was lost."""
    state, _ = chain
    first, second = state.cases
    service.resolve_case(
        first.review_case_id,
        decision=HumanReviewDecision.NO_MATCH,
        reviewer_id="reviewer-1",
        expected_version=1,
    )
    result = service.resolve_case(
        second.review_case_id,
        decision=HumanReviewDecision.DEFER,
        reviewer_id="reviewer-2",
        expected_version=1,
    )
    assert result.audit_entry.resolution_sequence == 2

    # Move the second resolution to ordinal 4, consistently, in both records.
    payload = case_payload(database, second.review_case_id)
    payload["resolution"]["resolution_sequence"] = 4
    overwrite_case_payload(database, second.review_case_id, payload)
    entry_payload = dict(result.audit_entry.to_dict())
    entry_payload["resolution_sequence"] = 4
    database.connect().execute(
        f"UPDATE {REVIEW_CASE_EVENTS_TABLE} SET resolution_sequence = 4, audit_entry_json = ? "
        "WHERE review_case_id = ? AND resolution_sequence = 2",
        (json.dumps(entry_payload), second.review_case_id),
    )

    with pytest.raises(ReviewEventIntegrityError, match="not contiguous"):
        repository.load_workflow_bundle()


def test_an_event_cannot_reference_a_case_that_does_not_exist(
    database: ReviewDatabase,
    chain: tuple[ReviewWorkflowState, ResolutionResult],
) -> None:
    """The foreign key makes an orphan event unrepresentable, not merely unlikely."""
    import sqlite3

    assert database.foreign_keys_enabled()
    with pytest.raises(sqlite3.IntegrityError):
        database.connect().execute(
            f"INSERT INTO {REVIEW_CASE_EVENTS_TABLE} "
            "(review_case_id, event_type, resolution_sequence, reviewer_id, audit_entry_json, "
            "suggestion_id, occurred_at_utc, schema_version) "
            "VALUES ('RC-ghost', 'NO_MATCH', 1, NULL, '{}', NULL, '2026-09-12T10:00:00Z', "
            "'1.0.0')",
        )


# --------------------------------------------------------------------------
# Sprint 08 report compatibility
# --------------------------------------------------------------------------


def test_the_reconstructed_state_equals_the_in_memory_sprint_08_state(
    service: ReviewQueueService,
    repository: SqliteReviewCaseRepository,
    chain: tuple[ReviewWorkflowState, ResolutionResult],
) -> None:
    """Section 22: the report must not depend on whether a database was involved.

    ``workflow_state_to_dict`` is the Sprint 08 report contract at schema
    version 1.0.0. What it produces from the reconstructed bundle has to be
    identical to what it produces from the workflow object the domain returned.
    """
    state, _ = chain
    first, second = state.cases
    service.resolve_case(
        first.review_case_id,
        decision=HumanReviewDecision.NO_MATCH,
        reviewer_id="reviewer-1",
        expected_version=1,
    )
    final = service.resolve_case(
        second.review_case_id,
        decision=HumanReviewDecision.DEFER,
        reviewer_id="reviewer-2",
        expected_version=1,
    )

    reconstructed = repository.load_workflow_bundle().to_workflow_state()

    assert workflow_state_to_dict(reconstructed) == workflow_state_to_dict(final.workflow_state)


def test_report_equivalence_holds_for_an_imported_decision_too(
    repository: SqliteReviewCaseRepository,
    database: ReviewDatabase,
    service: ReviewQueueService,
    resolution_config: EntityResolutionConfig,
) -> None:
    resolution = make_triangle_review_resolution(("rec-a", "rec-b", "rec-c"))
    state = generate_review_cases(resolution, config=resolution_config)
    ac_case = next(
        case for case in state.cases if case.pair == RecordPair.ordered("rec-a", "rec-c")
    )
    seed_eventless_resolved_case(
        repository,
        database,
        pending_state=state,
        resolved_state=ReviewWorkflow(state).resolve_case(
            ac_case.review_case_id,
            decision=HumanReviewDecision.NO_MATCH,
            reviewer_id="reviewer-1",
        ),
        review_case_id=ac_case.review_case_id,
        resolution=resolution,
        entity_resolution_config_path=CONFIG_PATH,
    )
    ab_case = next(
        case for case in state.cases if case.pair == RecordPair.ordered("rec-a", "rec-b")
    )
    final = service.resolve_case(
        ab_case.review_case_id,
        decision=HumanReviewDecision.MATCH,
        reviewer_id="reviewer-2",
        expected_version=1,
    )

    reconstructed = repository.load_workflow_bundle().to_workflow_state()

    assert workflow_state_to_dict(reconstructed) == workflow_state_to_dict(final.workflow_state)


# --------------------------------------------------------------------------
# Restart durability
# --------------------------------------------------------------------------


def test_a_decision_survives_closing_and_reopening_the_database(
    persistence_config: ReviewPersistenceConfig,
    resolution_config: EntityResolutionConfig,
    clock: FrozenClock,
) -> None:
    """Section 21: a real file database, closed and reopened from scratch.

    Everything the next reviewer needs must come back: the decision, the
    version, the history, the audit trail, the next sequence, and the whole
    authorization context. Losing any part of the context would not look like a
    failure -- it would look like a MATCH becoming allowed.
    """
    resolution = make_triangle_review_resolution(("rec-a", "rec-b", "rec-c"))
    state = generate_review_cases(resolution, config=resolution_config)

    database = open_review_database(persistence_config, clock=clock)
    repository = bound_repository(database, clock)
    register(repository, state, resolution)
    case_id = state.cases[0].review_case_id
    before = ReviewQueueService(repository, clock=clock).resolve_case(
        case_id,
        decision=HumanReviewDecision.NO_MATCH,
        reviewer_id="reviewer-1",
        expected_version=1,
    )
    database.close()

    reopened = open_review_database(persistence_config, clock=clock)
    try:
        fresh_repository = bound_repository(reopened, clock)
        fresh_service = ReviewQueueService(fresh_repository, clock=clock)

        persisted = fresh_repository.get_case(case_id)
        assert persisted == before.persisted_case
        assert persisted.status is ReviewStatus.NO_MATCH
        assert persisted.version == 2
        assert persisted.case.resolution == before.workflow_state.case_by_id(case_id).resolution

        (event,) = fresh_repository.list_events(case_id)
        assert event.audit_entry_payload == before.audit_entry.to_dict()

        bundle = fresh_repository.load_workflow_bundle()
        assert bundle.audit_entries == before.workflow_state.audit_trail
        assert bundle.next_resolution_sequence == 2
        assert bundle.entity_resolution_config_path == CONFIG_PATH
        assert {record.record_id for record in bundle.entity_records} == {
            "rec-a",
            "rec-b",
            "rec-c",
        }
        assert bundle.resolution_snapshot == resolution_snapshot(resolution)

        # And the queue is still usable: the next decision continues the trail.
        remaining = next(
            persisted_case
            for persisted_case in bundle.persisted_cases
            if persisted_case.status is ReviewStatus.PENDING
        )
        after = fresh_service.resolve_case(
            remaining.review_case_id,
            decision=HumanReviewDecision.DEFER,
            reviewer_id="reviewer-2",
            expected_version=remaining.version,
        )
        assert after.audit_entry.resolution_sequence == 2
        assert fresh_repository.load_workflow_bundle().next_resolution_sequence == 3
    finally:
        reopened.close()
