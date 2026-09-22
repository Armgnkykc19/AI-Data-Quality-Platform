"""``verify-queue``: what it detects, and that it changes nothing detecting it.

Two properties are under test and the second matters as much as the first.

It has to *find* things -- a duplicated resolution ordinal, a missing index, an
unsupported schema, a case whose row and history disagree. And it has to leave
the database byte-for-byte as it found it, including for a queue that is
broken, because the one thing worse than an unusable queue is a tool that
rewrote human decisions while looking at them.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from entity_resolution.models import ResolutionResult
from human_review.cases import generate_review_cases
from human_review.models import HumanReviewDecision, ReviewWorkflowState
from human_review.reporting import resolution_snapshot
from review_application.queues import ReviewQueue
from review_application.service import ReviewQueueService
from review_persistence.integrity import (
    EXPECTED_REVIEW_INDEXES,
    verify_review_queue,
)
from review_persistence.schema import (
    DATABASE_SCHEMA_VERSION,
    REVIEW_CASE_EVENTS_TABLE,
    SCHEMA_META_TABLE,
)
from review_persistence.sqlite.database import ReviewDatabase
from review_persistence.sqlite.review_repository import SqliteReviewCaseRepository
from tests.human_review.conftest import make_chain_review_resolution

NOW = "2026-09-12T08:00:00Z"


@pytest.fixture
def chain_resolution() -> ResolutionResult:
    return make_chain_review_resolution(("rec-a", "rec-b", "rec-c"))


@pytest.fixture
def healthy_queue(
    repository: SqliteReviewCaseRepository,
    chain_resolution: ResolutionResult,
    resolution_config,
) -> ReviewWorkflowState:
    state = generate_review_cases(chain_resolution, config=resolution_config)
    repository.register_workflow(
        state,
        entity_records=chain_resolution.records,
        resolution_snapshot=resolution_snapshot(chain_resolution),
        entity_resolution_config_path="configs/entity_resolution.yaml",
    )
    return state


def _snapshot(database: ReviewDatabase) -> str:
    """Every row in every review table, as a comparable string.

    Used to prove the verifier wrote nothing. Comparing the file's bytes would
    be stricter but also flaky -- SQLite rewrites free pages and the WAL for
    reasons that have nothing to do with this command.
    """
    connection = database.connect()
    tables = [
        str(row["name"])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        )
    ]
    dump: dict[str, list[list[object]]] = {}
    for table in tables:
        rows = connection.execute(f"SELECT * FROM {table}").fetchall()  # noqa: S608
        dump[table] = sorted([list(map(str, tuple(row))) for row in rows])
    return json.dumps(dump, sort_keys=True)


# --------------------------------------------------------------------------
# Healthy
# --------------------------------------------------------------------------


def test_a_healthy_queue_reports_no_findings(
    database: ReviewDatabase,
    review_queue: ReviewQueue,
    healthy_queue: ReviewWorkflowState,
) -> None:
    report = verify_review_queue(database, review_queue_id=review_queue.review_queue_id)

    assert report.ok
    assert report.findings == ()
    assert report.organization_id == review_queue.organization_id
    assert len(report.checks_run) >= 7


def test_a_resolved_queue_is_still_healthy(
    database: ReviewDatabase,
    repository: SqliteReviewCaseRepository,
    review_queue: ReviewQueue,
    healthy_queue: ReviewWorkflowState,
) -> None:
    """Ordinary decisions must not look like corruption."""
    service = ReviewQueueService(repository)
    for case in healthy_queue.cases:
        service.resolve_case(
            case.review_case_id,
            decision=HumanReviewDecision.NO_MATCH,
            reviewer_id="reviewer-1",
            expected_version=1,
        )

    assert verify_review_queue(database, review_queue_id=review_queue.review_queue_id).ok


# --------------------------------------------------------------------------
# Detection
# --------------------------------------------------------------------------


def test_a_duplicated_resolution_ordinal_is_reported(
    database: ReviewDatabase,
    review_queue: ReviewQueue,
    healthy_queue: ReviewWorkflowState,
) -> None:
    """The corruption A1 now prevents, in a database that already has it.

    Inserted with the unique index dropped, which is exactly the state a
    database created before Phase A would be in.
    """
    connection = database.connect()
    connection.execute("DROP INDEX ux_review_case_events_resolution_sequence")
    for case in healthy_queue.cases[:2]:
        connection.execute(
            f"INSERT INTO {REVIEW_CASE_EVENTS_TABLE} ("
            "review_queue_id, review_case_id, event_type, resolution_sequence, "
            "reviewer_id, audit_entry_json, suggestion_id, occurred_at_utc, schema_version"
            ") VALUES (?, ?, 'NO_MATCH', 1, 'r', '{}', NULL, ?, ?)",
            (
                review_queue.review_queue_id,
                case.review_case_id,
                NOW,
                DATABASE_SCHEMA_VERSION,
            ),
        )

    report = verify_review_queue(database, review_queue_id=review_queue.review_queue_id)

    assert not report.ok
    assert "RESOLUTION_SEQUENCE_DUPLICATED" in report.codes()
    assert "INDEX_MISSING" in report.codes()


def test_a_missing_index_is_reported_on_its_own(
    database: ReviewDatabase,
    review_queue: ReviewQueue,
    healthy_queue: ReviewWorkflowState,
) -> None:
    """A dropped index is a finding, not a silent pass."""
    database.connect().execute("DROP INDEX ux_review_case_events_resolution_sequence")

    report = verify_review_queue(database, review_queue_id=review_queue.review_queue_id)

    assert not report.ok
    assert report.codes() == ("INDEX_MISSING",)
    assert "ux_review_case_events_resolution_sequence" in report.findings[0].detail


def test_the_sprint_13_weak_index_is_reported_as_incompatible(
    database: ReviewDatabase,
    review_queue: ReviewQueue,
    healthy_queue: ReviewWorkflowState,
) -> None:
    connection = database.connect()
    connection.execute("DROP INDEX ux_review_case_events_resolution_sequence")
    connection.execute(
        "CREATE UNIQUE INDEX ux_review_case_events_resolution_sequence "
        "ON review_case_events (review_queue_id, review_case_id, resolution_sequence) "
        "WHERE resolution_sequence IS NOT NULL"
    )

    report = verify_review_queue(database, review_queue_id=review_queue.review_queue_id)

    assert not report.ok
    assert "INDEX_INCOMPATIBLE" in report.codes()
    assert "INDEX_MISSING" not in report.codes()


def test_every_expected_index_is_actually_present_in_a_fresh_database(
    database: ReviewDatabase,
) -> None:
    """Guards the list itself against naming an index the schema never creates."""
    present = {
        str(row["name"])
        for row in database.connect().execute("SELECT name FROM sqlite_master WHERE type = 'index'")
    }

    assert set(EXPECTED_REVIEW_INDEXES) <= present


def test_an_unknown_queue_is_reported_rather_than_raising(
    database: ReviewDatabase,
    review_queue: ReviewQueue,
) -> None:
    report = verify_review_queue(database, review_queue_id="RQ-not-stored")

    assert not report.ok
    assert report.codes() == ("QUEUE_NOT_FOUND",)


def test_a_queue_with_no_workflow_context_is_reported(
    database: ReviewDatabase,
    repository: SqliteReviewCaseRepository,
    review_queue: ReviewQueue,
    review_case,
) -> None:
    """``register_case`` alone leaves a queue that cannot authorize anything."""
    repository.register_case(review_case)

    report = verify_review_queue(database, review_queue_id=review_queue.review_queue_id)

    assert not report.ok
    assert "BUNDLE_NOT_RECONSTRUCTABLE" in report.codes()


def test_a_pending_case_holding_a_resolution_event_is_reported(
    database: ReviewDatabase,
    review_queue: ReviewQueue,
    healthy_queue: ReviewWorkflowState,
) -> None:
    """The case row and its history disagreeing about whether a decision exists."""
    database.connect().execute(
        f"INSERT INTO {REVIEW_CASE_EVENTS_TABLE} ("
        "review_queue_id, review_case_id, event_type, resolution_sequence, "
        "reviewer_id, audit_entry_json, suggestion_id, occurred_at_utc, schema_version"
        ") VALUES (?, ?, 'MATCH', 1, 'r', '{}', NULL, ?, ?)",
        (
            review_queue.review_queue_id,
            healthy_queue.cases[0].review_case_id,
            NOW,
            DATABASE_SCHEMA_VERSION,
        ),
    )

    report = verify_review_queue(database, review_queue_id=review_queue.review_queue_id)

    assert not report.ok
    assert "PENDING_CASE_HAS_RESOLUTION_EVENT" in report.codes()


def test_an_unsupported_schema_stops_the_run_immediately(
    database: ReviewDatabase,
    review_queue: ReviewQueue,
    healthy_queue: ReviewWorkflowState,
) -> None:
    """Nothing below the schema check can be trusted, so nothing below it runs."""
    database.connect().execute(
        f"UPDATE {SCHEMA_META_TABLE} SET schema_version = '0.0.1' WHERE id = 1"
    )

    report = verify_review_queue(database, review_queue_id=review_queue.review_queue_id)

    assert not report.ok
    assert report.codes() == ("SCHEMA_UNSUPPORTED",)
    assert report.checks_run == ("schema_version_supported",)


# --------------------------------------------------------------------------
# Read-only
# --------------------------------------------------------------------------


def test_verification_changes_nothing_in_a_healthy_queue(
    database: ReviewDatabase,
    review_queue: ReviewQueue,
    healthy_queue: ReviewWorkflowState,
) -> None:
    before = _snapshot(database)

    verify_review_queue(database, review_queue_id=review_queue.review_queue_id)

    assert _snapshot(database) == before


def test_verification_changes_nothing_in_a_corrupt_queue(
    database: ReviewDatabase,
    review_queue: ReviewQueue,
    healthy_queue: ReviewWorkflowState,
) -> None:
    """The important half. A broken queue must not be "helpfully" rewritten.

    Renumbering a duplicated ordinal would change which decision came first,
    which is an audit statement about human decisions and not something a tool
    may infer.
    """
    connection = database.connect()
    connection.execute("DROP INDEX ux_review_case_events_resolution_sequence")
    for case in healthy_queue.cases[:2]:
        connection.execute(
            f"INSERT INTO {REVIEW_CASE_EVENTS_TABLE} ("
            "review_queue_id, review_case_id, event_type, resolution_sequence, "
            "reviewer_id, audit_entry_json, suggestion_id, occurred_at_utc, schema_version"
            ") VALUES (?, ?, 'NO_MATCH', 1, 'r', '{}', NULL, ?, ?)",
            (review_queue.review_queue_id, case.review_case_id, NOW, DATABASE_SCHEMA_VERSION),
        )
    before = _snapshot(database)

    report = verify_review_queue(database, review_queue_id=review_queue.review_queue_id)

    assert not report.ok
    assert _snapshot(database) == before


def test_verification_leaves_no_transaction_open(
    database: ReviewDatabase,
    review_queue: ReviewQueue,
    healthy_queue: ReviewWorkflowState,
) -> None:
    """A verifier that held a lock would block the queue it was inspecting."""
    verify_review_queue(database, review_queue_id=review_queue.review_queue_id)

    assert database.connect().in_transaction is False


def test_findings_carry_no_record_values(
    database: ReviewDatabase,
    review_queue: ReviewQueue,
    healthy_queue: ReviewWorkflowState,
    chain_resolution: ResolutionResult,
) -> None:
    """An integrity report is something an operator pastes into an issue."""
    database.connect().execute("DROP INDEX ux_review_case_events_resolution_sequence")

    report = verify_review_queue(database, review_queue_id=review_queue.review_queue_id)
    text = " ".join(finding.detail for finding in report.findings)

    for record in chain_resolution.records:
        for value in (record.field_values or {}).values():
            if isinstance(value, str) and len(value) > 3:
                assert value not in text, f"a record value reached an integrity finding: {value!r}"


def test_the_verifier_issues_no_write_statements(
    database: ReviewDatabase,
    review_queue: ReviewQueue,
    healthy_queue: ReviewWorkflowState,
) -> None:
    """Traced rather than inferred, so "read-only" is a fact about the SQL."""
    statements: list[str] = []
    database.connect().set_trace_callback(statements.append)
    try:
        verify_review_queue(database, review_queue_id=review_queue.review_queue_id)
    finally:
        database.connect().set_trace_callback(None)

    forbidden = ("INSERT", "UPDATE", "DELETE", "DROP", "CREATE", "ALTER", "REPLACE")
    offenders = [
        statement
        for statement in statements
        if any(statement.strip().upper().startswith(word) for word in forbidden)
    ]
    assert offenders == []


def test_a_sqlite_error_while_reading_still_propagates(
    database: ReviewDatabase,
    review_queue: ReviewQueue,
) -> None:
    """Failing to read is not a finding; it is a failure.

    A closed database must raise rather than be reported as a clean queue.
    """
    database.close()
    database.connect().close()

    with pytest.raises(sqlite3.ProgrammingError):
        verify_review_queue(database, review_queue_id=review_queue.review_queue_id)
