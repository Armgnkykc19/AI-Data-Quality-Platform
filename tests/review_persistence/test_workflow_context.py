from __future__ import annotations

import dataclasses
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from entity_resolution.models import EntityRecord, ResolutionResult
from human_review.models import ReviewStatus, ReviewWorkflowState
from human_review.reporting import entity_records_to_dict
from review_application.errors import (
    DuplicateCaseRegistrationError,
    PersistedCaseIntegrityError,
    ReviewPersistenceError,
    ReviewWorkflowContextConflictError,
    ReviewWorkflowContextMissingError,
)
from review_application.models import WorkflowBundle
from review_persistence.schema import (
    ALL_TABLES,
    CREATE_REVIEW_WORKFLOW_CONTEXT,
    DATABASE_SCHEMA_VERSION,
    REVIEW_WORKFLOW_CONTEXT_TABLE,
    SUPPORTED_DATABASE_SCHEMA_VERSIONS,
    WORKFLOW_CONTEXT_ID,
)
from review_persistence.sqlite.context_mapper import (
    canonical_json,
    entity_records_from_payload,
    normalized_context_fingerprint,
)
from review_persistence.sqlite.database import ReviewDatabase
from review_persistence.sqlite.review_repository import SqliteReviewCaseRepository
from tests.review_persistence.conftest import FrozenClock

CONTEXT_PATH = "configs/entity_resolution.yaml"

FORBIDDEN_SCHEMA_TOKENS = (
    r"\bperson_id\b",
    r"\bexpected_person_id\b",
    r"\boracle\b",
    r"\bground_truth\b",
    r"\bfinal_holdout\b",
    r"\bdataset_split\b",
)


def _register(
    repository: SqliteReviewCaseRepository,
    state: ReviewWorkflowState,
    resolution: ResolutionResult,
    snapshot: dict[str, Any],
    **overrides: Any,
) -> tuple[Any, ...]:
    kwargs: dict[str, Any] = {
        "entity_records": resolution.records,
        "resolution_snapshot": snapshot,
        "entity_resolution_config_path": CONTEXT_PATH,
    }
    kwargs.update(overrides)
    return repository.register_workflow(state, **kwargs)


def _impostor(case: Any, *, record_b_id: str = "z-999") -> Any:
    """Same review_case_id, different pair -- and internally consistent.

    record_ids must move with the pair: ReviewCase.to_dict() derives the stored
    record ids from the pair, and the Sprint 08 loader rebuilds record_ids from
    those, so a fixture that changes only one of them is not a case the domain
    could ever produce.
    """
    pair = dataclasses.replace(case.pair, record_b_id=record_b_id)
    return dataclasses.replace(case, pair=pair, record_ids=(pair.record_a_id, pair.record_b_id))


# --------------------------------------------------------------------------
# A. Workflow context schema
# --------------------------------------------------------------------------


def test_workflow_context_table_exists(database: ReviewDatabase) -> None:
    tables = {
        row["name"]
        for row in database.connect().execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    assert REVIEW_WORKFLOW_CONTEXT_TABLE in tables
    assert tables == set(ALL_TABLES)


def test_workflow_context_is_a_singleton(database: ReviewDatabase) -> None:
    # One database is one review queue, so a second context row would mean two
    # authorization graphs competing over the same cases.
    assert re.search(r"CHECK \(context_id = 1\)", CREATE_REVIEW_WORKFLOW_CONTEXT)

    connection = database.connect()
    connection.execute(
        f"INSERT INTO {REVIEW_WORKFLOW_CONTEXT_TABLE} (context_id, entity_records_json, "
        "resolution_snapshot_json, schema_version, created_at_utc, updated_at_utc) "
        "VALUES (1, '[]', '{}', ?, 'now', 'now')",
        (DATABASE_SCHEMA_VERSION,),
    )
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            f"INSERT INTO {REVIEW_WORKFLOW_CONTEXT_TABLE} (context_id, entity_records_json, "
            "resolution_snapshot_json, schema_version, created_at_utc, updated_at_utc) "
            "VALUES (2, '[]', '{}', ?, 'now', 'now')",
            (DATABASE_SCHEMA_VERSION,),
        )


@pytest.mark.parametrize(
    "column",
    ["entity_records_json", "resolution_snapshot_json", "schema_version"],
)
def test_workflow_context_required_columns_are_not_null(
    database: ReviewDatabase, column: str
) -> None:
    columns = {
        row["name"]: row
        for row in database.connect().execute(f"PRAGMA table_info({REVIEW_WORKFLOW_CONTEXT_TABLE})")
    }
    assert columns[column]["notnull"] == 1


def test_workflow_context_config_path_is_nullable(database: ReviewDatabase) -> None:
    # Provenance, not an authorization input: the ER config is loaded from disk
    # at check time, so a workflow may legitimately omit the path.
    columns = {
        row["name"]: row
        for row in database.connect().execute(f"PRAGMA table_info({REVIEW_WORKFLOW_CONTEXT_TABLE})")
    }
    assert columns["entity_resolution_config_path"]["notnull"] == 0


@pytest.mark.parametrize("pattern", FORBIDDEN_SCHEMA_TOKENS)
def test_workflow_context_declares_no_forbidden_data(
    database: ReviewDatabase, pattern: str
) -> None:
    columns = [
        row["name"]
        for row in database.connect().execute(f"PRAGMA table_info({REVIEW_WORKFLOW_CONTEXT_TABLE})")
    ]
    assert not [column for column in columns if re.search(pattern, column, re.IGNORECASE)]
    assert not re.search(pattern, CREATE_REVIEW_WORKFLOW_CONTEXT, re.IGNORECASE)


def test_schema_version_is_still_the_initial_unreleased_version() -> None:
    # No Sprint 10 database has been released, so adding this table is part of
    # the initial schema, not a migration from an older one.
    assert DATABASE_SCHEMA_VERSION == "1.0.0"
    assert SUPPORTED_DATABASE_SCHEMA_VERSIONS == frozenset({"1.0.0"})


# --------------------------------------------------------------------------
# B. Context round-trip
# --------------------------------------------------------------------------


def test_entity_records_survive_the_round_trip(
    repository: SqliteReviewCaseRepository,
    review_state: ReviewWorkflowState,
    resolution: ResolutionResult,
    snapshot: dict[str, Any],
) -> None:
    _register(repository, review_state, resolution, snapshot)

    stored = repository.workflow_context()

    assert stored is not None
    assert stored.entity_records() == resolution.records


def test_reduced_auto_match_snapshot_survives_the_round_trip(
    repository: SqliteReviewCaseRepository,
    review_state: ReviewWorkflowState,
    bridge_resolution: ResolutionResult,
) -> None:
    from entity_resolution.config import load_entity_resolution_config
    from human_review.cases import generate_review_cases
    from human_review.reporting import resolution_snapshot

    state = generate_review_cases(bridge_resolution, config=load_entity_resolution_config())
    original = resolution_snapshot(bridge_resolution)
    assert original["auto_match_pairs"], "Fixture must carry AUTO_MATCH edges."

    _register(repository, state, bridge_resolution, original)
    stored = repository.workflow_context()

    assert stored is not None
    assert stored.resolution_snapshot == original


def test_stored_json_is_canonical(
    repository: SqliteReviewCaseRepository,
    database: ReviewDatabase,
    review_state: ReviewWorkflowState,
    resolution: ResolutionResult,
    snapshot: dict[str, Any],
) -> None:
    _register(repository, review_state, resolution, snapshot)

    row = (
        database.connect()
        .execute(
            f"SELECT entity_records_json, resolution_snapshot_json FROM "
            f"{REVIEW_WORKFLOW_CONTEXT_TABLE} WHERE context_id = {WORKFLOW_CONTEXT_ID}"
        )
        .fetchone()
    )

    records_json = row["entity_records_json"]
    assert records_json == canonical_json(json.loads(records_json))
    snapshot_json = row["resolution_snapshot_json"]
    assert snapshot_json == canonical_json(json.loads(snapshot_json))


def test_context_serialization_reuses_the_public_sprint_08_helper(
    resolution: ResolutionResult,
) -> None:
    from review_persistence.sqlite.context_mapper import entity_records_to_payload

    assert entity_records_to_payload(resolution.records) == entity_records_to_dict(
        resolution.records
    )


def test_entity_record_reconstruction_matches_the_report_loader(
    tmp_path: Path,
    resolution: ResolutionResult,
) -> None:
    # Persistence must not become a second, subtly different deserializer.
    payload = json.loads(json.dumps(entity_records_to_dict(resolution.records)))
    assert entity_records_from_payload(payload) == resolution.records


def test_malformed_context_json_fails_closed(
    repository: SqliteReviewCaseRepository,
    database: ReviewDatabase,
    review_state: ReviewWorkflowState,
    resolution: ResolutionResult,
    snapshot: dict[str, Any],
) -> None:
    _register(repository, review_state, resolution, snapshot)
    with database.transaction() as conn:
        conn.execute(f"UPDATE {REVIEW_WORKFLOW_CONTEXT_TABLE} SET entity_records_json = 'oops'")

    with pytest.raises(ReviewPersistenceError, match="not valid JSON"):
        repository.load_workflow_bundle()


def test_empty_entity_records_are_refused(
    repository: SqliteReviewCaseRepository,
    review_state: ReviewWorkflowState,
    resolution: ResolutionResult,
    snapshot: dict[str, Any],
) -> None:
    with pytest.raises(ReviewPersistenceError, match="requires entity records"):
        _register(repository, review_state, resolution, snapshot, entity_records=())


def test_snapshot_missing_auto_match_pairs_is_refused(
    repository: SqliteReviewCaseRepository,
    review_state: ReviewWorkflowState,
    resolution: ResolutionResult,
) -> None:
    with pytest.raises(ReviewPersistenceError, match="missing required keys"):
        _register(repository, review_state, resolution, {"source_label": "test"})


def test_workflow_refuses_records_that_do_not_cover_its_cases(
    repository: SqliteReviewCaseRepository,
    review_state: ReviewWorkflowState,
    resolution: ResolutionResult,
    snapshot: dict[str, Any],
) -> None:
    partial = (resolution.records[0],)

    with pytest.raises(ReviewPersistenceError, match="fail closed"):
        _register(repository, review_state, resolution, snapshot, entity_records=partial)

    assert repository.workflow_context() is None
    assert repository.list_cases() == ()


# --------------------------------------------------------------------------
# C. register_workflow
# --------------------------------------------------------------------------


def test_new_workflow_inserts_context_and_cases_atomically(
    repository: SqliteReviewCaseRepository,
    review_state: ReviewWorkflowState,
    resolution: ResolutionResult,
    snapshot: dict[str, Any],
) -> None:
    persisted = _register(repository, review_state, resolution, snapshot)

    assert len(persisted) == len(review_state.cases)
    assert all(item.version == 1 for item in persisted)
    assert all(
        item.created_at_utc == item.updated_at_utc == "2026-09-12T08:00:00Z" for item in persisted
    )

    stored = repository.workflow_context()
    assert stored is not None
    assert stored.created_at_utc == stored.updated_at_utc == "2026-09-12T08:00:00Z"
    assert stored.entity_resolution_config_path == CONTEXT_PATH


def test_identical_re_registration_is_a_no_op(
    repository: SqliteReviewCaseRepository,
    database: ReviewDatabase,
    review_state: ReviewWorkflowState,
    resolution: ResolutionResult,
    snapshot: dict[str, Any],
    clock: FrozenClock,
) -> None:
    _register(repository, review_state, resolution, snapshot)
    before_cases = [dict(row) for row in database.connect().execute("SELECT * FROM review_cases")]
    before_context = [
        dict(row)
        for row in database.connect().execute(f"SELECT * FROM {REVIEW_WORKFLOW_CONTEXT_TABLE}")
    ]

    clock.advance(86400)
    again = _register(repository, review_state, resolution, snapshot)

    after_cases = [dict(row) for row in database.connect().execute("SELECT * FROM review_cases")]
    after_context = [
        dict(row)
        for row in database.connect().execute(f"SELECT * FROM {REVIEW_WORKFLOW_CONTEXT_TABLE}")
    ]

    assert after_cases == before_cases
    assert after_context == before_context
    assert all(item.version == 1 for item in again)


def test_re_registration_with_reordered_content_is_not_a_conflict(
    repository: SqliteReviewCaseRepository,
    bridge_resolution: ResolutionResult,
) -> None:
    """Record and edge order carry no meaning; only content does."""
    from entity_resolution.config import load_entity_resolution_config
    from human_review.cases import generate_review_cases
    from human_review.reporting import resolution_snapshot

    state = generate_review_cases(bridge_resolution, config=load_entity_resolution_config())
    original = resolution_snapshot(bridge_resolution)
    _register(repository, state, bridge_resolution, original)

    shuffled_snapshot = {
        "source_label": original["source_label"],
        "auto_match_pairs": list(reversed(original["auto_match_pairs"])),
    }
    reordered_records = tuple(reversed(bridge_resolution.records))

    _register(
        repository,
        state,
        bridge_resolution,
        shuffled_snapshot,
        entity_records=reordered_records,
    )

    stored = repository.workflow_context()
    assert stored is not None
    assert stored.resolution_snapshot == original


def test_fingerprint_ignores_dict_key_order() -> None:
    left = normalized_context_fingerprint(
        [{"record_id": "a", "source_name": "s", "field_values": {"x": "1", "y": "2"}}],
        {"source_label": "t", "auto_match_pairs": [["a", "b"]]},
    )
    right = normalized_context_fingerprint(
        [{"field_values": {"y": "2", "x": "1"}, "source_name": "s", "record_id": "a"}],
        {"auto_match_pairs": [["a", "b"]], "source_label": "t"},
    )
    assert left == right


# --------------------------------------------------------------------------
# D. Resolved-case preservation
# --------------------------------------------------------------------------


def test_resolved_case_is_not_reset_by_re_registering_the_pending_workflow(
    repository: SqliteReviewCaseRepository,
    review_state: ReviewWorkflowState,
    resolved_match_state: ReviewWorkflowState,
    resolution: ResolutionResult,
    snapshot: dict[str, Any],
    clock: FrozenClock,
) -> None:
    """Case generation is deterministic and always emits the PENDING form.

    If re-running it overwrote storage, a recorded human MATCH would vanish.
    """
    stored = _register(repository, resolved_match_state, resolution, snapshot)
    assert stored[0].status is ReviewStatus.MATCH

    clock.advance(7200)
    returned = _register(repository, review_state, resolution, snapshot)

    assert returned[0].status is ReviewStatus.MATCH
    assert returned[0].version == stored[0].version == 1
    assert returned[0].created_at_utc == stored[0].created_at_utc
    assert returned[0].updated_at_utc == stored[0].updated_at_utc

    reloaded = repository.get_case(stored[0].review_case_id)
    assert reloaded.case == resolved_match_state.cases[0]
    assert reloaded.case.resolution is not None


# --------------------------------------------------------------------------
# E. Context conflict
# --------------------------------------------------------------------------


def test_changed_entity_record_is_a_typed_context_conflict(
    repository: SqliteReviewCaseRepository,
    review_state: ReviewWorkflowState,
    resolution: ResolutionResult,
    snapshot: dict[str, Any],
) -> None:
    _register(repository, review_state, resolution, snapshot)
    mutated = (
        dataclasses.replace(
            resolution.records[0],
            field_values={**resolution.records[0].field_values, "email": "changed@example.com"},
        ),
    ) + resolution.records[1:]

    with pytest.raises(ReviewWorkflowContextConflictError, match="different record set"):
        _register(repository, review_state, resolution, snapshot, entity_records=mutated)


def test_changed_auto_match_snapshot_is_a_typed_context_conflict(
    repository: SqliteReviewCaseRepository,
    review_state: ReviewWorkflowState,
    resolution: ResolutionResult,
    snapshot: dict[str, Any],
) -> None:
    _register(repository, review_state, resolution, snapshot)
    tampered = {
        "source_label": snapshot["source_label"],
        "auto_match_pairs": [["a-1", "a-2"]],
    }

    with pytest.raises(ReviewWorkflowContextConflictError, match="AUTO_MATCH"):
        _register(repository, review_state, resolution, tampered)


def test_context_conflict_is_not_a_case_registration_error(
    repository: SqliteReviewCaseRepository,
    review_state: ReviewWorkflowState,
    resolution: ResolutionResult,
    snapshot: dict[str, Any],
) -> None:
    # A global context conflict must not be reported as one case's identity
    # problem; an operator would look in entirely the wrong place.
    _register(repository, review_state, resolution, snapshot)
    tampered = {"source_label": "other", "auto_match_pairs": []}

    with pytest.raises(ReviewWorkflowContextConflictError) as caught:
        _register(repository, review_state, resolution, tampered)

    assert not isinstance(caught.value, DuplicateCaseRegistrationError)


def test_changed_config_path_is_a_typed_context_conflict(
    repository: SqliteReviewCaseRepository,
    review_state: ReviewWorkflowState,
    resolution: ResolutionResult,
    snapshot: dict[str, Any],
) -> None:
    _register(repository, review_state, resolution, snapshot)

    with pytest.raises(ReviewWorkflowContextConflictError, match="thresholds"):
        _register(
            repository,
            review_state,
            resolution,
            snapshot,
            entity_resolution_config_path="configs/other_er.yaml",
        )


def test_context_conflict_leaves_nothing_partially_changed(
    repository: SqliteReviewCaseRepository,
    database: ReviewDatabase,
    review_state: ReviewWorkflowState,
    resolution: ResolutionResult,
    snapshot: dict[str, Any],
    bridge_resolution: ResolutionResult,
) -> None:
    from entity_resolution.config import load_entity_resolution_config
    from human_review.cases import generate_review_cases
    from human_review.reporting import resolution_snapshot

    _register(repository, review_state, resolution, snapshot)
    before_cases = [dict(row) for row in database.connect().execute("SELECT * FROM review_cases")]
    before_context = [
        dict(row)
        for row in database.connect().execute(f"SELECT * FROM {REVIEW_WORKFLOW_CONTEXT_TABLE}")
    ]

    # A completely different workflow: new records, new cases, new snapshot.
    other_state = generate_review_cases(bridge_resolution, config=load_entity_resolution_config())
    with pytest.raises(ReviewWorkflowContextConflictError):
        _register(
            repository,
            other_state,
            bridge_resolution,
            resolution_snapshot(bridge_resolution),
        )

    after_cases = [dict(row) for row in database.connect().execute("SELECT * FROM review_cases")]
    after_context = [
        dict(row)
        for row in database.connect().execute(f"SELECT * FROM {REVIEW_WORKFLOW_CONTEXT_TABLE}")
    ]
    assert after_cases == before_cases
    assert after_context == before_context


# --------------------------------------------------------------------------
# F. load_workflow_bundle
# --------------------------------------------------------------------------


def test_bundle_carries_every_case_including_resolved_ones(
    repository: SqliteReviewCaseRepository,
    resolved_match_state: ReviewWorkflowState,
    resolution: ResolutionResult,
    snapshot: dict[str, Any],
) -> None:
    _register(repository, resolved_match_state, resolution, snapshot)

    bundle = repository.load_workflow_bundle()

    assert bundle.cases() == resolved_match_state.cases
    assert any(case.status is ReviewStatus.MATCH for case in bundle.cases())
    assert set(bundle.versions_by_case_id().values()) == {1}


def test_bundle_carries_records_and_snapshot(
    repository: SqliteReviewCaseRepository,
    review_state: ReviewWorkflowState,
    resolution: ResolutionResult,
    snapshot: dict[str, Any],
) -> None:
    _register(repository, review_state, resolution, snapshot)

    bundle = repository.load_workflow_bundle()

    assert isinstance(bundle, WorkflowBundle)
    assert bundle.entity_records == resolution.records
    assert bundle.resolution_snapshot == snapshot
    assert bundle.entity_resolution_config_path == CONTEXT_PATH
    assert set(bundle.records_by_id()) == {record.record_id for record in resolution.records}


def test_bundle_ordering_is_deterministic(
    repository: SqliteReviewCaseRepository,
    bridge_resolution: ResolutionResult,
) -> None:
    from entity_resolution.config import load_entity_resolution_config
    from human_review.cases import generate_review_cases
    from human_review.reporting import resolution_snapshot

    state = generate_review_cases(bridge_resolution, config=load_entity_resolution_config())
    _register(repository, state, bridge_resolution, resolution_snapshot(bridge_resolution))

    first = [item.review_case_id for item in repository.load_workflow_bundle().persisted_cases]
    second = [item.review_case_id for item in repository.load_workflow_bundle().persisted_cases]

    assert first == second == sorted(first)


def test_bundle_derives_next_resolution_sequence_from_stored_resolutions(
    repository: SqliteReviewCaseRepository,
    review_state: ReviewWorkflowState,
    resolved_match_state: ReviewWorkflowState,
    resolution: ResolutionResult,
    snapshot: dict[str, Any],
) -> None:
    _register(repository, review_state, resolution, snapshot)
    assert repository.load_workflow_bundle().next_resolution_sequence == 1

    # A resolved case carries the sequence it was stamped with, so the counter
    # is recoverable without the Phase D event table.
    assert resolved_match_state.next_resolution_sequence == 2


def test_bundle_without_a_context_fails_closed(
    repository: SqliteReviewCaseRepository,
    review_case: Any,
) -> None:
    # register_case does not create a context. Returning cases alone would hand
    # authorization a graph with no records and no AUTO_MATCH edges.
    repository.register_case(review_case)

    with pytest.raises(ReviewWorkflowContextMissingError, match="partial bundle"):
        repository.load_workflow_bundle()


def test_bundle_on_an_empty_database_fails_closed(
    repository: SqliteReviewCaseRepository,
) -> None:
    with pytest.raises(ReviewWorkflowContextMissingError):
        repository.load_workflow_bundle()


def test_case_outside_the_context_record_set_fails_closed(
    repository: SqliteReviewCaseRepository,
    review_state: ReviewWorkflowState,
    resolution: ResolutionResult,
    snapshot: dict[str, Any],
    bridge_resolution: ResolutionResult,
) -> None:
    """A case smuggled in by register_case cannot silently join the bundle."""
    from entity_resolution.config import load_entity_resolution_config
    from human_review.cases import generate_review_cases

    _register(repository, review_state, resolution, snapshot)
    foreign = generate_review_cases(
        bridge_resolution, config=load_entity_resolution_config()
    ).cases[0]
    repository.register_case(foreign)

    with pytest.raises(PersistedCaseIntegrityError, match="fail closed"):
        repository.load_workflow_bundle()


def test_bundle_reads_context_and_cases_in_one_transaction(
    repository: SqliteReviewCaseRepository,
    database: ReviewDatabase,
    review_state: ReviewWorkflowState,
    resolution: ResolutionResult,
    snapshot: dict[str, Any],
) -> None:
    """Two unrelated reads could observe a state that never existed."""
    _register(repository, review_state, resolution, snapshot)

    traced: list[str] = []
    connection = database.connect()
    connection.set_trace_callback(traced.append)
    try:
        repository.load_workflow_bundle()
    finally:
        connection.set_trace_callback(None)

    assert traced[0].strip().upper().startswith("BEGIN DEFERRED")
    assert traced[-1].strip().upper().startswith("COMMIT")

    inner = traced[1:-1]
    assert not any("COMMIT" in statement.upper() for statement in inner)
    assert any(REVIEW_WORKFLOW_CONTEXT_TABLE in statement for statement in inner)
    assert any("FROM review_cases" in statement for statement in inner)


# --------------------------------------------------------------------------
# H. Rollback
# --------------------------------------------------------------------------


def test_case_conflict_during_registration_rolls_back_the_new_context(
    repository: SqliteReviewCaseRepository,
    review_state: ReviewWorkflowState,
    review_case: Any,
    resolution: ResolutionResult,
    snapshot: dict[str, Any],
) -> None:
    """Context is written first, so a later case failure must undo it."""
    impostor = _impostor(review_case)
    repository.register_case(impostor)
    assert repository.workflow_context() is None

    with pytest.raises(DuplicateCaseRegistrationError):
        _register(repository, review_state, resolution, snapshot)

    # No context-without-cases and no half-registered workflow survived.
    assert repository.workflow_context() is None
    stored = repository.list_cases()
    assert [item.review_case_id for item in stored] == [impostor.review_case_id]
    assert stored[0].case == impostor


def test_failed_registration_leaves_no_new_cases(
    repository: SqliteReviewCaseRepository,
    review_state: ReviewWorkflowState,
    review_case: Any,
    resolution: ResolutionResult,
    snapshot: dict[str, Any],
    bridge_resolution: ResolutionResult,
) -> None:
    from entity_resolution.config import load_entity_resolution_config
    from human_review.cases import generate_review_cases

    impostor = _impostor(review_case)
    repository.register_case(impostor)

    extra = generate_review_cases(bridge_resolution, config=load_entity_resolution_config()).cases
    combined = ReviewWorkflowState(
        cases=extra + review_state.cases,
        audit_trail=(),
        next_resolution_sequence=1,
    )
    records = tuple(bridge_resolution.records) + tuple(resolution.records)

    with pytest.raises(DuplicateCaseRegistrationError):
        _register(repository, combined, resolution, snapshot, entity_records=records)

    # The bridge cases were inserted before the conflicting one; all rolled back.
    assert [item.review_case_id for item in repository.list_cases()] == [impostor.review_case_id]
    assert repository.workflow_context() is None


def test_entity_record_type_is_preserved_through_storage(
    repository: SqliteReviewCaseRepository,
    review_state: ReviewWorkflowState,
    resolution: ResolutionResult,
    snapshot: dict[str, Any],
) -> None:
    _register(repository, review_state, resolution, snapshot)
    bundle = repository.load_workflow_bundle()

    for record in bundle.entity_records:
        assert isinstance(record, EntityRecord)
        assert isinstance(record.field_values, dict)
