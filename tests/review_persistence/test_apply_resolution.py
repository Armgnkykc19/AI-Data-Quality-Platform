"""The repository write path: it records decisions, it never makes them.

``apply_resolution`` is the only method that can move a case out of PENDING.
Every test here attacks that from a different angle: the inputs it must refuse,
the concurrency it must survive, and the guarantee that a failure part-way
through leaves nothing behind.

No test constructs a resolved ``ReviewCase`` by hand. Each one runs the real
``ReviewWorkflow`` to produce the case and the audit entry, then feeds the
repository combinations of those genuine objects. That is what makes the
refusals meaningful: the material is always domain-produced, and what is
rejected is the *pairing*, which is the only thing a caller could get wrong
without the domain noticing.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace

import pytest

from entity_resolution.config import EntityResolutionConfig
from entity_resolution.models import ResolutionResult
from human_review.cases import generate_review_cases
from human_review.errors import ReviewCaseNotFoundError
from human_review.models import (
    HumanReviewDecision,
    ReviewCase,
    ReviewStatus,
    ReviewWorkflowState,
)
from human_review.reporting import resolution_snapshot
from human_review.workflow import ReviewWorkflow
from review_application.errors import (
    PersistedCaseIntegrityError,
    ReviewConflictError,
    ReviewEventIntegrityError,
)
from review_application.models import PersistedCase, ReviewEvent
from review_persistence.schema import DATABASE_SCHEMA_VERSION, REVIEW_CASE_EVENTS_TABLE
from review_persistence.sqlite.database import ReviewDatabase
from review_persistence.sqlite.review_repository import SqliteReviewCaseRepository
from tests.human_review.conftest import match_authorization_kwargs
from tests.review_persistence.conftest import FrozenClock

RESOLVED_AT = "2026-09-12T09:30:00Z"


# --------------------------------------------------------------------------
# Helpers: every resolved case below comes from the real Sprint 08 workflow
# --------------------------------------------------------------------------


def domain_resolution(
    state: ReviewWorkflowState,
    review_case_id: str,
    *,
    decision: HumanReviewDecision,
    reviewer_id: str | None = "reviewer-1",
    resolution: ResolutionResult | None = None,
    config: EntityResolutionConfig | None = None,
    occurred_at_utc: str = RESOLVED_AT,
) -> tuple[ReviewCase, ReviewEvent]:
    """Run ReviewWorkflow.resolve_case and project what it produced."""
    kwargs: dict[str, object] = {}
    if decision is HumanReviewDecision.MATCH:
        assert resolution is not None and config is not None
        kwargs = match_authorization_kwargs(resolution, config)

    updated = ReviewWorkflow(state).resolve_case(
        review_case_id,
        decision=decision,
        reviewer_id=reviewer_id,
        **kwargs,  # type: ignore[arg-type]
    )
    case = updated.case_by_id(review_case_id)
    assert case is not None
    event = ReviewEvent.from_audit_entry(
        updated.audit_trail[-1],
        occurred_at_utc=occurred_at_utc,
        schema_version=DATABASE_SCHEMA_VERSION,
    )
    return case, event


@pytest.fixture
def registered(
    repository: SqliteReviewCaseRepository,
    review_state: ReviewWorkflowState,
    resolution: ResolutionResult,
) -> PersistedCase:
    repository.register_workflow(
        review_state,
        entity_records=resolution.records,
        resolution_snapshot=resolution_snapshot(resolution),
        entity_resolution_config_path="configs/entity_resolution.yaml",
    )
    return repository.get_case(review_state.cases[0].review_case_id)


def stored_snapshot(repository: SqliteReviewCaseRepository, review_case_id: str) -> tuple:
    persisted = repository.get_case(review_case_id)
    return (
        persisted.status,
        persisted.version,
        persisted.created_at_utc,
        persisted.updated_at_utc,
        len(repository.list_events(review_case_id)),
    )


# --------------------------------------------------------------------------
# A decision is persisted exactly as the domain made it
# --------------------------------------------------------------------------


def test_applying_a_domain_resolution_bumps_the_version_and_appends_one_event(
    repository: SqliteReviewCaseRepository,
    registered: PersistedCase,
    review_state: ReviewWorkflowState,
) -> None:
    case, event = domain_resolution(
        review_state,
        registered.review_case_id,
        decision=HumanReviewDecision.NO_MATCH,
    )

    applied = repository.apply_resolution(
        case,
        expected_version=registered.version,
        event=event,
        now_utc=RESOLVED_AT,
    )

    assert applied.version == registered.version + 1
    assert applied.status is ReviewStatus.NO_MATCH
    reloaded = repository.get_case(registered.review_case_id)
    assert reloaded == applied
    assert reloaded.case == case

    (stored_event,) = repository.list_events(registered.review_case_id)
    assert stored_event.event_type.value == ReviewStatus.NO_MATCH.value
    assert stored_event.audit_entry_payload == event.audit_entry_payload
    assert stored_event.event_id is not None


def test_created_at_is_preserved_and_updated_at_comes_from_the_injected_clock(
    repository: SqliteReviewCaseRepository,
    registered: PersistedCase,
    review_state: ReviewWorkflowState,
    clock: FrozenClock,
) -> None:
    """Section 19: no SQLite localtime, no scattered datetime.now().

    The clock is the one injected into the repository, so a test can prove the
    timestamp advanced without depending on wall-clock time.
    """
    case, event = domain_resolution(
        review_state,
        registered.review_case_id,
        decision=HumanReviewDecision.DEFER,
    )
    clock.advance(3600)

    applied = repository.apply_resolution(
        case,
        expected_version=registered.version,
        event=event,
    )

    assert applied.created_at_utc == registered.created_at_utc
    assert applied.updated_at_utc != registered.updated_at_utc
    assert applied.updated_at_utc == "2026-09-12T09:00:00Z"
    assert repository.get_case(registered.review_case_id).updated_at_utc == applied.updated_at_utc


def test_defer_is_stored_as_the_deferred_status(
    repository: SqliteReviewCaseRepository,
    registered: PersistedCase,
    review_state: ReviewWorkflowState,
) -> None:
    case, event = domain_resolution(
        review_state,
        registered.review_case_id,
        decision=HumanReviewDecision.DEFER,
    )
    repository.apply_resolution(case, expected_version=1, event=event, now_utc=RESOLVED_AT)

    assert repository.get_case(registered.review_case_id).status is ReviewStatus.DEFERRED


# --------------------------------------------------------------------------
# Inputs the repository must refuse
# --------------------------------------------------------------------------


def test_a_pending_case_is_refused(
    repository: SqliteReviewCaseRepository,
    registered: PersistedCase,
    review_state: ReviewWorkflowState,
) -> None:
    # There is no such thing as "persist this case as resolved". The case must
    # already carry the domain's transition.
    _, event = domain_resolution(
        review_state,
        registered.review_case_id,
        decision=HumanReviewDecision.NO_MATCH,
    )
    with pytest.raises(PersistedCaseIntegrityError):
        repository.apply_resolution(
            registered.case,
            expected_version=1,
            event=event,
            now_utc=RESOLVED_AT,
        )
    assert stored_snapshot(repository, registered.review_case_id)[0] is ReviewStatus.PENDING


def test_a_lifecycle_event_cannot_stand_in_for_a_resolution(
    repository: SqliteReviewCaseRepository,
    registered: PersistedCase,
    review_state: ReviewWorkflowState,
) -> None:
    case, _ = domain_resolution(
        review_state,
        registered.review_case_id,
        decision=HumanReviewDecision.NO_MATCH,
    )
    lifecycle = ReviewEvent.case_created(
        registered.review_case_id,
        occurred_at_utc=RESOLVED_AT,
        schema_version=DATABASE_SCHEMA_VERSION,
    )

    with pytest.raises(ReviewEventIntegrityError):
        repository.apply_resolution(
            case,
            expected_version=1,
            event=lifecycle,
            now_utc=RESOLVED_AT,
        )
    assert repository.list_events(registered.review_case_id) == ()


def test_an_event_recording_a_different_decision_is_refused(
    repository: SqliteReviewCaseRepository,
    registered: PersistedCase,
    review_state: ReviewWorkflowState,
) -> None:
    """Both objects are genuine; only the pairing is wrong.

    This is the failure that would let persistence rewrite a decision: a case
    the domain made NO_MATCH, filed under a MATCH event. The audit payload and
    the case must agree, so it is refused.
    """
    no_match_case, _ = domain_resolution(
        review_state,
        registered.review_case_id,
        decision=HumanReviewDecision.NO_MATCH,
    )
    _, defer_event = domain_resolution(
        review_state,
        registered.review_case_id,
        decision=HumanReviewDecision.DEFER,
    )

    with pytest.raises(ReviewEventIntegrityError):
        repository.apply_resolution(
            no_match_case,
            expected_version=1,
            event=defer_event,
            now_utc=RESOLVED_AT,
        )
    assert stored_snapshot(repository, registered.review_case_id)[0] is ReviewStatus.PENDING


def test_an_event_naming_a_different_reviewer_is_refused(
    repository: SqliteReviewCaseRepository,
    registered: PersistedCase,
    review_state: ReviewWorkflowState,
) -> None:
    case, _ = domain_resolution(
        review_state,
        registered.review_case_id,
        decision=HumanReviewDecision.NO_MATCH,
        reviewer_id="reviewer-1",
    )
    _, other_event = domain_resolution(
        review_state,
        registered.review_case_id,
        decision=HumanReviewDecision.NO_MATCH,
        reviewer_id="reviewer-2",
    )

    with pytest.raises(ReviewEventIntegrityError):
        repository.apply_resolution(
            case,
            expected_version=1,
            event=other_event,
            now_utc=RESOLVED_AT,
        )


def test_an_event_at_a_different_sequence_is_refused(
    repository: SqliteReviewCaseRepository,
    registered: PersistedCase,
    review_state: ReviewWorkflowState,
) -> None:
    case, _ = domain_resolution(
        review_state,
        registered.review_case_id,
        decision=HumanReviewDecision.NO_MATCH,
    )
    # The same decision taken in a workflow that had already resolved something
    # else: identical in every way except where it sits in the audit trail.
    later_state = replace(review_state, next_resolution_sequence=7)
    _, later_event = domain_resolution(
        later_state,
        registered.review_case_id,
        decision=HumanReviewDecision.NO_MATCH,
    )
    assert later_event.resolution_sequence == 7

    with pytest.raises(ReviewEventIntegrityError):
        repository.apply_resolution(
            case,
            expected_version=1,
            event=later_event,
            now_utc=RESOLVED_AT,
        )


def test_a_case_whose_pair_disagrees_with_the_stored_row_is_refused(
    repository: SqliteReviewCaseRepository,
    registered: PersistedCase,
    resolution_config: EntityResolutionConfig,
) -> None:
    """Identity is the id plus the ordered record pair, and it is immutable."""
    from tests.human_review.conftest import make_review_resolution

    other_resolution = make_review_resolution("z-1", "z-2")
    other_state = generate_review_cases(other_resolution, config=resolution_config)
    other_case, other_event = domain_resolution(
        other_state,
        other_state.cases[0].review_case_id,
        decision=HumanReviewDecision.NO_MATCH,
    )

    # Same id as the stored case, different records.
    impostor = replace(other_case, review_case_id=registered.review_case_id)
    payload = dict(other_event.audit_entry_payload or {})
    payload["review_case_id"] = registered.review_case_id
    impostor_event = replace(
        other_event,
        review_case_id=registered.review_case_id,
        audit_entry_payload=payload,
    )

    with pytest.raises(PersistedCaseIntegrityError):
        repository.apply_resolution(
            impostor,
            expected_version=1,
            event=impostor_event,
            now_utc=RESOLVED_AT,
        )
    assert stored_snapshot(repository, registered.review_case_id)[0] is ReviewStatus.PENDING


def test_an_unregistered_case_is_not_found(
    repository: SqliteReviewCaseRepository,
    registered: PersistedCase,
    resolution_config: EntityResolutionConfig,
) -> None:
    from tests.human_review.conftest import make_review_resolution

    other = make_review_resolution("z-1", "z-2")
    other_state = generate_review_cases(other, config=resolution_config)
    case, event = domain_resolution(
        other_state,
        other_state.cases[0].review_case_id,
        decision=HumanReviewDecision.NO_MATCH,
    )

    with pytest.raises(ReviewCaseNotFoundError):
        repository.apply_resolution(case, expected_version=1, event=event, now_utc=RESOLVED_AT)


def test_an_already_resolved_case_is_terminal(
    repository: SqliteReviewCaseRepository,
    registered: PersistedCase,
    review_state: ReviewWorkflowState,
) -> None:
    case, event = domain_resolution(
        review_state,
        registered.review_case_id,
        decision=HumanReviewDecision.NO_MATCH,
    )
    repository.apply_resolution(case, expected_version=1, event=event, now_utc=RESOLVED_AT)
    before = stored_snapshot(repository, registered.review_case_id)

    # Correct version, but the case is resolved: not a race, an overwrite.
    with pytest.raises(PersistedCaseIntegrityError):
        repository.apply_resolution(case, expected_version=2, event=event, now_utc=RESOLVED_AT)
    assert stored_snapshot(repository, registered.review_case_id) == before


# --------------------------------------------------------------------------
# Optimistic concurrency
# --------------------------------------------------------------------------


def test_a_stale_expected_version_is_a_conflict_and_writes_nothing(
    repository: SqliteReviewCaseRepository,
    registered: PersistedCase,
    review_state: ReviewWorkflowState,
) -> None:
    case, event = domain_resolution(
        review_state,
        registered.review_case_id,
        decision=HumanReviewDecision.NO_MATCH,
    )
    before = stored_snapshot(repository, registered.review_case_id)

    with pytest.raises(ReviewConflictError) as raised:
        repository.apply_resolution(
            case,
            expected_version=registered.version + 5,
            event=event,
            now_utc=RESOLVED_AT,
        )
    assert raised.value.review_case_id == registered.review_case_id
    assert stored_snapshot(repository, registered.review_case_id) == before


def test_the_conditional_update_itself_refuses_a_superseded_version(
    repository: SqliteReviewCaseRepository,
    registered: PersistedCase,
    review_state: ReviewWorkflowState,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Section 7B: the database CAS is a second, independent protection.

    The pre-read check normally fires first, so this disables only that check --
    it does not change production behaviour, and it does not touch the write.
    What is left is the bare ``WHERE ... AND version = ?``, and it must still
    refuse and still write nothing.
    """
    case, event = domain_resolution(
        review_state,
        registered.review_case_id,
        decision=HumanReviewDecision.NO_MATCH,
    )
    monkeypatch.setattr(
        SqliteReviewCaseRepository,
        "_require_resolvable_case",
        staticmethod(lambda stored, resolved_case, expected_version: stored),
    )
    before = stored_snapshot(repository, registered.review_case_id)

    with pytest.raises(ReviewConflictError):
        repository.apply_resolution(
            case,
            expected_version=registered.version + 1,
            event=event,
            now_utc=RESOLVED_AT,
        )

    assert stored_snapshot(repository, registered.review_case_id) == before
    assert repository.list_events(registered.review_case_id) == ()


# --------------------------------------------------------------------------
# Atomicity
# --------------------------------------------------------------------------


def test_a_failure_after_the_case_update_rolls_the_case_back_too(
    repository: SqliteReviewCaseRepository,
    registered: PersistedCase,
    review_state: ReviewWorkflowState,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Section 20: the mandatory failure injection.

    The case UPDATE has already run when the event append fails. If the two
    were not one transaction, the database would be left with a resolved case
    at version 2 and no history saying who resolved it -- an audit trail with a
    hole in it, which is worse than a failed write.

    No production flag exists for this. The seam is the private append method,
    replaced here for the duration of one call.
    """
    case, event = domain_resolution(
        review_state,
        registered.review_case_id,
        decision=HumanReviewDecision.NO_MATCH,
    )
    before = stored_snapshot(repository, registered.review_case_id)

    def explode(connection: object, appended: ReviewEvent) -> None:
        raise RuntimeError("history append failed")

    monkeypatch.setattr(SqliteReviewCaseRepository, "_append_event", staticmethod(explode))

    with pytest.raises(RuntimeError, match="history append failed"):
        repository.apply_resolution(
            case,
            expected_version=1,
            event=event,
            now_utc=RESOLVED_AT,
        )

    assert stored_snapshot(repository, registered.review_case_id) == before
    assert repository.list_events(registered.review_case_id) == ()


def test_the_rollback_survives_reopening_the_database(
    repository: SqliteReviewCaseRepository,
    database: ReviewDatabase,
    registered: PersistedCase,
    review_state: ReviewWorkflowState,
    clock: FrozenClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An in-memory rollback that had not actually reached the file would still
    # read back correctly on the same connection. Reopening proves the commit
    # never happened.
    case, event = domain_resolution(
        review_state,
        registered.review_case_id,
        decision=HumanReviewDecision.NO_MATCH,
    )

    def explode(connection: object, appended: ReviewEvent) -> None:
        raise RuntimeError("history append failed")

    monkeypatch.setattr(SqliteReviewCaseRepository, "_append_event", staticmethod(explode))
    with pytest.raises(RuntimeError):
        repository.apply_resolution(case, expected_version=1, event=event, now_utc=RESOLVED_AT)

    monkeypatch.undo()
    database.close()
    database.initialize()
    reopened = SqliteReviewCaseRepository(database, clock=clock)

    persisted = reopened.get_case(registered.review_case_id)
    assert persisted.status is ReviewStatus.PENDING
    assert persisted.version == 1
    assert persisted.updated_at_utc == registered.updated_at_utc
    assert reopened.list_events(registered.review_case_id) == ()


def test_an_event_insert_rejected_by_the_schema_rolls_the_case_back(
    repository: SqliteReviewCaseRepository,
    database: ReviewDatabase,
    registered: PersistedCase,
    review_state: ReviewWorkflowState,
) -> None:
    """The schema CHECK is the last line, and it too must roll the case back."""
    case, event = domain_resolution(
        review_state,
        registered.review_case_id,
        decision=HumanReviewDecision.NO_MATCH,
    )
    persisted_before = repository.get_case(registered.review_case_id)

    # An event already occupying resolution ordinal 1 for this case, which the
    # partial unique index forbids a second row from claiming. The payload is
    # internally consistent so it is the index, not a malformed row, that fires.
    database.connect().execute(
        f"INSERT INTO {REVIEW_CASE_EVENTS_TABLE} "
        "(review_case_id, event_type, resolution_sequence, reviewer_id, audit_entry_json, "
        "suggestion_id, occurred_at_utc, schema_version) "
        "VALUES (?, 'NO_MATCH', 1, 'reviewer-1', ?, NULL, ?, ?)",
        (
            registered.review_case_id,
            json.dumps(event.audit_entry_payload),
            RESOLVED_AT,
            DATABASE_SCHEMA_VERSION,
        ),
    )

    with pytest.raises(sqlite3.IntegrityError):
        repository.apply_resolution(case, expected_version=1, event=event, now_utc=RESOLVED_AT)

    persisted_after = repository.get_case(registered.review_case_id)
    assert persisted_after == persisted_before
    assert persisted_after.status is ReviewStatus.PENDING
    # The pre-existing row is still the only one: the failed append added none.
    assert len(repository.list_events(registered.review_case_id)) == 1
