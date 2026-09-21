"""Every statement the review repository issues, recorded and checked.

Defence in depth. The behavioural isolation tests prove that two tenants cannot
see each other *today*; this proves the mechanism, by recording the SQL the
repository actually executes while every Protocol method is exercised, and
asserting each statement touching a review table is scoped to that repository's
own queue.

The distinction matters because of how this would fail. A future edit that adds
an unscoped read -- a new list method, a count, a lookup by record id -- would
pass every behavioural test written against a single queue, and would only be
caught by a test that happened to set up two tenants for that exact method.
Recording the statements catches it regardless of which method is added.

Checked on execution rather than on source text on purpose. A source scan
cannot tell ``review_queue_id`` in a SELECT column list from
``review_queue_id`` in a WHERE clause, so it would wave through a statement
that selects the column while filtering on nothing. ``set_trace_callback``
hands over the SQL SQLite actually ran, with parameters bound -- so the queue
id can be checked as a *value*, not merely as a column name.
"""

from __future__ import annotations

import re
from collections.abc import Iterator

import pytest

from entity_resolution.models import ResolutionResult
from human_review.models import HumanReviewDecision, ReviewWorkflowState
from human_review.reporting import resolution_snapshot
from human_review.workflow import ReviewWorkflow
from review_application.models import ReviewEvent
from review_persistence.schema import DATABASE_SCHEMA_VERSION
from review_persistence.sqlite.database import ReviewDatabase
from review_persistence.sqlite.review_repository import SqliteReviewCaseRepository
from tests.review_persistence.semantic_fixtures import make_suggestion

# The tables a tenant owns. ``review_queues``, ``organizations``, ``users`` and
# ``organization_memberships`` are the tenant graph itself, addressed by their
# own primary keys; they are not review data, so they are not scanned here.
REVIEW_TABLES = (
    "review_cases",
    "review_case_events",
    "semantic_suggestions",
    "review_workflow_context",
)

RESOLVED_AT = "2026-09-12T09:00:00Z"


@pytest.fixture
def recorded(database: ReviewDatabase) -> Iterator[list[str]]:
    """Every statement SQLite runs on this connection, with parameters bound."""
    statements: list[str] = []
    connection = database.connect()
    connection.set_trace_callback(statements.append)
    try:
        yield statements
    finally:
        connection.set_trace_callback(None)


def exercise_every_method(
    repository: SqliteReviewCaseRepository,
    state: ReviewWorkflowState,
    resolution: ResolutionResult,
) -> None:
    """Call every method on the repository Protocol at least once."""
    repository.register_workflow(
        state,
        entity_records=resolution.records,
        resolution_snapshot=resolution_snapshot(resolution),
        entity_resolution_config_path="configs/entity_resolution.yaml",
    )
    case_id = state.cases[0].review_case_id

    repository.get_case(case_id)
    repository.list_cases()
    repository.list_cases(status=state.cases[0].status)
    repository.load_workflow_bundle()
    repository.list_events(case_id)

    records = {record.record_id: record for record in resolution.records}
    suggestion = make_suggestion(state.cases[0], records)
    repository.record_semantic_suggestion(suggestion)
    # Again, to exercise the idempotent-replay read path.
    repository.record_semantic_suggestion(suggestion)
    repository.list_semantic_suggestions(case_id)

    updated = ReviewWorkflow(state).resolve_case(
        case_id,
        decision=HumanReviewDecision.NO_MATCH,
        reviewer_id="reviewer-1",
    )
    resolved = updated.case_by_id(case_id)
    assert resolved is not None
    repository.apply_resolution(
        resolved,
        expected_version=1,
        event=ReviewEvent.from_audit_entry(
            updated.audit_trail[-1],
            occurred_at_utc=RESOLVED_AT,
            schema_version=DATABASE_SCHEMA_VERSION,
        ),
        now_utc=RESOLVED_AT,
    )
    repository.workflow_context()


def statements_touching_review_tables(statements: list[str]) -> list[str]:
    return [
        statement
        for statement in statements
        if any(re.search(rf"\b{table}\b", statement) for table in REVIEW_TABLES)
    ]


def is_scoped_to(statement: str, queue_id: str) -> bool:
    """True when this statement cannot reach outside ``queue_id``.

    A write supplies the queue as a column and as a value, so the row lands
    inside it. A read or an update must filter on that exact queue id in its
    WHERE clause. Anything else could address another tenant's row.
    """
    if statement.lstrip().upper().startswith("INSERT"):
        columns = statement[statement.index("(") : statement.index(")")]
        return "review_queue_id" in columns and f"'{queue_id}'" in statement
    match = re.search(r"\bWHERE\b([\s\S]*)", statement, re.IGNORECASE)
    if match is None:
        return False
    return bool(re.search(rf"review_queue_id\s*=\s*'{re.escape(queue_id)}'", match.group(1)))


def test_every_executed_review_statement_is_scoped_to_its_own_queue(
    repository: SqliteReviewCaseRepository,
    review_state: ReviewWorkflowState,
    resolution: ResolutionResult,
    recorded: list[str],
) -> None:
    exercise_every_method(repository, review_state, resolution)

    offenders = [
        statement
        for statement in statements_touching_review_tables(recorded)
        if not is_scoped_to(statement, repository.review_queue_id)
    ]

    assert offenders == []


def test_the_recording_actually_captured_the_repository(
    repository: SqliteReviewCaseRepository,
    review_state: ReviewWorkflowState,
    resolution: ResolutionResult,
    recorded: list[str],
) -> None:
    """Guards the test above from passing on an empty or partial recording.

    Every review table must appear, and a read, an insert and an update among
    them -- otherwise the scan above is checking a subset of the real surface.
    """
    exercise_every_method(repository, review_state, resolution)
    touching = statements_touching_review_tables(recorded)

    assert len(touching) >= 10
    for table in REVIEW_TABLES:
        assert any(re.search(rf"\b{table}\b", statement) for statement in touching), table
    for verb in ("INSERT", "UPDATE", "SELECT"):
        assert any(statement.lstrip().upper().startswith(verb) for statement in touching), verb


def test_the_scope_check_rejects_what_it_exists_to_catch() -> None:
    """Guards the predicate itself.

    The first case is precisely the mistake a source-text scan waves through:
    a SELECT naming review_queue_id in its column list and filtering on
    nothing. The last is subtler and matters more -- a statement scoped to
    *another* queue is scoped, and still wrong.
    """
    queue = "RQ-mine"
    other = "RQ-theirs"

    assert not is_scoped_to("SELECT review_queue_id, status FROM review_cases", queue)
    assert not is_scoped_to(
        "UPDATE review_cases SET status = 'MATCH' WHERE review_case_id = 'RC-1'", queue
    )
    assert not is_scoped_to(
        "INSERT INTO review_cases (review_case_id, status) VALUES ('RC-1', 'PENDING')", queue
    )
    assert not is_scoped_to(
        f"SELECT status FROM review_cases WHERE review_queue_id = '{other}'", queue
    )

    assert is_scoped_to(f"SELECT status FROM review_cases WHERE review_queue_id = '{queue}'", queue)
    assert is_scoped_to(
        f"UPDATE review_cases SET status = 'MATCH' WHERE review_queue_id = '{queue}' "
        "AND review_case_id = 'RC-1'",
        queue,
    )
    assert is_scoped_to(
        f"INSERT INTO review_cases (review_queue_id, review_case_id) VALUES ('{queue}', 'RC-1')",
        queue,
    )
