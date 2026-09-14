"""Contract tests for the Sprint 10 SQLite schema.

The DDL is checked two ways. Token sets are parsed out of the CHECK clauses,
never matched as substrings: ``NO_MATCH`` is a substring of ``SUGGEST_NO_MATCH``
and of ``SUGGEST_NO_MATCH``'s neighbours, so a naive ``in`` assertion would pass
on a schema that actually accepts the authoritative Human Review token.

The same DDL is then executed against an in-memory database. No file and no
connection factory is created; this only proves the statements are valid SQLite
and that the constraints reject what they claim to reject. A DDL contract that
was never executed could ship syntactically broken and still pass every
text-level assertion.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from human_review.models import ReviewStatus
from review_application.errors import ReviewSchemaVersionError
from review_application.models import ReviewEventType
from review_persistence.schema import (
    ALL_TABLES,
    ALLOWED_REVIEW_EVENT_TYPES,
    ALLOWED_REVIEW_STATUS_VALUES,
    ALLOWED_SEMANTIC_SUGGESTION_VALUES,
    CREATE_REVIEW_CASE_EVENTS,
    CREATE_REVIEW_CASES,
    CREATE_SEMANTIC_SUGGESTIONS,
    DATABASE_SCHEMA_VERSION,
    FORBIDDEN_SEMANTIC_SUGGESTION_VALUES,
    REQUIRED_PRAGMAS,
    SCHEMA_STATEMENTS,
    SUPPORTED_DATABASE_SCHEMA_VERSIONS,
    assert_supported_schema_version,
)

NOW = "2026-09-12T00:00:00Z"
CASE_ID = "RC-000000000000abcd"

EXPECTED_REVIEW_STATUS_VALUES = {"PENDING", "MATCH", "NO_MATCH", "DEFERRED"}
EXPECTED_SEMANTIC_SUGGESTION_VALUES = {
    "SUGGEST_MATCH",
    "SUGGEST_NO_MATCH",
    "INSUFFICIENT_EVIDENCE",
    "PROVIDER_FAILURE",
}
EXPECTED_EVENT_TYPES = {
    "CASE_CREATED",
    "SEMANTIC_SUGGESTION_RECORDED",
    "MATCH",
    "NO_MATCH",
    "DEFERRED",
}

# Ground-truth and tuning material that must never reach the review database.
# Each pattern is anchored so that a legitimate column cannot accidentally
# satisfy it.
FORBIDDEN_SCHEMA_TOKENS = (
    r"\bperson_id\b",
    r"\bexpected_person_id\b",
    r"\boracle\b",
    r"\bground_truth\b",
    r"\bfinal_holdout\b",
    r"\bdataset_split\b",
    r"\bauto_match_threshold_tuning\b",
)

CASE_COLUMNS = (
    "review_case_id",
    "record_a_id",
    "record_b_id",
    "status",
    "machine_decision",
    "machine_score",
    "version",
    "case_payload_json",
    "schema_version",
    "created_at_utc",
    "updated_at_utc",
)


def _check_in_list(ddl: str, column: str) -> set[str]:
    """Return the quoted tokens of ``CHECK (<column> IN (...))`` as a set."""
    match = re.search(rf"CHECK \({column} IN \(([^)]*)\)\)", ddl)
    assert match is not None, f"No IN-list CHECK found for column {column!r}."
    tokens = re.findall(r"'([^']*)'", match.group(1))
    assert tokens, f"IN-list for {column!r} is empty."
    return set(tokens)


@pytest.fixture
def connection() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(":memory:")
    # Without this pragma every REFERENCES clause below is inert, so the
    # foreign-key assertions would silently test nothing.
    conn.execute("PRAGMA foreign_keys = ON")
    for statement in SCHEMA_STATEMENTS:
        conn.execute(statement)
    try:
        yield conn
    finally:
        conn.close()


def _insert_case(
    conn: sqlite3.Connection,
    *,
    review_case_id: str = CASE_ID,
    record_a_id: str = "a-1",
    record_b_id: str = "a-2",
    status: str = "PENDING",
    version: int = 1,
) -> None:
    conn.execute(
        f"INSERT INTO review_cases ({', '.join(CASE_COLUMNS)}) "
        f"VALUES ({', '.join('?' * len(CASE_COLUMNS))})",
        (
            review_case_id,
            record_a_id,
            record_b_id,
            status,
            "REVIEW",
            0.75,
            version,
            "{}",
            DATABASE_SCHEMA_VERSION,
            NOW,
            NOW,
        ),
    )


def _insert_event(
    conn: sqlite3.Connection,
    *,
    event_type: str,
    resolution_sequence: int | None = None,
    reviewer_id: str | None = None,
    audit_entry_json: str | None = None,
    suggestion_id: str | None = None,
    review_case_id: str = CASE_ID,
) -> None:
    conn.execute(
        "INSERT INTO review_case_events ("
        "review_case_id, event_type, resolution_sequence, reviewer_id, "
        "audit_entry_json, suggestion_id, occurred_at_utc, schema_version"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            review_case_id,
            event_type,
            resolution_sequence,
            reviewer_id,
            audit_entry_json,
            suggestion_id,
            NOW,
            DATABASE_SCHEMA_VERSION,
        ),
    )


def _insert_suggestion(
    conn: sqlite3.Connection,
    *,
    suggestion: str,
    suggestion_id: str = "LS-0123456789abcdef",
    review_case_id: str = CASE_ID,
) -> None:
    conn.execute(
        "INSERT INTO semantic_suggestions ("
        "suggestion_id, review_case_id, record_a_id, record_b_id, suggestion, "
        "failure_code, live, provider, requested_model, suggestion_payload_json, "
        "created_at_utc, schema_version"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            suggestion_id,
            review_case_id,
            "a-1",
            "a-2",
            suggestion,
            None,
            0,
            "simulated",
            "test-model",
            "{}",
            NOW,
            DATABASE_SCHEMA_VERSION,
        ),
    )


# --------------------------------------------------------------------------
# Schema version
# --------------------------------------------------------------------------


def test_declared_schema_version_is_supported() -> None:
    assert DATABASE_SCHEMA_VERSION == "1.0.0"
    assert DATABASE_SCHEMA_VERSION in SUPPORTED_DATABASE_SCHEMA_VERSIONS
    assert_supported_schema_version(DATABASE_SCHEMA_VERSION)


@pytest.mark.parametrize("version", ["0.9.0", "2.0.0", "", "1.0"])
def test_unsupported_schema_version_fails_closed(version: str) -> None:
    with pytest.raises(ReviewSchemaVersionError):
        assert_supported_schema_version(version)


def test_schema_declares_the_expected_tables(connection: sqlite3.Connection) -> None:
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    assert {row[0] for row in rows} == set(ALL_TABLES)


def test_schema_meta_is_a_single_row_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        "INSERT INTO schema_meta (id, schema_version, created_at_utc) VALUES (1, ?, ?)",
        (DATABASE_SCHEMA_VERSION, NOW),
    )
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO schema_meta (id, schema_version, created_at_utc) VALUES (2, ?, ?)",
            (DATABASE_SCHEMA_VERSION, NOW),
        )


def test_required_pragmas_enable_foreign_keys() -> None:
    normalized = {pragma.replace(" ", "").upper() for pragma in REQUIRED_PRAGMAS}
    assert "PRAGMAFOREIGN_KEYS=ON" in normalized


# --------------------------------------------------------------------------
# review_cases
# --------------------------------------------------------------------------


def test_review_cases_status_tokens_are_exactly_the_domain_enum() -> None:
    tokens = _check_in_list(CREATE_REVIEW_CASES, "status")
    assert tokens == EXPECTED_REVIEW_STATUS_VALUES
    assert tokens == ALLOWED_REVIEW_STATUS_VALUES
    assert tokens == {status.value for status in ReviewStatus}


def test_review_cases_rejects_an_unknown_status(connection: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        _insert_case(connection, status="DEFER")


def test_review_cases_version_starts_at_one_and_cannot_go_below(
    connection: sqlite3.Connection,
) -> None:
    assert re.search(
        r"version INTEGER NOT NULL DEFAULT 1 CHECK \(version >= 1\)", CREATE_REVIEW_CASES
    )

    _insert_case(connection)
    assert connection.execute("SELECT version FROM review_cases").fetchone()[0] == 1

    with pytest.raises(sqlite3.IntegrityError):
        _insert_case(
            connection,
            review_case_id="RC-1",
            record_a_id="b-1",
            record_b_id="b-2",
            version=0,
        )


def test_review_cases_pair_is_unique(connection: sqlite3.Connection) -> None:
    _insert_case(connection)
    with pytest.raises(sqlite3.IntegrityError):
        _insert_case(connection, review_case_id="RC-other")


def test_review_cases_indexes_exist(connection: sqlite3.Connection) -> None:
    indexed = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'review_cases'"
        )
    }
    assert {
        "ix_review_cases_status",
        "ix_review_cases_record_a_id",
        "ix_review_cases_record_b_id",
    } <= indexed


# --------------------------------------------------------------------------
# review_case_events
# --------------------------------------------------------------------------


def test_event_type_tokens_are_exactly_the_application_enum() -> None:
    tokens = _check_in_list(CREATE_REVIEW_CASE_EVENTS, "event_type")
    assert tokens == EXPECTED_EVENT_TYPES
    assert tokens == ALLOWED_REVIEW_EVENT_TYPES
    assert tokens == {event.value for event in ReviewEventType}


@pytest.mark.parametrize("event_type", ["MATCH", "NO_MATCH", "DEFERRED"])
def test_resolution_event_requires_audit_payload_and_sequence(
    connection: sqlite3.Connection, event_type: str
) -> None:
    _insert_case(connection)

    with pytest.raises(sqlite3.IntegrityError):
        _insert_event(connection, event_type=event_type, resolution_sequence=1)

    with pytest.raises(sqlite3.IntegrityError):
        _insert_event(connection, event_type=event_type, audit_entry_json="{}")

    _insert_event(
        connection,
        event_type=event_type,
        resolution_sequence=1,
        reviewer_id="reviewer-1",
        audit_entry_json="{}",
    )


def test_semantic_event_requires_suggestion_id_and_no_resolution_data(
    connection: sqlite3.Connection,
) -> None:
    _insert_case(connection)

    with pytest.raises(sqlite3.IntegrityError):
        _insert_event(connection, event_type="SEMANTIC_SUGGESTION_RECORDED")

    # A suggestion row that also carried a sequence would be indistinguishable
    # from a decision in the history projection.
    with pytest.raises(sqlite3.IntegrityError):
        _insert_event(
            connection,
            event_type="SEMANTIC_SUGGESTION_RECORDED",
            suggestion_id="LS-0123456789abcdef",
            resolution_sequence=1,
        )

    _insert_event(
        connection,
        event_type="SEMANTIC_SUGGESTION_RECORDED",
        suggestion_id="LS-0123456789abcdef",
    )


def test_case_created_event_cannot_carry_a_decision(connection: sqlite3.Connection) -> None:
    _insert_case(connection)

    _insert_event(connection, event_type="CASE_CREATED")

    with pytest.raises(sqlite3.IntegrityError):
        _insert_event(
            connection,
            event_type="CASE_CREATED",
            resolution_sequence=1,
            audit_entry_json="{}",
        )


def test_resolution_sequence_is_unique_per_case(connection: sqlite3.Connection) -> None:
    _insert_case(connection)
    _insert_event(connection, event_type="MATCH", resolution_sequence=1, audit_entry_json="{}")

    with pytest.raises(sqlite3.IntegrityError):
        _insert_event(
            connection, event_type="NO_MATCH", resolution_sequence=1, audit_entry_json="{}"
        )

    # NULL sequences on lifecycle rows stay unaffected by the partial index.
    _insert_event(connection, event_type="CASE_CREATED")
    _insert_event(connection, event_type="CASE_CREATED")


def test_events_reference_cases_with_restrict(connection: sqlite3.Connection) -> None:
    keys = connection.execute("PRAGMA foreign_key_list(review_case_events)").fetchall()
    assert [(key[2], key[3], key[4], key[6]) for key in keys] == [
        ("review_cases", "review_case_id", "review_case_id", "RESTRICT")
    ]

    _insert_case(connection)
    _insert_event(connection, event_type="CASE_CREATED")

    with pytest.raises(sqlite3.IntegrityError):
        _insert_event(connection, event_type="CASE_CREATED", review_case_id="RC-unknown")

    # History is never orphaned by deleting the case it describes.
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute("DELETE FROM review_cases WHERE review_case_id = ?", (CASE_ID,))


# --------------------------------------------------------------------------
# semantic_suggestions
# --------------------------------------------------------------------------


def test_suggestion_tokens_are_exactly_the_four_advisory_values() -> None:
    tokens = _check_in_list(CREATE_SEMANTIC_SUGGESTIONS, "suggestion")
    assert tokens == EXPECTED_SEMANTIC_SUGGESTION_VALUES
    assert tokens == ALLOWED_SEMANTIC_SUGGESTION_VALUES


def test_suggestion_tokens_exclude_authoritative_human_decisions() -> None:
    tokens = _check_in_list(CREATE_SEMANTIC_SUGGESTIONS, "suggestion")

    # Set membership, not substring search: 'NO_MATCH' in 'SUGGEST_NO_MATCH' is
    # true, so a substring assertion here would be meaningless.
    assert tokens.isdisjoint(FORBIDDEN_SEMANTIC_SUGGESTION_VALUES)
    assert FORBIDDEN_SEMANTIC_SUGGESTION_VALUES == {"MATCH", "NO_MATCH", "DEFER", "DEFERRED"}
    assert "SUGGEST_NO_MATCH" in tokens


@pytest.mark.parametrize("forbidden", ["MATCH", "NO_MATCH", "DEFER", "DEFERRED"])
def test_database_rejects_a_human_decision_as_a_suggestion(
    connection: sqlite3.Connection, forbidden: str
) -> None:
    _insert_case(connection)
    with pytest.raises(sqlite3.IntegrityError):
        _insert_suggestion(connection, suggestion=forbidden)


@pytest.mark.parametrize("allowed", sorted(EXPECTED_SEMANTIC_SUGGESTION_VALUES))
def test_database_accepts_every_advisory_value(
    connection: sqlite3.Connection, allowed: str
) -> None:
    _insert_case(connection)
    _insert_suggestion(connection, suggestion=allowed, suggestion_id=f"LS-{allowed}")


def test_suggestion_id_is_insert_if_absent(connection: sqlite3.Connection) -> None:
    # Sprint 09 suggestion_id is content-addressed, so the primary key is what
    # makes re-recording the same observation an idempotent no-op.
    _insert_case(connection)
    _insert_suggestion(connection, suggestion="SUGGEST_MATCH")

    with pytest.raises(sqlite3.IntegrityError):
        _insert_suggestion(connection, suggestion="SUGGEST_MATCH")

    connection.execute(
        "INSERT OR IGNORE INTO semantic_suggestions ("
        "suggestion_id, review_case_id, record_a_id, record_b_id, suggestion, "
        "failure_code, live, provider, requested_model, suggestion_payload_json, "
        "created_at_utc, schema_version"
        ") VALUES ('LS-0123456789abcdef', ?, 'a-1', 'a-2', 'SUGGEST_NO_MATCH', NULL, 0, "
        "'simulated', 'test-model', '{}', ?, ?)",
        (CASE_ID, NOW, DATABASE_SCHEMA_VERSION),
    )
    stored = connection.execute("SELECT suggestion FROM semantic_suggestions").fetchall()
    assert stored == [("SUGGEST_MATCH",)]


def test_suggestions_reference_cases_with_restrict(connection: sqlite3.Connection) -> None:
    keys = connection.execute("PRAGMA foreign_key_list(semantic_suggestions)").fetchall()
    assert [(key[2], key[3], key[4], key[6]) for key in keys] == [
        ("review_cases", "review_case_id", "review_case_id", "RESTRICT")
    ]

    with pytest.raises(sqlite3.IntegrityError):
        _insert_suggestion(connection, suggestion="SUGGEST_MATCH", review_case_id="RC-unknown")


# --------------------------------------------------------------------------
# Forbidden data
# --------------------------------------------------------------------------


@pytest.mark.parametrize("pattern", FORBIDDEN_SCHEMA_TOKENS)
def test_schema_module_declares_no_ground_truth_or_tuning_data(pattern: str) -> None:
    source = Path("review_persistence/schema.py").read_text(encoding="utf-8")
    assert not re.search(pattern, source, re.IGNORECASE), pattern


@pytest.mark.parametrize("pattern", FORBIDDEN_SCHEMA_TOKENS)
def test_no_table_exposes_a_forbidden_column(connection: sqlite3.Connection, pattern: str) -> None:
    for table in ALL_TABLES:
        columns = [row[1] for row in connection.execute(f"PRAGMA table_info({table})")]
        offenders = [column for column in columns if re.search(pattern, column, re.IGNORECASE)]
        assert not offenders, f"{table}: {offenders}"
