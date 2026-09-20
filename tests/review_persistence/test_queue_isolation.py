"""Two tenants, one database file, and no way across the boundary.

This is the file the whole tenant-ownership design exists for. Every test here
uses **one** ``ReviewDatabase`` and two repositories bound to two queues owned
by two different organizations -- which is the realistic shape, because a
single SQLite file is the deployment.

The scenario that makes this necessary rather than decorative: a review case id
is not globally unique. ``human_review.ids.stable_review_case_id`` derives it
from a SHA-256 digest of the reviewed record pair, and record identifiers come
from customer data -- ``entity_resolution.records`` falls back to ``row-{n}``
when a source carries no identifier column. Two organizations uploading two
unrelated files therefore produce the same ``RC-...`` routinely. Every test
below registers *the same workflow* into both queues, so the review case ids
genuinely collide rather than merely being different strings.
"""

from __future__ import annotations

import pytest

from entity_resolution.config import EntityResolutionConfig
from entity_resolution.models import ResolutionResult
from human_review.errors import ReviewCaseNotFoundError
from human_review.models import (
    HumanReviewDecision,
    ReviewStatus,
    ReviewWorkflowState,
)
from human_review.reporting import resolution_snapshot
from human_review.workflow import ReviewWorkflow
from review_application.errors import (
    ReviewQueueNotFoundError,
    ReviewWorkflowContextConflictError,
)
from review_application.models import ReviewEvent
from review_persistence.schema import DATABASE_SCHEMA_VERSION
from review_persistence.sqlite.database import ReviewDatabase
from review_persistence.sqlite.review_repository import SqliteReviewCaseRepository
from review_persistence.sqlite.tenant_repository import SqliteTenantRepository
from tests.human_review.conftest import make_record, match_authorization_kwargs
from tests.review_persistence.semantic_fixtures import make_suggestion

CONFIG_PATH = "configs/entity_resolution.yaml"
RESOLVED_AT = "2026-09-12T09:00:00Z"


def register(
    repository: SqliteReviewCaseRepository,
    state: ReviewWorkflowState,
    resolution: ResolutionResult,
    *,
    config_path: str | None = CONFIG_PATH,
) -> None:
    repository.register_workflow(
        state,
        entity_records=resolution.records,
        resolution_snapshot=resolution_snapshot(resolution),
        entity_resolution_config_path=config_path,
    )


def register_records(
    repository: SqliteReviewCaseRepository,
    state: ReviewWorkflowState,
    resolution: ResolutionResult,
    *,
    records: tuple,
) -> None:
    """Register with an explicit record set, so the context fingerprint can differ."""
    repository.register_workflow(
        state,
        entity_records=records,
        resolution_snapshot=resolution_snapshot(resolution),
        entity_resolution_config_path=CONFIG_PATH,
    )


def records_by_id(resolution: ResolutionResult) -> dict:
    return {record.record_id: record for record in resolution.records}


def resolve_in(
    repository: SqliteReviewCaseRepository,
    state: ReviewWorkflowState,
    review_case_id: str,
    *,
    decision: HumanReviewDecision = HumanReviewDecision.NO_MATCH,
    reviewer_id: str = "reviewer-1",
    expected_version: int = 1,
    resolution: ResolutionResult | None = None,
    config: EntityResolutionConfig | None = None,
) -> None:
    """Resolve through the real Sprint 08 workflow, then persist the result."""
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
    repository.apply_resolution(
        case,
        expected_version=expected_version,
        event=ReviewEvent.from_audit_entry(
            updated.audit_trail[-1],
            occurred_at_utc=RESOLVED_AT,
            schema_version=DATABASE_SCHEMA_VERSION,
        ),
        now_utc=RESOLVED_AT,
    )


@pytest.fixture
def both_queues(
    repository: SqliteReviewCaseRepository,
    second_repository: SqliteReviewCaseRepository,
    review_state: ReviewWorkflowState,
    resolution: ResolutionResult,
) -> str:
    """The same workflow registered into two tenants' queues. Returns the shared id."""
    register(repository, review_state, resolution)
    register(second_repository, review_state, resolution)
    return review_state.cases[0].review_case_id


# --------------------------------------------------------------------------
# The collision itself
# --------------------------------------------------------------------------


def test_the_same_review_case_id_persists_in_two_queues(both_queues: str) -> None:
    """The invariant schema 1.0.0 could not express.

    Under a globally unique review_case_id the second registration would have
    failed -- or, far worse, silently attached the second tenant's workflow to
    the first tenant's case.
    """
    assert both_queues.startswith("RC-")


def test_each_queue_reads_back_its_own_case(
    repository: SqliteReviewCaseRepository,
    second_repository: SqliteReviewCaseRepository,
    both_queues: str,
) -> None:
    first = repository.get_case(both_queues)
    second = second_repository.get_case(both_queues)

    assert first.review_case_id == second.review_case_id == both_queues
    # Two rows, in two queues, each complete and each its own.
    assert first.status is ReviewStatus.PENDING
    assert second.status is ReviewStatus.PENDING


def test_a_queue_never_sees_another_queues_case(
    repository: SqliteReviewCaseRepository,
    second_repository: SqliteReviewCaseRepository,
    review_state: ReviewWorkflowState,
    resolution: ResolutionResult,
) -> None:
    """Registered in one queue only: absent from the other, as if it never existed."""
    register(repository, review_state, resolution)
    case_id = review_state.cases[0].review_case_id

    with pytest.raises(ReviewCaseNotFoundError):
        second_repository.get_case(case_id)


def test_listing_is_scoped_to_one_queue(
    repository: SqliteReviewCaseRepository,
    second_repository: SqliteReviewCaseRepository,
    review_state: ReviewWorkflowState,
    resolution: ResolutionResult,
) -> None:
    """A count that crossed the boundary would leak how much work another tenant has."""
    register(repository, review_state, resolution)

    assert len(repository.list_cases()) == len(review_state.cases)
    assert second_repository.list_cases() == ()

    register(second_repository, review_state, resolution)

    assert len(repository.list_cases()) == len(review_state.cases)
    assert len(second_repository.list_cases()) == len(review_state.cases)


# --------------------------------------------------------------------------
# Workflow context
# --------------------------------------------------------------------------


def test_each_queue_stores_its_own_context(
    repository: SqliteReviewCaseRepository,
    second_repository: SqliteReviewCaseRepository,
    both_queues: str,
) -> None:
    first = repository.workflow_context()
    second = second_repository.workflow_context()

    assert first is not None
    assert second is not None
    # Identical content, two independent rows: registering the same workflow
    # into a second queue is a first registration there, not a replay.
    assert first.fingerprint == second.fingerprint


def test_a_queue_with_no_context_is_unaffected_by_another_queues_context(
    repository: SqliteReviewCaseRepository,
    second_repository: SqliteReviewCaseRepository,
    review_state: ReviewWorkflowState,
    resolution: ResolutionResult,
) -> None:
    register(repository, review_state, resolution)

    assert second_repository.workflow_context() is None


def test_a_changed_context_conflicts_only_in_the_queue_that_stored_one(
    repository: SqliteReviewCaseRepository,
    second_repository: SqliteReviewCaseRepository,
    review_state: ReviewWorkflowState,
    resolution: ResolutionResult,
) -> None:
    """The fail-closed guarantee, now scoped.

    A different record set must still be refused where a context is already
    stored -- human decisions there were authorized against it -- and must
    still be accepted in a queue that has none.
    """
    register(repository, review_state, resolution)

    # The same cases, registered with one extra entity record. That is the
    # realistic shape of a changed context -- an edited input file -- and it
    # changes the stored fingerprint without making the context unusable.
    widened = (*resolution.records, make_record("c-1", first_name="Veli", last_name="Demir"))

    with pytest.raises(ReviewWorkflowContextConflictError):
        register_records(repository, review_state, resolution, records=widened)

    # The other tenant has no stored context, so the same registration lands.
    register_records(second_repository, review_state, resolution, records=widened)
    assert second_repository.workflow_context() is not None


def test_a_different_config_path_conflicts_only_in_its_own_queue(
    repository: SqliteReviewCaseRepository,
    second_repository: SqliteReviewCaseRepository,
    review_state: ReviewWorkflowState,
    resolution: ResolutionResult,
) -> None:
    register(repository, review_state, resolution, config_path=CONFIG_PATH)

    with pytest.raises(ReviewWorkflowContextConflictError):
        register(repository, review_state, resolution, config_path="configs/other.yaml")

    register(second_repository, review_state, resolution, config_path="configs/other.yaml")


def test_a_bundle_carries_only_its_own_queues_world(
    repository: SqliteReviewCaseRepository,
    second_repository: SqliteReviewCaseRepository,
    both_queues: str,
    review_state: ReviewWorkflowState,
) -> None:
    """Sprint 08 authorization is meaningless if the bundle spans two tenants.

    The boundary check projects a connected component across every AUTO_MATCH
    edge and every recorded human decision it is handed. A bundle covering two
    queues would let one organization's NO_MATCH forbid another's MATCH.
    """
    first = repository.load_workflow_bundle()
    second = second_repository.load_workflow_bundle()

    assert len(first.persisted_cases) == len(review_state.cases)
    assert len(second.persisted_cases) == len(review_state.cases)


# --------------------------------------------------------------------------
# Idempotence is queue-local
# --------------------------------------------------------------------------


def test_re_registering_is_a_no_op_in_its_own_queue_only(
    repository: SqliteReviewCaseRepository,
    second_repository: SqliteReviewCaseRepository,
    review_state: ReviewWorkflowState,
    resolution: ResolutionResult,
) -> None:
    register(repository, review_state, resolution)
    before = repository.workflow_context()

    register(repository, review_state, resolution)
    after = repository.workflow_context()

    # A true no-op: not even updated_at_utc moved.
    assert after == before
    # And it never created anything in the other tenant's queue.
    assert second_repository.workflow_context() is None
    assert second_repository.list_cases() == ()


def test_a_regenerated_pending_case_never_overwrites_a_resolved_one(
    repository: SqliteReviewCaseRepository,
    second_repository: SqliteReviewCaseRepository,
    both_queues: str,
    review_state: ReviewWorkflowState,
    resolution: ResolutionResult,
) -> None:
    """Deterministic generation always emits PENDING, in every queue.

    Re-running the pipeline is the normal operational rhythm, so the decision
    recorded in one queue has to survive it -- and the other queue's untouched
    PENDING case has to stay PENDING.
    """
    resolve_in(repository, review_state, both_queues)

    register(repository, review_state, resolution)
    register(second_repository, review_state, resolution)

    resolved = repository.get_case(both_queues)
    untouched = second_repository.get_case(both_queues)

    assert resolved.status is ReviewStatus.NO_MATCH
    assert resolved.version == 2
    assert untouched.status is ReviewStatus.PENDING
    assert untouched.version == 1


# --------------------------------------------------------------------------
# Resolution
# --------------------------------------------------------------------------


def test_resolving_in_one_queue_leaves_the_other_queues_case_untouched(
    repository: SqliteReviewCaseRepository,
    second_repository: SqliteReviewCaseRepository,
    both_queues: str,
    review_state: ReviewWorkflowState,
) -> None:
    """The compare-and-swap names the queue, so it cannot match the wrong row.

    Without the queue in its WHERE clause, the UPDATE would address whichever
    row SQLite reached first -- one organization's decision applied to
    another's data, with no error and no trace.
    """
    resolve_in(repository, review_state, both_queues)

    assert repository.get_case(both_queues).status is ReviewStatus.NO_MATCH

    untouched = second_repository.get_case(both_queues)
    assert untouched.status is ReviewStatus.PENDING
    assert untouched.version == 1
    assert untouched.updated_at_utc != RESOLVED_AT


def test_both_queues_can_resolve_the_same_case_id_differently(
    repository: SqliteReviewCaseRepository,
    second_repository: SqliteReviewCaseRepository,
    both_queues: str,
    review_state: ReviewWorkflowState,
    resolution: ResolutionResult,
    resolution_config: EntityResolutionConfig,
) -> None:
    """Two tenants, one id, two independent decisions -- including a MATCH.

    The MATCH runs the real Sprint 08 authorization against the queue's own
    bundle, which is what makes this a genuine second decision rather than a
    copy of the first.
    """
    resolve_in(repository, review_state, both_queues, decision=HumanReviewDecision.NO_MATCH)
    resolve_in(
        second_repository,
        review_state,
        both_queues,
        decision=HumanReviewDecision.MATCH,
        resolution=resolution,
        config=resolution_config,
    )

    assert repository.get_case(both_queues).status is ReviewStatus.NO_MATCH
    assert second_repository.get_case(both_queues).status is ReviewStatus.MATCH


def test_a_resolution_cannot_be_applied_to_a_case_in_another_queue(
    second_repository: SqliteReviewCaseRepository,
    repository: SqliteReviewCaseRepository,
    review_state: ReviewWorkflowState,
    resolution: ResolutionResult,
) -> None:
    """Registered only in the first queue; the second cannot resolve it at all."""
    register(repository, review_state, resolution)
    case_id = review_state.cases[0].review_case_id

    with pytest.raises(ReviewCaseNotFoundError):
        resolve_in(second_repository, review_state, case_id)


# --------------------------------------------------------------------------
# Events
# --------------------------------------------------------------------------


def test_history_never_splices_in_another_queues_events(
    repository: SqliteReviewCaseRepository,
    second_repository: SqliteReviewCaseRepository,
    both_queues: str,
    review_state: ReviewWorkflowState,
) -> None:
    resolve_in(repository, review_state, both_queues)

    own = repository.list_events(both_queues)
    other = second_repository.list_events(both_queues)

    assert [event.event_type.value for event in own] == ["NO_MATCH"]
    assert other == ()


def test_both_queues_keep_their_own_resolution_sequence(
    repository: SqliteReviewCaseRepository,
    second_repository: SqliteReviewCaseRepository,
    both_queues: str,
    review_state: ReviewWorkflowState,
) -> None:
    """The partial unique index is scoped by queue, so both may claim ordinal 1."""
    resolve_in(repository, review_state, both_queues)
    resolve_in(second_repository, review_state, both_queues)

    assert repository.list_events(both_queues)[0].resolution_sequence == 1
    assert second_repository.list_events(both_queues)[0].resolution_sequence == 1


def test_an_event_cannot_be_written_into_a_queue_that_does_not_own_the_case(
    database: ReviewDatabase,
    repository: SqliteReviewCaseRepository,
    second_repository: SqliteReviewCaseRepository,
    review_state: ReviewWorkflowState,
    resolution: ResolutionResult,
) -> None:
    """The composite foreign key, tested directly rather than trusted.

    Application code cannot produce this row, but the database is what
    guarantees it -- so the guarantee is asserted where it lives.
    """
    import sqlite3

    register(repository, review_state, resolution)
    case_id = review_state.cases[0].review_case_id

    with pytest.raises(sqlite3.IntegrityError):
        database.connect().execute(
            "INSERT INTO review_case_events (review_queue_id, review_case_id, event_type, "
            "resolution_sequence, reviewer_id, audit_entry_json, suggestion_id, "
            "occurred_at_utc, schema_version) "
            "VALUES (?, ?, 'CASE_CREATED', NULL, NULL, NULL, NULL, ?, ?)",
            (
                second_repository.review_queue_id,
                case_id,
                RESOLVED_AT,
                DATABASE_SCHEMA_VERSION,
            ),
        )


# --------------------------------------------------------------------------
# Semantic suggestions
# --------------------------------------------------------------------------


def test_one_content_addressed_suggestion_id_is_storable_in_both_queues(
    repository: SqliteReviewCaseRepository,
    second_repository: SqliteReviewCaseRepository,
    both_queues: str,
    review_state: ReviewWorkflowState,
    resolution: ResolutionResult,
) -> None:
    """A Sprint 09 id is a content address, so two queues can mint the same one.

    Scoping uniqueness to the queue keeps replay-is-a-no-op where it means
    something, without letting one tenant's stored observation answer for
    another's.
    """
    suggestion = make_suggestion(review_state.cases[0], records_by_id(resolution))

    assert repository.record_semantic_suggestion(suggestion) is True
    assert second_repository.record_semantic_suggestion(suggestion) is True

    assert [item.suggestion_id for item in repository.list_semantic_suggestions(both_queues)] == [
        suggestion.suggestion_id
    ]
    assert [
        item.suggestion_id for item in second_repository.list_semantic_suggestions(both_queues)
    ] == [suggestion.suggestion_id]


def test_a_replay_is_still_a_no_op_inside_one_queue(
    repository: SqliteReviewCaseRepository,
    both_queues: str,
    review_state: ReviewWorkflowState,
    resolution: ResolutionResult,
) -> None:
    suggestion = make_suggestion(review_state.cases[0], records_by_id(resolution))

    assert repository.record_semantic_suggestion(suggestion) is True
    assert repository.record_semantic_suggestion(suggestion) is False
    assert len(repository.list_semantic_suggestions(both_queues)) == 1


def test_suggestions_are_never_read_across_the_boundary(
    repository: SqliteReviewCaseRepository,
    second_repository: SqliteReviewCaseRepository,
    both_queues: str,
    review_state: ReviewWorkflowState,
    resolution: ResolutionResult,
) -> None:
    suggestion = make_suggestion(review_state.cases[0], records_by_id(resolution))
    repository.record_semantic_suggestion(suggestion)

    assert second_repository.list_semantic_suggestions(both_queues) == ()


def test_a_suggestion_cannot_attach_to_a_case_in_another_queue(
    repository: SqliteReviewCaseRepository,
    second_repository: SqliteReviewCaseRepository,
    review_state: ReviewWorkflowState,
    resolution: ResolutionResult,
) -> None:
    """Registered in one queue only, so the other has no case to describe."""
    register(repository, review_state, resolution)
    suggestion = make_suggestion(review_state.cases[0], records_by_id(resolution))

    with pytest.raises(ReviewCaseNotFoundError):
        second_repository.record_semantic_suggestion(suggestion)


def test_advisory_suggestions_still_never_touch_case_state(
    repository: SqliteReviewCaseRepository,
    both_queues: str,
    review_state: ReviewWorkflowState,
    resolution: ResolutionResult,
) -> None:
    """Sprint 09 semantics are unchanged by tenancy: advisory, and only advisory."""
    before = repository.get_case(both_queues)

    repository.record_semantic_suggestion(
        make_suggestion(review_state.cases[0], records_by_id(resolution))
    )

    after = repository.get_case(both_queues)
    assert after.status is before.status
    assert after.version == before.version


# --------------------------------------------------------------------------
# A queue must exist
# --------------------------------------------------------------------------


def test_registering_into_a_queue_that_does_not_exist_is_refused(
    database: ReviewDatabase,
    review_state: ReviewWorkflowState,
    resolution: ResolutionResult,
) -> None:
    """Review data owned by no queue would be review data owned by no tenant."""
    orphan = SqliteReviewCaseRepository(database, review_queue_id="RQ-never-created")

    with pytest.raises(ReviewQueueNotFoundError):
        register(orphan, review_state, resolution)

    assert orphan.list_cases() == ()


def test_a_refused_registration_writes_nothing_at_all(
    database: ReviewDatabase,
    review_state: ReviewWorkflowState,
    resolution: ResolutionResult,
) -> None:
    orphan = SqliteReviewCaseRepository(database, review_queue_id="RQ-never-created")

    with pytest.raises(ReviewQueueNotFoundError):
        register(orphan, review_state, resolution)

    counts = (
        database.connect()
        .execute(
            "SELECT (SELECT COUNT(*) FROM review_cases) AS cases, "
            "(SELECT COUNT(*) FROM review_workflow_context) AS contexts"
        )
        .fetchone()
    )
    assert counts["cases"] == 0
    assert counts["contexts"] == 0


def test_a_repository_cannot_be_built_without_a_queue_binding(
    database: ReviewDatabase,
) -> None:
    """There is no instance without one, which is the whole point of the design.

    A per-call scope parameter could be omitted or supplied from the wrong
    place and still type-check; a constructor binding cannot.
    """
    with pytest.raises(TypeError):
        SqliteReviewCaseRepository(database)  # type: ignore[call-arg]


def test_the_queue_binding_is_validated(database: ReviewDatabase) -> None:
    from identity.errors import IdentityValidationError

    with pytest.raises(IdentityValidationError):
        SqliteReviewCaseRepository(database, review_queue_id="not-a-queue-id")


def test_the_two_queues_belong_to_two_different_organizations(
    database: ReviewDatabase,
    repository: SqliteReviewCaseRepository,
    second_repository: SqliteReviewCaseRepository,
) -> None:
    """Guards this whole module: the isolation proven above is cross-tenant."""
    tenants = SqliteTenantRepository(database)
    first = tenants.get_review_queue(repository.review_queue_id)
    second = tenants.get_review_queue(second_repository.review_queue_id)

    assert first is not None
    assert second is not None
    assert first.organization_id != second.organization_id
