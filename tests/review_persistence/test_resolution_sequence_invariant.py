"""The queue-global resolution_sequence invariant, enforced by the database.

``review_application.history.reconstruct_history`` sorts every resolved case in
a queue by ``resolution_sequence`` and requires 1, 2, 3, ... with no gap and no
repeat. That is what ``ReviewWorkflow`` produces -- it takes the state's
``next_resolution_sequence``, uses it, and increments by one -- so a stored
queue that violates it is one the reader cannot load at all.

Before Sprint 14 Phase A the unique index was
``(review_queue_id, review_case_id, resolution_sequence)``, which enforced "one
ordinal per case". No writer can violate that: a case is resolvable only while
PENDING, and a second resolution event for one case is refused several layers
above. Meanwhile two *different* cases could both claim ordinal 1, because
``(Q, caseA, 1)`` and ``(Q, caseB, 1)`` are different keys -- and the
consequence is not a tidy duplicate row but a queue that raises out of the
contiguity check on every subsequent load, permanently, with no repair path.

So these tests do the thing the old index could not stop, with raw SQL. Raw SQL
is the point: the application cannot produce this state, which is exactly why
the guarantee has to be the database's rather than the service's.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from entity_resolution.config import EntityResolutionConfig
from entity_resolution.models import ResolutionResult
from human_review.cases import generate_review_cases
from human_review.models import HumanReviewDecision, ReviewWorkflowState
from human_review.reporting import resolution_snapshot
from review_application.service import ReviewQueueService
from review_persistence.schema import (
    DATABASE_SCHEMA_VERSION,
    REVIEW_CASE_EVENTS_TABLE,
    UNIQUE_RESOLUTION_SEQUENCE_INDEX,
)
from review_persistence.sqlite.database import ReviewDatabase
from review_persistence.sqlite.review_repository import SqliteReviewCaseRepository
from tests.human_review.conftest import make_chain_review_resolution

NOW = "2026-09-12T08:00:00Z"


def _insert_resolution_event(
    database: ReviewDatabase,
    *,
    review_queue_id: str,
    review_case_id: str,
    resolution_sequence: int,
    event_type: str = "NO_MATCH",
) -> None:
    """Append a resolution event directly, bypassing every application guard.

    The ``audit_entry_json`` payload only has to be non-null to satisfy the
    table's CHECK constraint; nothing here reads it. What is under test is the
    unique index, so the row is otherwise the minimum a resolution event can be.
    """
    database.connect().execute(
        f"INSERT INTO {REVIEW_CASE_EVENTS_TABLE} ("
        "review_queue_id, review_case_id, event_type, resolution_sequence, "
        "reviewer_id, audit_entry_json, suggestion_id, occurred_at_utc, schema_version"
        ") VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?)",
        (
            review_queue_id,
            review_case_id,
            event_type,
            resolution_sequence,
            "reviewer-raw",
            json.dumps({"review_case_id": review_case_id}),
            NOW,
            DATABASE_SCHEMA_VERSION,
        ),
    )


@pytest.fixture
def service(repository: SqliteReviewCaseRepository) -> ReviewQueueService:
    """The authoritative resolution path, over the fixture queue."""
    return ReviewQueueService(repository)


@pytest.fixture
def chain_resolution() -> ResolutionResult:
    """Three records in a chain, which generates more than one REVIEW case."""
    return make_chain_review_resolution(("rec-a", "rec-b", "rec-c"))


@pytest.fixture
def chain_state(
    chain_resolution: ResolutionResult,
    resolution_config: EntityResolutionConfig,
) -> ReviewWorkflowState:
    return generate_review_cases(chain_resolution, config=resolution_config)


@pytest.fixture
def registered_chain(
    repository: SqliteReviewCaseRepository,
    chain_state: ReviewWorkflowState,
    chain_resolution: ResolutionResult,
) -> ReviewWorkflowState:
    repository.register_workflow(
        chain_state,
        entity_records=chain_resolution.records,
        resolution_snapshot=resolution_snapshot(chain_resolution),
        entity_resolution_config_path="configs/entity_resolution.yaml",
    )
    assert len(chain_state.cases) >= 2, "Fixture must produce at least two review cases."
    return chain_state


# --------------------------------------------------------------------------
# The invariant the old index did not enforce
# --------------------------------------------------------------------------


def test_two_different_cases_cannot_share_one_resolution_sequence(
    repository: SqliteReviewCaseRepository,
    database: ReviewDatabase,
    registered_chain: ReviewWorkflowState,
    review_queue,
) -> None:
    """The exact state that used to be storable, and used to brick the queue.

    Two distinct review cases, one queue, one ordinal. Under the old index these
    were different keys and both rows committed; afterwards every
    ``load_workflow_bundle`` for the queue raised ``ReviewEventIntegrityError``
    and the queue could never be read or resolved again.
    """
    first, second = registered_chain.cases[0], registered_chain.cases[1]
    assert first.review_case_id != second.review_case_id

    _insert_resolution_event(
        database,
        review_queue_id=review_queue.review_queue_id,
        review_case_id=first.review_case_id,
        resolution_sequence=1,
    )

    with pytest.raises(sqlite3.IntegrityError):
        _insert_resolution_event(
            database,
            review_queue_id=review_queue.review_queue_id,
            review_case_id=second.review_case_id,
            resolution_sequence=1,
        )


def test_the_index_key_is_queue_and_sequence_not_queue_case_and_sequence(
    database: ReviewDatabase,
) -> None:
    """Pins the key itself, so the old spelling cannot quietly come back.

    The previous key type-checked, tested green, and enforced nothing. A test
    that only exercised behaviour through the application would not have caught
    it, because the application never produces the violating state.
    """
    columns = [
        row["name"]
        for row in database.connect().execute(
            "PRAGMA index_info(ux_review_case_events_resolution_sequence)"
        )
    ]
    assert columns == ["review_queue_id", "resolution_sequence"]
    assert "review_case_id" not in columns
    assert "review_case_id" not in UNIQUE_RESOLUTION_SEQUENCE_INDEX.split("ON")[1]


def test_the_same_sequence_is_allowed_in_a_different_queue(
    repository: SqliteReviewCaseRepository,
    second_repository: SqliteReviewCaseRepository,
    database: ReviewDatabase,
    registered_chain: ReviewWorkflowState,
    chain_state: ReviewWorkflowState,
    chain_resolution: ResolutionResult,
    review_queue,
    second_review_queue,
) -> None:
    """The constraint is queue-global, not installation-global.

    Two tenants both start their history at 1. Scoping the uniqueness any wider
    would make one tenant's activity refuse another's write.
    """
    second_repository.register_workflow(
        chain_state,
        entity_records=chain_resolution.records,
        resolution_snapshot=resolution_snapshot(chain_resolution),
        entity_resolution_config_path="configs/entity_resolution.yaml",
    )
    case_id = registered_chain.cases[0].review_case_id

    _insert_resolution_event(
        database,
        review_queue_id=review_queue.review_queue_id,
        review_case_id=case_id,
        resolution_sequence=1,
    )
    _insert_resolution_event(
        database,
        review_queue_id=second_review_queue.review_queue_id,
        review_case_id=case_id,
        resolution_sequence=1,
    )

    counts = database.connect().execute(
        f"SELECT review_queue_id, COUNT(*) AS n FROM {REVIEW_CASE_EVENTS_TABLE} "
        "WHERE resolution_sequence = 1 GROUP BY review_queue_id"
    )
    assert {row["review_queue_id"]: row["n"] for row in counts} == {
        review_queue.review_queue_id: 1,
        second_review_queue.review_queue_id: 1,
    }


# --------------------------------------------------------------------------
# What must keep working
# --------------------------------------------------------------------------


def test_ordinary_history_still_numbers_one_two_three(
    repository: SqliteReviewCaseRepository,
    service: ReviewQueueService,
    registered_chain: ReviewWorkflowState,
) -> None:
    """The tightened index must not obstruct the sequence the domain produces."""
    for case in registered_chain.cases:
        service.resolve_case(
            case.review_case_id,
            decision=HumanReviewDecision.NO_MATCH,
            reviewer_id="reviewer-1",
            expected_version=1,
        )

    bundle = repository.load_workflow_bundle()
    sequences = [entry.resolution_sequence for entry in bundle.audit_entries]

    assert sequences == list(range(1, len(registered_chain.cases) + 1))
    assert bundle.next_resolution_sequence == len(registered_chain.cases) + 1


def test_semantic_suggestions_consume_no_resolution_sequence(
    repository: SqliteReviewCaseRepository,
    database: ReviewDatabase,
    registered_chain: ReviewWorkflowState,
    review_queue,
) -> None:
    """Many advisory rows per queue, all with NULL sequences, all permitted.

    The index is partial for this reason. A suggestion is advisory and must not
    occupy an ordinal in the human decision history -- and NULLs would collide
    under a non-partial unique index, which would make recording a second
    suggestion in a queue impossible.
    """
    connection = database.connect()
    for case in registered_chain.cases:
        connection.execute(
            f"INSERT INTO {REVIEW_CASE_EVENTS_TABLE} ("
            "review_queue_id, review_case_id, event_type, resolution_sequence, "
            "reviewer_id, audit_entry_json, suggestion_id, occurred_at_utc, schema_version"
            ") VALUES (?, ?, 'SEMANTIC_SUGGESTION_RECORDED', NULL, NULL, NULL, ?, ?, ?)",
            (
                review_queue.review_queue_id,
                case.review_case_id,
                f"SS-{case.review_case_id}",
                NOW,
                DATABASE_SCHEMA_VERSION,
            ),
        )

    nulls = connection.execute(
        f"SELECT COUNT(*) AS n FROM {REVIEW_CASE_EVENTS_TABLE} "
        "WHERE resolution_sequence IS NULL AND review_queue_id = ?",
        (review_queue.review_queue_id,),
    ).fetchone()
    assert nulls["n"] == len(registered_chain.cases)

    # And the queue still loads: NULL sequences take part in no ordinal at all.
    assert repository.load_workflow_bundle().next_resolution_sequence == 1
