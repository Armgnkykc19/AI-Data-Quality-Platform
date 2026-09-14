from __future__ import annotations

import ast
import dataclasses
import inspect
import json
from pathlib import Path

import pytest

from entity_resolution.config import EntityResolutionConfig
from entity_resolution.models import ResolutionResult
from human_review.errors import ReviewCaseNotFoundError
from human_review.models import (
    HumanReviewOutcome,
    ReviewCase,
    ReviewStatus,
    ReviewWorkflowState,
)
from human_review.reporting import load_human_review_report, write_review_reports
from review_application.errors import DuplicateCaseRegistrationError, ReviewPersistenceError
from review_application.models import PersistedCase
from review_persistence.schema import (
    DATABASE_SCHEMA_VERSION,
    REVIEW_CASE_EVENTS_TABLE,
    REVIEW_CASES_TABLE,
)
from review_persistence.sqlite import review_repository
from review_persistence.sqlite.database import ReviewDatabase
from review_persistence.sqlite.mapper import review_case_from_payload
from review_persistence.sqlite.review_repository import SqliteReviewCaseRepository
from tests.review_persistence.conftest import FrozenClock

# Any of these on the repository would be a decision-mutation path.
FORBIDDEN_REPOSITORY_METHODS = frozenset(
    {
        "authorize_match",
        "delete_case",
        "force_match",
        "force_no_match",
        "mark_match",
        "override_status",
        "resolve_case",
        "resolve_without_authorization",
        "set_status",
        "update_case",
        "update_status",
        "upsert_case",
    }
)


# --------------------------------------------------------------------------
# Mapper: the stored representation is the Sprint 08 representation
# --------------------------------------------------------------------------


def test_case_survives_a_database_round_trip_unchanged(
    repository: SqliteReviewCaseRepository, review_case: ReviewCase
) -> None:
    repository.register_case(review_case)

    loaded = repository.get_case(review_case.review_case_id).case

    assert loaded == review_case
    assert loaded.to_dict() == review_case.to_dict()


def test_round_trip_preserves_tuple_types_not_just_contents(
    repository: SqliteReviewCaseRepository, review_case: ReviewCase
) -> None:
    repository.register_case(review_case)
    loaded = repository.get_case(review_case.review_case_id).case

    for field in dataclasses.fields(ReviewCase):
        original = getattr(review_case, field.name)
        restored = getattr(loaded, field.name)
        assert type(restored) is type(original), field.name
        assert restored == original, field.name


def test_round_trip_preserves_evidence_and_reason_detail(
    repository: SqliteReviewCaseRepository, review_case: ReviewCase
) -> None:
    assert review_case.supporting_evidence, "Fixture must carry evidence to be meaningful."
    repository.register_case(review_case)

    loaded = repository.get_case(review_case.review_case_id).case

    assert loaded.supporting_evidence == review_case.supporting_evidence
    assert loaded.conflicting_evidence == review_case.conflicting_evidence
    assert loaded.blocking_reasons == review_case.blocking_reasons
    assert loaded.missing_evidence_notes == review_case.missing_evidence_notes
    assert loaded.machine_readable_reasons == review_case.machine_readable_reasons
    assert loaded.human_summary == review_case.human_summary


def test_round_trip_preserves_pair_ordering_and_ids(
    repository: SqliteReviewCaseRepository, review_case: ReviewCase
) -> None:
    repository.register_case(review_case)
    loaded = repository.get_case(review_case.review_case_id).case

    assert loaded.review_case_id == review_case.review_case_id
    assert loaded.pair == review_case.pair
    assert loaded.record_ids == review_case.record_ids
    assert loaded.pair.record_a_id <= loaded.pair.record_b_id


def test_round_trip_preserves_a_resolved_case_and_its_resolution(
    repository: SqliteReviewCaseRepository, resolved_match_case: ReviewCase
) -> None:
    repository.register_case(resolved_match_case)
    loaded = repository.get_case(resolved_match_case.review_case_id).case

    assert loaded.status is ReviewStatus.MATCH
    assert loaded.resolution == resolved_match_case.resolution
    assert loaded == resolved_match_case


def test_mapper_agrees_with_the_sprint_08_report_loader(
    tmp_path: Path,
    review_state: ReviewWorkflowState,
    resolution: ResolutionResult,
    resolution_config: EntityResolutionConfig,
    review_case: ReviewCase,
) -> None:
    """Pins the mapper to the public Sprint 08 serialization contract.

    Persistence must not become a second, subtly different deserializer. This
    writes a real human_review_report.json and loads it back through the
    public API, then rebuilds the same case through the mapper and requires
    the two to be identical.
    """
    write_review_reports(
        HumanReviewOutcome(workflow_state=review_state),
        output_directory=tmp_path,
        entity_records=resolution.records,
        resolution=resolution,
        entity_resolution_config_path=str(resolution_config.report_output_directory),
    )
    from_report = load_human_review_report(tmp_path / "human_review_report.json")
    report_case = from_report.outcome.workflow_state.case_by_id(review_case.review_case_id)

    from_mapper = review_case_from_payload(json.loads(json.dumps(review_case.to_dict())))

    assert report_case is not None
    assert from_mapper == report_case


def test_stored_payload_is_the_unmodified_sprint_08_dict(
    repository: SqliteReviewCaseRepository,
    database: ReviewDatabase,
    review_case: ReviewCase,
) -> None:
    repository.register_case(review_case)

    stored = (
        database.connect()
        .execute(
            "SELECT case_payload_json, schema_version FROM review_cases WHERE review_case_id = ?",
            (review_case.review_case_id,),
        )
        .fetchone()
    )

    assert json.loads(stored["case_payload_json"]) == review_case.to_dict()
    assert stored["schema_version"] == DATABASE_SCHEMA_VERSION


def test_row_that_contradicts_its_payload_is_refused(
    repository: SqliteReviewCaseRepository,
    database: ReviewDatabase,
    review_case: ReviewCase,
) -> None:
    # Only reachable by editing the database outside this code. Loading it
    # would hand the domain a case whose status is not the one the queue was
    # filtered on, so it fails closed instead.
    repository.register_case(review_case)
    with database.transaction() as conn:
        conn.execute(
            "UPDATE review_cases SET status = 'MATCH' WHERE review_case_id = ?",
            (review_case.review_case_id,),
        )

    with pytest.raises(ReviewPersistenceError, match="disagrees with its payload"):
        repository.get_case(review_case.review_case_id)


def test_unparseable_payload_is_refused(
    repository: SqliteReviewCaseRepository,
    database: ReviewDatabase,
    review_case: ReviewCase,
) -> None:
    repository.register_case(review_case)
    with database.transaction() as conn:
        conn.execute(
            "UPDATE review_cases SET case_payload_json = 'not json' WHERE review_case_id = ?",
            (review_case.review_case_id,),
        )

    with pytest.raises(ReviewPersistenceError, match="not valid JSON"):
        repository.get_case(review_case.review_case_id)


def test_incomplete_payload_is_refused() -> None:
    with pytest.raises(ReviewPersistenceError, match="missing required fields"):
        review_case_from_payload({"review_case_id": "RC-1"})


# --------------------------------------------------------------------------
# Registration
# --------------------------------------------------------------------------


def test_first_registration_returns_version_one_with_equal_timestamps(
    repository: SqliteReviewCaseRepository, review_case: ReviewCase
) -> None:
    persisted = repository.register_case(review_case)

    assert persisted.version == 1
    assert persisted.created_at_utc == persisted.updated_at_utc == "2026-09-12T08:00:00Z"
    assert persisted.review_case_id == review_case.review_case_id


def test_duplicate_registration_is_a_true_no_op(
    repository: SqliteReviewCaseRepository, review_case: ReviewCase, clock: FrozenClock
) -> None:
    first = repository.register_case(review_case)

    clock.advance(3600)
    second = repository.register_case(review_case)

    assert second.version == first.version == 1
    assert second.created_at_utc == first.created_at_utc
    assert second.updated_at_utc == first.updated_at_utc
    assert second.status == first.status


def test_duplicate_registration_writes_nothing(
    repository: SqliteReviewCaseRepository,
    database: ReviewDatabase,
    review_case: ReviewCase,
    clock: FrozenClock,
) -> None:
    repository.register_case(review_case)
    before = (
        database.connect()
        .execute(
            "SELECT * FROM review_cases WHERE review_case_id = ?",
            (review_case.review_case_id,),
        )
        .fetchone()
    )

    clock.advance(86400)
    repository.register_case(review_case)

    after = (
        database.connect()
        .execute(
            "SELECT * FROM review_cases WHERE review_case_id = ?",
            (review_case.review_case_id,),
        )
        .fetchone()
    )
    assert dict(after) == dict(before)


def test_resolved_case_cannot_regress_to_pending_through_registration(
    repository: SqliteReviewCaseRepository,
    resolved_match_case: ReviewCase,
    review_case: ReviewCase,
    clock: FrozenClock,
) -> None:
    """The most dangerous persistence bug this sprint can produce.

    Case generation is deterministic and always emits the PENDING form. If
    re-running it overwrote storage, a human MATCH would silently disappear.
    """
    assert resolved_match_case.review_case_id == review_case.review_case_id
    assert review_case.status is ReviewStatus.PENDING

    stored = repository.register_case(resolved_match_case)
    clock.advance(7200)

    returned = repository.register_case(review_case)

    assert returned.status is ReviewStatus.MATCH
    assert returned.case.resolution == resolved_match_case.resolution
    assert returned.version == stored.version == 1
    assert returned.created_at_utc == stored.created_at_utc
    assert returned.updated_at_utc == stored.updated_at_utc

    reloaded = repository.get_case(review_case.review_case_id)
    assert reloaded.case == resolved_match_case
    assert reloaded.status is ReviewStatus.MATCH


def test_identity_mismatch_under_the_same_id_fails_closed(
    repository: SqliteReviewCaseRepository, review_case: ReviewCase
) -> None:
    repository.register_case(review_case)
    impostor = dataclasses.replace(
        review_case,
        pair=dataclasses.replace(review_case.pair, record_b_id="z-999"),
    )

    with pytest.raises(DuplicateCaseRegistrationError, match="contradicts"):
        repository.register_case(impostor)


def test_identity_mismatch_leaves_the_stored_case_untouched(
    repository: SqliteReviewCaseRepository, review_case: ReviewCase
) -> None:
    original = repository.register_case(review_case)
    impostor = dataclasses.replace(
        review_case,
        pair=dataclasses.replace(review_case.pair, record_b_id="z-999"),
    )

    with pytest.raises(DuplicateCaseRegistrationError):
        repository.register_case(impostor)

    assert repository.get_case(review_case.review_case_id) == original


def test_second_case_id_for_the_same_record_pair_fails_closed(
    repository: SqliteReviewCaseRepository, review_case: ReviewCase
) -> None:
    repository.register_case(review_case)
    renamed = dataclasses.replace(review_case, review_case_id="RC-0000000000000001")

    with pytest.raises(DuplicateCaseRegistrationError, match="already"):
        repository.register_case(renamed)


def test_registration_accepts_an_explicit_timestamp(
    repository: SqliteReviewCaseRepository, review_case: ReviewCase
) -> None:
    persisted = repository.register_case(review_case, now_utc="2026-01-01T00:00:00Z")
    assert persisted.created_at_utc == "2026-01-01T00:00:00Z"


# --------------------------------------------------------------------------
# Reads
# --------------------------------------------------------------------------


def test_get_missing_case_raises_not_found(repository: SqliteReviewCaseRepository) -> None:
    with pytest.raises(ReviewCaseNotFoundError, match="RC-absent"):
        repository.get_case("RC-absent")


def test_list_cases_is_empty_on_a_fresh_database(
    repository: SqliteReviewCaseRepository,
) -> None:
    assert repository.list_cases() == ()


def _register_chain(repository: SqliteReviewCaseRepository, clock: FrozenClock) -> list[str]:
    from entity_resolution.config import load_entity_resolution_config
    from human_review.cases import generate_review_cases
    from tests.human_review.conftest import make_chain_review_resolution

    state = generate_review_cases(
        make_chain_review_resolution(("a-1", "a-2", "a-3")),
        config=load_entity_resolution_config(),
    )
    assert len(state.cases) >= 2
    ids = []
    for case in state.cases:
        clock.advance(60)
        ids.append(repository.register_case(case).review_case_id)
    return ids


def test_list_cases_ordering_is_deterministic(
    repository: SqliteReviewCaseRepository, clock: FrozenClock
) -> None:
    _register_chain(repository, clock)

    first = [item.review_case_id for item in repository.list_cases()]
    second = [item.review_case_id for item in repository.list_cases()]

    assert first == second
    keys = [(item.created_at_utc, item.review_case_id) for item in repository.list_cases()]
    assert keys == sorted(keys)


def test_list_cases_ties_break_on_review_case_id(
    repository: SqliteReviewCaseRepository, clock: FrozenClock
) -> None:
    from entity_resolution.config import load_entity_resolution_config
    from human_review.cases import generate_review_cases
    from tests.human_review.conftest import make_chain_review_resolution

    # The clock never advances, so every row shares created_at_utc.
    state = generate_review_cases(
        make_chain_review_resolution(("a-1", "a-2", "a-3")),
        config=load_entity_resolution_config(),
    )
    for case in state.cases:
        repository.register_case(case)

    ids = [item.review_case_id for item in repository.list_cases()]
    assert ids == sorted(ids)


def test_status_filter_selects_only_that_status(
    repository: SqliteReviewCaseRepository,
    resolved_match_case: ReviewCase,
    clock: FrozenClock,
) -> None:
    repository.register_case(resolved_match_case)
    clock.advance(60)
    pending_ids = set(_register_chain(repository, clock))
    pending_ids.discard(resolved_match_case.review_case_id)

    matched = repository.list_cases(status=ReviewStatus.MATCH)
    pending = repository.list_cases(status=ReviewStatus.PENDING)

    assert [item.review_case_id for item in matched] == [resolved_match_case.review_case_id]
    assert all(item.status is ReviewStatus.PENDING for item in pending)
    assert {item.review_case_id for item in pending} == pending_ids


def test_status_filter_can_return_nothing(
    repository: SqliteReviewCaseRepository, review_case: ReviewCase
) -> None:
    repository.register_case(review_case)
    assert repository.list_cases(status=ReviewStatus.NO_MATCH) == ()


def test_list_cases_returns_persisted_cases(
    repository: SqliteReviewCaseRepository, review_case: ReviewCase
) -> None:
    repository.register_case(review_case)
    (only,) = repository.list_cases()

    assert isinstance(only, PersistedCase)
    assert only.version == 1
    assert only.case == review_case


# --------------------------------------------------------------------------
# Safety boundary
# --------------------------------------------------------------------------


def test_repository_exposes_no_decision_mutation_method() -> None:
    names = {
        name
        for name, _ in inspect.getmembers(SqliteReviewCaseRepository, inspect.isfunction)
        if not name.startswith("_")
    }
    assert not names & FORBIDDEN_REPOSITORY_METHODS
    # Phase E completed the surface. It is frozen here so a bypass cannot arrive
    # unnoticed. Neither writer is one: apply_resolution accepts only a
    # ReviewCase the domain already transitioned plus the event projecting its
    # audit entry, and record_semantic_suggestion cannot reach review_cases at
    # all.
    assert names == {
        "register_case",
        "register_workflow",
        "get_case",
        "list_cases",
        "load_workflow_bundle",
        "apply_resolution",
        "list_events",
        "record_semantic_suggestion",
        "list_semantic_suggestions",
        "workflow_context",
    }


def _public_protocol_methods() -> tuple[str, ...]:
    """The Protocol's own method names, read from the contract rather than retyped."""
    from review_application.repository import ReviewCaseRepository

    return tuple(
        name
        for name, member in vars(ReviewCaseRepository).items()
        if not name.startswith("_") and inspect.isfunction(member)
    )


def _sql_literals(path: Path) -> str:
    """Every string literal in a module except its docstrings.

    Scanning raw source would match the word UPDATE inside prose; comments are
    absent from the AST, and docstrings are filtered out here, so what remains
    is what can actually reach SQLite.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    documented = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
        and ast.get_docstring(node) is not None
    }
    return "\n".join(
        node.value.upper()
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in documented
    )


SQL_VERBS = ("SELECT", "INSERT", "UPDATE", "DELETE")


def _composed_sql_statements(module: object) -> dict[str, str]:
    """Every module-level SQL string the repository can hand to SQLite.

    Scanning source literals was accurate while all SQL was one f-string, but a
    statement assembled from several pieces reaches the AST as fragments, and an
    error message that says "conditional update" reads as an UPDATE. These
    constants are the composed text itself, so what is asserted below is what
    actually executes.
    """
    return {
        name: value.upper()
        for name, value in vars(module).items()
        if isinstance(value, str)
        and name.startswith("_")
        and not name.startswith("__")
        and any(verb in value.upper() for verb in SQL_VERBS)
    }


def test_repository_deletes_nothing_and_overwrites_nothing() -> None:
    # INSERT OR REPLACE / OR IGNORE are named explicitly because either would
    # turn registration into a silent overwrite of a resolved case. Nothing in
    # this repository ever deletes: a review queue holds human decisions.
    for name, statement in _composed_sql_statements(review_repository).items():
        assert "DELETE" not in statement, name
        assert "INSERT OR REPLACE" not in statement, name
        assert "INSERT OR IGNORE" not in statement, name


def test_the_only_update_is_the_versioned_case_compare_and_swap() -> None:
    """Phase D introduced exactly one UPDATE, and it is conditional.

    An unconditional UPDATE would let a reviewer who read a stale version
    overwrite a decision already made. Requiring ``WHERE ... AND version = ?``
    on the only UPDATE this module can emit is what makes losing that race
    impossible rather than unlikely.
    """
    updates = {
        name: statement
        for name, statement in _composed_sql_statements(review_repository).items()
        if "UPDATE " in statement
    }

    assert len(updates) == 1, sorted(updates)
    statement = next(iter(updates.values()))
    assert REVIEW_CASES_TABLE.upper() in statement
    assert "VERSION = VERSION + 1" in statement
    assert "AND VERSION = ?" in statement


def test_event_history_is_append_only_in_sql() -> None:
    # The append-only guarantee has to hold in the SQL itself, not only in the
    # method names: an UPDATE or DELETE against review_case_events would let a
    # recorded human decision be rewritten after the fact.
    touching_events = {
        name: statement
        for name, statement in _composed_sql_statements(review_repository).items()
        if REVIEW_CASE_EVENTS_TABLE.upper() in statement
    }

    assert touching_events, "Phase D must emit SQL against review_case_events."
    for name, statement in touching_events.items():
        assert "UPDATE " not in statement, name
        assert "DELETE" not in statement, name


def test_no_protocol_method_is_left_as_a_stub() -> None:
    """Every Protocol method is implemented for real after Phase E.

    Through the phases the rule was that a deferred method must be absent
    rather than stubbed, so calling code failed loudly instead of appearing to
    work while dropping what it was supposed to persist. Nothing is deferred
    now, so the rule becomes: nothing may be a placeholder either.
    """
    for name in _public_protocol_methods():
        implementation = getattr(SqliteReviewCaseRepository, name, None)
        assert implementation is not None, f"{name} is not implemented."
        source = inspect.getsource(implementation)
        assert "NotImplementedError" not in source, name
        assert "pass" not in source.split("\n")[-2:], name
