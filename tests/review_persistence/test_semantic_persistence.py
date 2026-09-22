"""Advisory suggestion storage that can never become a decision.

Sprint 09 suggestions are advice. Phase E gives them durability and nothing
else, so almost every test below is a statement about what did *not* happen:
the case did not move, the version did not change, no resolution appeared in
the audit trail, and the next reviewer sees exactly what they saw before.

Every suggestion here is produced by the offline Sprint 09 provider from a real
request. No LLM is called, no network is touched, and no dict is hand-built to
look like a suggestion.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace

import pytest

from entity_resolution.config import EntityResolutionConfig
from entity_resolution.models import RecordPair, ResolutionResult
from human_review.cases import generate_review_cases
from human_review.errors import ReviewCaseNotFoundError
from human_review.models import (
    HumanReviewDecision,
    ReviewCase,
    ReviewStatus,
    ReviewWorkflowState,
)
from human_review.reporting import resolution_snapshot
from review_application.errors import (
    ReviewSchemaVersionError,
    SemanticSuggestionConflictError,
    SemanticSuggestionIntegrityError,
)
from review_application.models import ReviewEventType
from review_application.service import ReviewQueueService
from review_persistence.config import ReviewPersistenceConfig
from review_persistence.schema import SEMANTIC_SUGGESTIONS_TABLE
from review_persistence.sqlite.database import ReviewDatabase, open_review_database
from review_persistence.sqlite.review_repository import SqliteReviewCaseRepository
from review_persistence.sqlite.semantic_mapper import (
    ABSENT_EXPLANATION,
    canonical_suggestion_json,
)
from semantic_review.models import (
    SemanticFailureCode,
    SemanticSuggestion,
    SemanticSuggestionType,
)
from tests.human_review.conftest import make_triangle_review_resolution
from tests.review_persistence.conftest import FrozenClock, bound_repository
from tests.review_persistence.semantic_fixtures import (
    as_provider_failure,
    make_suggestion,
    with_same_id_different_content,
)

CONFIG_PATH = "configs/entity_resolution.yaml"
RECORDED_AT = "2026-09-12T09:15:00Z"

# A deliberately recognisable, obviously fake marker. Provider explanations are
# free-form model prose written after the model has seen real record values, so
# this stands in for the customer data such prose could echo. Every assertion
# below that hunts for it is asking one question: did any of it reach the disk?
PII_MARKER = "Customer email alice@example.invalid matched"


def as_stored(suggestion: SemanticSuggestion) -> SemanticSuggestion:
    """The suggestion as persistence returns it.

    Identical to what was recorded except for ``explanation``, which this layer
    does not retain. Comparing against this rather than against the original is
    what keeps the tests honest about the durability boundary.
    """
    return replace(suggestion, explanation=ABSENT_EXPLANATION)


def raw_suggestion_rows(database: ReviewDatabase) -> str:
    """Every column of every stored suggestion row, as one searchable blob."""
    rows = database.connect().execute(f"SELECT * FROM {SEMANTIC_SUGGESTIONS_TABLE}").fetchall()
    return "\n".join(str(tuple(row)) for row in rows)


def raw_event_rows(database: ReviewDatabase) -> str:
    rows = database.connect().execute("SELECT * FROM review_case_events").fetchall()
    return "\n".join(str(tuple(row)) for row in rows)


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


@pytest.fixture
def records_by_id(resolution: ResolutionResult) -> dict:
    return {record.record_id: record for record in resolution.records}


@pytest.fixture
def registered_case(
    repository: SqliteReviewCaseRepository,
    review_state: ReviewWorkflowState,
    resolution: ResolutionResult,
) -> ReviewCase:
    repository.register_workflow(
        review_state,
        entity_records=resolution.records,
        resolution_snapshot=resolution_snapshot(resolution),
        entity_resolution_config_path=CONFIG_PATH,
    )
    return review_state.cases[0]


@pytest.fixture
def service(repository: SqliteReviewCaseRepository, clock: FrozenClock) -> ReviewQueueService:
    return ReviewQueueService(repository, clock=clock)


def case_snapshot(repository: SqliteReviewCaseRepository, review_case_id: str) -> tuple:
    """Everything advisory persistence must leave exactly as it found it."""
    persisted = repository.get_case(review_case_id)
    return (
        persisted.status,
        persisted.version,
        persisted.created_at_utc,
        persisted.updated_at_utc,
        persisted.case.resolution,
        persisted.case.to_dict(),
    )


def semantic_events(repository: SqliteReviewCaseRepository, review_case_id: str) -> tuple:
    return tuple(
        event
        for event in repository.list_events(review_case_id)
        if event.event_type is ReviewEventType.SEMANTIC_SUGGESTION_RECORDED
    )


# --------------------------------------------------------------------------
# A-D: every advisory value persists, and none of them decides anything
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        SemanticSuggestionType.SUGGEST_MATCH,
        SemanticSuggestionType.SUGGEST_NO_MATCH,
        SemanticSuggestionType.INSUFFICIENT_EVIDENCE,
    ],
)
def test_each_advisory_value_is_stored_without_touching_the_case(
    repository: SqliteReviewCaseRepository,
    registered_case: ReviewCase,
    records_by_id: dict,
    value: SemanticSuggestionType,
) -> None:
    before = case_snapshot(repository, registered_case.review_case_id)
    suggestion = make_suggestion(registered_case, records_by_id, suggestion=value)

    assert repository.record_semantic_suggestion(suggestion, now_utc=RECORDED_AT) is True

    (stored,) = repository.list_semantic_suggestions(registered_case.review_case_id)
    assert stored == as_stored(suggestion)
    assert stored.suggestion is value
    assert case_snapshot(repository, registered_case.review_case_id) == before
    assert before[0] is ReviewStatus.PENDING
    assert before[1] == 1


def test_a_provider_failure_is_stored_as_a_failure_not_a_deferral(
    repository: SqliteReviewCaseRepository,
    registered_case: ReviewCase,
    records_by_id: dict,
) -> None:
    """PROVIDER_FAILURE means the adviser could not answer, not that the case is closed.

    Turning it into DEFERRED would be persistence inventing a human decision out
    of an infrastructure problem -- the exact failure the advisory boundary
    exists to prevent.
    """
    before = case_snapshot(repository, registered_case.review_case_id)
    suggestion = as_provider_failure(
        make_suggestion(registered_case, records_by_id),
        failure_code=SemanticFailureCode.PROVIDER_TIMEOUT,
    )

    assert repository.record_semantic_suggestion(suggestion, now_utc=RECORDED_AT) is True

    (stored,) = repository.list_semantic_suggestions(registered_case.review_case_id)
    assert stored.suggestion is SemanticSuggestionType.PROVIDER_FAILURE
    assert stored.failure_code is SemanticFailureCode.PROVIDER_TIMEOUT
    assert stored == as_stored(suggestion)

    after = case_snapshot(repository, registered_case.review_case_id)
    assert after == before
    assert after[0] is ReviewStatus.PENDING
    assert repository.get_case(registered_case.review_case_id).case.resolution is None


def test_every_durable_sprint_09_field_round_trips(
    repository: SqliteReviewCaseRepository,
    registered_case: ReviewCase,
    records_by_id: dict,
) -> None:
    """The Sprint 09 payload is preserved exactly -- all of it except explanation."""
    suggestion = make_suggestion(registered_case, records_by_id)
    repository.record_semantic_suggestion(suggestion, now_utc=RECORDED_AT)

    (stored,) = repository.list_semantic_suggestions(registered_case.review_case_id)
    assert stored.to_dict() == suggestion.to_dict()
    assert stored.provider == suggestion.provider
    assert stored.requested_model == suggestion.requested_model
    assert stored.returned_model == suggestion.returned_model
    assert stored.live == suggestion.live
    assert stored.cost_mode == suggestion.cost_mode
    assert stored.reason_codes == suggestion.reason_codes
    assert stored.request_fingerprint == suggestion.request_fingerprint
    assert stored.attempt_count == suggestion.attempt_count
    assert stored.result_source == suggestion.result_source


def test_the_provider_explanation_never_reaches_the_database(
    repository: SqliteReviewCaseRepository,
    database: ReviewDatabase,
    registered_case: ReviewCase,
    records_by_id: dict,
) -> None:
    """Free-form model prose is not retained, and the disk proves it.

    The explanation is the one field the provider fills with unconstrained text,
    written after the model has read the untrusted record section, so it can
    quote customer values. Sprint 09 already excludes it from ``to_dict()`` and
    from its audit artifact; persistence keeps that boundary instead of widening
    what the platform durably retains from model output.
    """
    suggestion = replace(make_suggestion(registered_case, records_by_id), explanation=PII_MARKER)
    assert suggestion.explanation == PII_MARKER

    repository.record_semantic_suggestion(suggestion, now_utc=RECORDED_AT)

    stored_json = (
        database.connect()
        .execute(
            f"SELECT suggestion_payload_json FROM {SEMANTIC_SUGGESTIONS_TABLE} "
            "WHERE suggestion_id = ?",
            (suggestion.suggestion_id,),
        )
        .fetchone()["suggestion_payload_json"]
    )
    assert "explanation" not in json.loads(stored_json)
    assert PII_MARKER not in stored_json
    # Not merely absent from that column -- absent from the whole row, and from
    # the advisory event, which carries only the suggestion id.
    assert PII_MARKER not in raw_suggestion_rows(database)
    assert PII_MARKER not in raw_event_rows(database)
    assert "alice@example.invalid" not in raw_suggestion_rows(database)


def test_loading_a_suggestion_invents_no_explanation(
    repository: SqliteReviewCaseRepository,
    registered_case: ReviewCase,
    records_by_id: dict,
) -> None:
    """An empty explanation records that nothing was stored, not a lost value.

    Reconstructing prose that was never persisted would be worse than dropping
    it: a reader could not tell a recovered explanation from a manufactured one.
    """
    suggestion = replace(make_suggestion(registered_case, records_by_id), explanation=PII_MARKER)
    repository.record_semantic_suggestion(suggestion, now_utc=RECORDED_AT)

    (stored,) = repository.list_semantic_suggestions(registered_case.review_case_id)
    assert stored.explanation == ABSENT_EXPLANATION
    assert stored.explanation != PII_MARKER
    assert stored == as_stored(suggestion)


def test_dropping_the_explanation_does_not_disturb_identity_validation(
    repository: SqliteReviewCaseRepository,
    registered_case: ReviewCase,
    records_by_id: dict,
) -> None:
    """The suggestion id is unaffected, because it never hashed the explanation.

    Sprint 09 derives the id from request id, advisory value, attempt count and
    request fingerprint. A reloaded suggestion with an empty explanation is
    still its own content address, so it survives re-validation.
    """
    from semantic_review.ids import stable_suggestion_id

    suggestion = replace(make_suggestion(registered_case, records_by_id), explanation=PII_MARKER)
    repository.record_semantic_suggestion(suggestion, now_utc=RECORDED_AT)

    (stored,) = repository.list_semantic_suggestions(registered_case.review_case_id)
    assert stored.suggestion_id == suggestion.suggestion_id
    assert stored.suggestion_id == stable_suggestion_id(
        request_id=stored.request_id,
        suggestion=stored.suggestion.value,
        attempt_count=stored.attempt_count,
        fingerprint=stored.request_fingerprint,
    )


def test_the_explanation_has_no_bearing_on_human_review_authority(
    repository: SqliteReviewCaseRepository,
    service: ReviewQueueService,
    registered_case: ReviewCase,
    records_by_id: dict,
) -> None:
    """Nothing reads it, so dropping it cannot change any decision."""
    before = case_snapshot(repository, registered_case.review_case_id)
    repository.record_semantic_suggestion(
        replace(make_suggestion(registered_case, records_by_id), explanation=PII_MARKER),
        now_utc=RECORDED_AT,
    )
    assert case_snapshot(repository, registered_case.review_case_id) == before

    result = service.resolve_case(
        registered_case.review_case_id,
        decision=HumanReviewDecision.NO_MATCH,
        reviewer_id="reviewer-1",
        expected_version=1,
    )
    assert result.audit_entry.human_decision == "NO_MATCH"
    assert repository.get_case(registered_case.review_case_id).status is ReviewStatus.NO_MATCH


# --------------------------------------------------------------------------
# E-F: content-addressed idempotency, and the conflict it must not hide
# --------------------------------------------------------------------------


def test_an_identical_replay_is_an_idempotent_no_op(
    repository: SqliteReviewCaseRepository,
    registered_case: ReviewCase,
    records_by_id: dict,
) -> None:
    suggestion = make_suggestion(registered_case, records_by_id)
    assert repository.record_semantic_suggestion(suggestion, now_utc=RECORDED_AT) is True
    before = case_snapshot(repository, registered_case.review_case_id)

    assert (
        repository.record_semantic_suggestion(suggestion, now_utc="2026-09-13T10:00:00Z") is False
    )

    assert len(repository.list_semantic_suggestions(registered_case.review_case_id)) == 1
    assert len(semantic_events(repository, registered_case.review_case_id)) == 1
    assert case_snapshot(repository, registered_case.review_case_id) == before
    # The first observation stays authoritative for when it was recorded.
    (stored,) = repository.list_semantic_suggestions(registered_case.review_case_id)
    assert stored.created_at_utc == suggestion.created_at_utc


def test_the_same_id_with_different_content_fails_closed(
    repository: SqliteReviewCaseRepository,
    registered_case: ReviewCase,
    records_by_id: dict,
) -> None:
    """Two observations cannot share one content address.

    Persistence has no basis for choosing between them, and a stored suggestion
    is immutable, so the second write is refused rather than merged or
    overwritten.
    """
    original = make_suggestion(registered_case, records_by_id)
    repository.record_semantic_suggestion(original, now_utc=RECORDED_AT)
    impostor = with_same_id_different_content(original)
    assert impostor.suggestion_id == original.suggestion_id
    assert canonical_suggestion_json(impostor) != canonical_suggestion_json(original)

    with pytest.raises(SemanticSuggestionConflictError):
        repository.record_semantic_suggestion(impostor, now_utc=RECORDED_AT)

    (stored,) = repository.list_semantic_suggestions(registered_case.review_case_id)
    assert stored == as_stored(original)
    assert len(semantic_events(repository, registered_case.review_case_id)) == 1


def test_the_same_id_with_only_a_different_explanation_is_the_same_observation(
    repository: SqliteReviewCaseRepository,
    registered_case: ReviewCase,
    records_by_id: dict,
) -> None:
    """Explanation is not retained, so it cannot make two recordings differ.

    This is a statement about what persistence stores, not about the identifier.
    Sprint 09 does not hash the explanation into the suggestion id either, so
    these two really are one observation as far as anything durable is
    concerned -- and the first stored row stands.
    """
    original = replace(
        make_suggestion(registered_case, records_by_id), explanation="First wording."
    )
    reworded = replace(original, explanation=PII_MARKER)
    assert reworded.suggestion_id == original.suggestion_id
    assert canonical_suggestion_json(reworded) == canonical_suggestion_json(original)

    assert repository.record_semantic_suggestion(original, now_utc=RECORDED_AT) is True
    assert repository.record_semantic_suggestion(reworded, now_utc=RECORDED_AT) is False

    stored = repository.list_semantic_suggestions(registered_case.review_case_id)
    assert len(stored) == 1
    assert len(semantic_events(repository, registered_case.review_case_id)) == 1
    assert stored[0] == as_stored(original)


def test_a_conflict_is_not_reported_as_a_storage_failure(
    repository: SqliteReviewCaseRepository,
    registered_case: ReviewCase,
    records_by_id: dict,
) -> None:
    from review_application.errors import ReviewConflictError, ReviewPersistenceError

    original = make_suggestion(registered_case, records_by_id)
    repository.record_semantic_suggestion(original, now_utc=RECORDED_AT)

    with pytest.raises(SemanticSuggestionConflictError) as raised:
        repository.record_semantic_suggestion(
            with_same_id_different_content(original), now_utc=RECORDED_AT
        )
    # A caller must be able to tell an advisory contradiction from a lost
    # version race and from infrastructure failing.
    assert not isinstance(raised.value, ReviewPersistenceError)
    assert not isinstance(raised.value, ReviewConflictError)


# --------------------------------------------------------------------------
# G-K: identity validation
# --------------------------------------------------------------------------


def test_a_suggestion_naming_another_case_is_refused(
    repository: SqliteReviewCaseRepository,
    registered_case: ReviewCase,
    records_by_id: dict,
) -> None:
    suggestion = make_suggestion(registered_case, records_by_id)
    # The content address survives this edit, which is exactly why the case
    # identity has to be checked separately.
    impostor = replace(suggestion, review_case_id="RC-somewhere-else")

    with pytest.raises(ReviewCaseNotFoundError):
        repository.record_semantic_suggestion(impostor, now_utc=RECORDED_AT)
    assert repository.list_semantic_suggestions(registered_case.review_case_id) == ()


def test_a_suggestion_for_a_case_that_does_not_exist_is_refused(
    repository: SqliteReviewCaseRepository,
    review_state: ReviewWorkflowState,
    records_by_id: dict,
) -> None:
    # No register_workflow in this test: the queue is empty.
    suggestion = make_suggestion(review_state.cases[0], records_by_id)

    with pytest.raises(ReviewCaseNotFoundError):
        repository.record_semantic_suggestion(suggestion, now_utc=RECORDED_AT)


@pytest.mark.parametrize("field", ["record_a_id", "record_b_id"])
def test_a_suggestion_describing_another_record_is_refused(
    repository: SqliteReviewCaseRepository,
    registered_case: ReviewCase,
    records_by_id: dict,
    field: str,
) -> None:
    suggestion = make_suggestion(registered_case, records_by_id)
    impostor = replace(suggestion, **{field: "rec-not-in-this-case"})

    with pytest.raises(SemanticSuggestionIntegrityError, match="describes records"):
        repository.record_semantic_suggestion(impostor, now_utc=RECORDED_AT)
    assert repository.list_semantic_suggestions(registered_case.review_case_id) == ()


def test_a_reversed_record_pair_is_refused(
    repository: SqliteReviewCaseRepository,
    registered_case: ReviewCase,
    records_by_id: dict,
) -> None:
    """RecordPair is ordered, so the reversed pair is a different comparison."""
    suggestion = make_suggestion(registered_case, records_by_id)
    reversed_pair = replace(
        suggestion,
        record_a_id=suggestion.record_b_id,
        record_b_id=suggestion.record_a_id,
    )

    with pytest.raises(SemanticSuggestionIntegrityError, match="Record order"):
        repository.record_semantic_suggestion(reversed_pair, now_utc=RECORDED_AT)
    assert repository.list_semantic_suggestions(registered_case.review_case_id) == ()


def test_an_id_that_is_not_its_own_content_address_is_refused(
    repository: SqliteReviewCaseRepository,
    registered_case: ReviewCase,
    records_by_id: dict,
) -> None:
    """Sprint 09 ids are derived from content, so a hand-set id is detectable."""
    suggestion = make_suggestion(registered_case, records_by_id)
    forged = replace(suggestion, suggestion_id="LS-0000000000000000")

    with pytest.raises(SemanticSuggestionIntegrityError, match="content address"):
        repository.record_semantic_suggestion(forged, now_utc=RECORDED_AT)


def test_a_non_sprint_09_object_is_refused(
    repository: SqliteReviewCaseRepository,
    registered_case: ReviewCase,
) -> None:
    with pytest.raises(SemanticSuggestionIntegrityError, match="SemanticSuggestion"):
        repository.record_semantic_suggestion(
            {"suggestion_id": "LS-1234567890abcdef", "suggestion": "MATCH"},  # type: ignore[arg-type]
            now_utc=RECORDED_AT,
        )


def test_a_human_decision_token_can_never_be_a_suggestion_value(
    registered_case: ReviewCase,
    records_by_id: dict,
) -> None:
    """The enum makes this unrepresentable; the boundary is asserted anyway."""
    from semantic_review.models import FORBIDDEN_HUMAN_DECISIONS

    values = {item.value for item in SemanticSuggestionType}
    assert values.isdisjoint(FORBIDDEN_HUMAN_DECISIONS)
    with pytest.raises(ValueError):
        SemanticSuggestionType("MATCH")


# --------------------------------------------------------------------------
# L-N: the semantic event, and atomicity
# --------------------------------------------------------------------------


def test_a_new_suggestion_appends_exactly_one_non_resolution_event(
    repository: SqliteReviewCaseRepository,
    registered_case: ReviewCase,
    records_by_id: dict,
) -> None:
    suggestion = make_suggestion(registered_case, records_by_id)
    repository.record_semantic_suggestion(suggestion, now_utc=RECORDED_AT)

    (event,) = repository.list_events(registered_case.review_case_id)
    assert event.event_type is ReviewEventType.SEMANTIC_SUGGESTION_RECORDED
    assert event.is_resolution is False
    assert event.resolution_sequence is None
    assert event.reviewer_id is None
    assert event.audit_entry_payload is None
    assert event.suggestion_id == suggestion.suggestion_id
    assert event.occurred_at_utc == RECORDED_AT


def test_a_replay_appends_no_second_event(
    repository: SqliteReviewCaseRepository,
    registered_case: ReviewCase,
    records_by_id: dict,
) -> None:
    suggestion = make_suggestion(registered_case, records_by_id)
    repository.record_semantic_suggestion(suggestion, now_utc=RECORDED_AT)
    repository.record_semantic_suggestion(suggestion, now_utc=RECORDED_AT)
    repository.record_semantic_suggestion(suggestion, now_utc=RECORDED_AT)

    assert len(repository.list_events(registered_case.review_case_id)) == 1


def test_a_failure_after_the_suggestion_insert_rolls_both_back(
    repository: SqliteReviewCaseRepository,
    registered_case: ReviewCase,
    records_by_id: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The row is written when the event append fails.

    If the two were not one transaction the store would hold a suggestion with
    no record of when it was observed. The seam is the private append method;
    no production flag exists for this.
    """
    suggestion = make_suggestion(registered_case, records_by_id)

    def explode(connection: object, event: object) -> None:
        raise RuntimeError("event append failed")

    monkeypatch.setattr(SqliteReviewCaseRepository, "_append_event", staticmethod(explode))

    with pytest.raises(RuntimeError, match="event append failed"):
        repository.record_semantic_suggestion(suggestion, now_utc=RECORDED_AT)

    monkeypatch.undo()
    assert repository.list_semantic_suggestions(registered_case.review_case_id) == ()
    assert repository.list_events(registered_case.review_case_id) == ()


def test_the_rollback_leaves_nothing_in_the_reopened_database(
    repository: SqliteReviewCaseRepository,
    database: ReviewDatabase,
    registered_case: ReviewCase,
    records_by_id: dict,
    clock: FrozenClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    suggestion = make_suggestion(registered_case, records_by_id)

    def explode(connection: object, event: object) -> None:
        raise RuntimeError("event append failed")

    monkeypatch.setattr(SqliteReviewCaseRepository, "_append_event", staticmethod(explode))
    with pytest.raises(RuntimeError):
        repository.record_semantic_suggestion(suggestion, now_utc=RECORDED_AT)
    monkeypatch.undo()

    database.close()
    database.initialize()
    reopened = bound_repository(database, clock)
    assert reopened.list_semantic_suggestions(registered_case.review_case_id) == ()
    assert reopened.list_events(registered_case.review_case_id) == ()
    assert reopened.get_case(registered_case.review_case_id).status is ReviewStatus.PENDING


# --------------------------------------------------------------------------
# Q: several advisory observations may coexist
# --------------------------------------------------------------------------


def test_distinct_suggestions_for_one_case_all_survive(
    repository: SqliteReviewCaseRepository,
    registered_case: ReviewCase,
    records_by_id: dict,
) -> None:
    """Advisory observations accumulate; none of them supersedes another.

    There is no "current semantic decision". The case status is the only
    statement about what was decided, and it is untouched by all of this.
    """
    before = case_snapshot(repository, registered_case.review_case_id)
    match = make_suggestion(
        registered_case, records_by_id, suggestion=SemanticSuggestionType.SUGGEST_MATCH
    )
    insufficient = make_suggestion(
        registered_case, records_by_id, suggestion=SemanticSuggestionType.INSUFFICIENT_EVIDENCE
    )
    assert match.suggestion_id != insufficient.suggestion_id

    assert repository.record_semantic_suggestion(match, now_utc=RECORDED_AT) is True
    assert repository.record_semantic_suggestion(insufficient, now_utc=RECORDED_AT) is True

    stored = repository.list_semantic_suggestions(registered_case.review_case_id)
    assert len(stored) == 2
    assert {item.suggestion for item in stored} == {
        SemanticSuggestionType.SUGGEST_MATCH,
        SemanticSuggestionType.INSUFFICIENT_EVIDENCE,
    }
    assert len(semantic_events(repository, registered_case.review_case_id)) == 2
    assert case_snapshot(repository, registered_case.review_case_id) == before


def test_suggestions_are_returned_in_a_deterministic_order(
    repository: SqliteReviewCaseRepository,
    registered_case: ReviewCase,
    records_by_id: dict,
) -> None:
    suggestions = [
        make_suggestion(registered_case, records_by_id, suggestion=value)
        for value in (
            SemanticSuggestionType.SUGGEST_MATCH,
            SemanticSuggestionType.SUGGEST_NO_MATCH,
            SemanticSuggestionType.INSUFFICIENT_EVIDENCE,
        )
    ]
    for item in reversed(suggestions):
        repository.record_semantic_suggestion(item, now_utc=RECORDED_AT)

    stored = repository.list_semantic_suggestions(registered_case.review_case_id)

    # Same created_at_utc, so the content address breaks the tie -- a total
    # order that does not depend on insertion order.
    assert [item.suggestion_id for item in stored] == sorted(
        item.suggestion_id for item in suggestions
    )
    assert repository.list_semantic_suggestions("RC-unknown") == ()


def test_suggestions_are_scoped_to_their_own_case(
    repository: SqliteReviewCaseRepository,
    resolution_config: EntityResolutionConfig,
) -> None:
    resolution = make_triangle_review_resolution(("rec-a", "rec-b", "rec-c"))
    state = generate_review_cases(resolution, config=resolution_config)
    repository.register_workflow(
        state,
        entity_records=resolution.records,
        resolution_snapshot=resolution_snapshot(resolution),
        entity_resolution_config_path=CONFIG_PATH,
    )
    records = {record.record_id: record for record in resolution.records}
    first, second = state.cases[0], state.cases[1]

    repository.record_semantic_suggestion(make_suggestion(first, records), now_utc=RECORDED_AT)

    assert len(repository.list_semantic_suggestions(first.review_case_id)) == 1
    assert repository.list_semantic_suggestions(second.review_case_id) == ()


# --------------------------------------------------------------------------
# R-S: the reviewer is not bound by advice
# --------------------------------------------------------------------------


def test_a_human_no_match_overrides_an_advisory_suggest_match(
    repository: SqliteReviewCaseRepository,
    service: ReviewQueueService,
    registered_case: ReviewCase,
    records_by_id: dict,
) -> None:
    """The mandatory advisory-isolation proof, in the direction that matters most.

    The adviser said these records match. The reviewer says they do not. The
    reviewer wins, and the suggestion is not consulted at any point.
    """
    suggestion = make_suggestion(
        registered_case, records_by_id, suggestion=SemanticSuggestionType.SUGGEST_MATCH
    )
    repository.record_semantic_suggestion(suggestion, now_utc=RECORDED_AT)

    persisted = repository.get_case(registered_case.review_case_id)
    assert persisted.status is ReviewStatus.PENDING
    assert persisted.version == 1

    result = service.resolve_case(
        registered_case.review_case_id,
        decision=HumanReviewDecision.NO_MATCH,
        reviewer_id="reviewer-1",
        expected_version=1,
    )

    final = repository.get_case(registered_case.review_case_id)
    assert final.status is ReviewStatus.NO_MATCH
    assert final.version == 2

    events = repository.list_events(registered_case.review_case_id)
    assert [event.event_type for event in events] == [
        ReviewEventType.SEMANTIC_SUGGESTION_RECORDED,
        ReviewEventType.NO_MATCH,
    ]
    # The workflow's audit trail contains the human decision and nothing else.
    assert len(result.workflow_state.audit_trail) == 1
    assert result.workflow_state.audit_trail[0].human_decision == "NO_MATCH"
    bundle = repository.load_workflow_bundle()
    assert len(bundle.audit_entries) == 1
    assert bundle.next_resolution_sequence == 2


def test_a_human_match_ignores_an_advisory_suggest_no_match(
    repository: SqliteReviewCaseRepository,
    service: ReviewQueueService,
    registered_case: ReviewCase,
    records_by_id: dict,
) -> None:
    """The mirror proof: advice against a MATCH is not a constraint.

    A human NO_MATCH *is* a constraint -- Sprint 08 propagates it across the
    component. SUGGEST_NO_MATCH is not, and the deterministic authorization
    that allows this MATCH must reach the same verdict with the suggestion
    stored as without it.
    """
    suggestion = make_suggestion(
        registered_case, records_by_id, suggestion=SemanticSuggestionType.SUGGEST_NO_MATCH
    )
    repository.record_semantic_suggestion(suggestion, now_utc=RECORDED_AT)

    result = service.resolve_case(
        registered_case.review_case_id,
        decision=HumanReviewDecision.MATCH,
        reviewer_id="reviewer-1",
        expected_version=1,
    )

    final = repository.get_case(registered_case.review_case_id)
    assert final.status is ReviewStatus.MATCH
    assert final.version == 2
    assert result.audit_entry.human_decision == "MATCH"
    # The advisory row is still there, still advisory.
    (stored,) = repository.list_semantic_suggestions(registered_case.review_case_id)
    assert stored.suggestion is SemanticSuggestionType.SUGGEST_NO_MATCH


def test_a_suggestion_recorded_after_a_human_decision_changes_nothing(
    repository: SqliteReviewCaseRepository,
    service: ReviewQueueService,
    registered_case: ReviewCase,
    records_by_id: dict,
) -> None:
    """Late persistence is allowed, and it changes nothing.

    What is proven here is the guarantee persistence can actually make: a
    suggestion whose identity matches the case may be recorded after that case
    was resolved, and doing so never reopens or mutates it. Persistence does not
    infer when the suggestion was generated -- it validates identity, and
    identity says nothing about provenance.
    """
    suggestion = make_suggestion(registered_case, records_by_id)
    service.resolve_case(
        registered_case.review_case_id,
        decision=HumanReviewDecision.NO_MATCH,
        reviewer_id="reviewer-1",
        expected_version=1,
    )
    before = case_snapshot(repository, registered_case.review_case_id)

    assert repository.record_semantic_suggestion(suggestion, now_utc=RECORDED_AT) is True

    after = case_snapshot(repository, registered_case.review_case_id)
    assert after == before
    assert after[0] is ReviewStatus.NO_MATCH
    assert after[1] == 2
    bundle = repository.load_workflow_bundle()
    assert len(bundle.audit_entries) == 1
    assert bundle.next_resolution_sequence == 2


# --------------------------------------------------------------------------
# T-U: history reconciliation is unaffected
# --------------------------------------------------------------------------


def test_semantic_events_do_not_advance_the_resolution_sequence(
    repository: SqliteReviewCaseRepository,
    registered_case: ReviewCase,
    records_by_id: dict,
) -> None:
    before = repository.load_workflow_bundle()
    for value in (
        SemanticSuggestionType.SUGGEST_MATCH,
        SemanticSuggestionType.SUGGEST_NO_MATCH,
        SemanticSuggestionType.INSUFFICIENT_EVIDENCE,
    ):
        repository.record_semantic_suggestion(
            make_suggestion(registered_case, records_by_id, suggestion=value),
            now_utc=RECORDED_AT,
        )

    after = repository.load_workflow_bundle()
    assert after.next_resolution_sequence == before.next_resolution_sequence == 1
    assert after.audit_entries == before.audit_entries == ()


def test_mixed_events_keep_their_append_order_and_their_kinds(
    repository: SqliteReviewCaseRepository,
    service: ReviewQueueService,
    registered_case: ReviewCase,
    records_by_id: dict,
) -> None:
    repository.record_semantic_suggestion(
        make_suggestion(registered_case, records_by_id), now_utc=RECORDED_AT
    )
    service.resolve_case(
        registered_case.review_case_id,
        decision=HumanReviewDecision.DEFER,
        reviewer_id="reviewer-1",
        expected_version=1,
    )
    repository.record_semantic_suggestion(
        make_suggestion(
            registered_case,
            records_by_id,
            suggestion=SemanticSuggestionType.INSUFFICIENT_EVIDENCE,
        ),
        now_utc="2026-09-12T11:00:00Z",
    )

    events = repository.list_events(registered_case.review_case_id)
    assert [event.event_type for event in events] == [
        ReviewEventType.SEMANTIC_SUGGESTION_RECORDED,
        ReviewEventType.DEFERRED,
        ReviewEventType.SEMANTIC_SUGGESTION_RECORDED,
    ]
    assert [event.is_resolution for event in events] == [False, True, False]
    # Only the resolution event carries an ordinal.
    assert [event.resolution_sequence for event in events] == [None, 1, None]

    bundle = repository.load_workflow_bundle()
    assert len(bundle.audit_entries) == 1
    assert bundle.next_resolution_sequence == 2


# --------------------------------------------------------------------------
# V-W: corrupted storage fails closed
# --------------------------------------------------------------------------


def test_a_row_edited_out_from_under_the_repository_fails_closed(
    repository: SqliteReviewCaseRepository,
    database: ReviewDatabase,
    registered_case: ReviewCase,
    records_by_id: dict,
) -> None:
    """Denormalized columns and the payload are written together, so they must agree."""
    suggestion = make_suggestion(registered_case, records_by_id)
    repository.record_semantic_suggestion(suggestion, now_utc=RECORDED_AT)

    database.connect().execute(
        f"UPDATE {SEMANTIC_SUGGESTIONS_TABLE} SET suggestion = ? WHERE suggestion_id = ?",
        (SemanticSuggestionType.SUGGEST_NO_MATCH.value, suggestion.suggestion_id),
    )

    with pytest.raises(SemanticSuggestionIntegrityError, match="disagrees with its payload"):
        repository.list_semantic_suggestions(registered_case.review_case_id)


def test_a_payload_missing_a_sprint_09_field_fails_closed(
    repository: SqliteReviewCaseRepository,
    database: ReviewDatabase,
    registered_case: ReviewCase,
    records_by_id: dict,
) -> None:
    """A silently defaulted field would be a suggestion nobody made."""
    suggestion = make_suggestion(registered_case, records_by_id)
    repository.record_semantic_suggestion(suggestion, now_utc=RECORDED_AT)

    payload = json.loads(canonical_suggestion_json(suggestion))
    # A retained field: explanation is not in the payload to begin with.
    payload.pop("provider")
    database.connect().execute(
        f"UPDATE {SEMANTIC_SUGGESTIONS_TABLE} SET suggestion_payload_json = ? "
        "WHERE suggestion_id = ?",
        (json.dumps(payload), suggestion.suggestion_id),
    )

    with pytest.raises(SemanticSuggestionIntegrityError, match="missing="):
        repository.list_semantic_suggestions(registered_case.review_case_id)


def test_a_row_written_by_an_unsupported_build_fails_closed(
    repository: SqliteReviewCaseRepository,
    database: ReviewDatabase,
    registered_case: ReviewCase,
    records_by_id: dict,
) -> None:
    """The file-level version check guards the database; this guards the row.

    A row inserted out of band by a future build declares a schema this one does
    not understand, and reading it as if it did is how a silent data-format
    change becomes a silent behaviour change.
    """
    suggestion = make_suggestion(registered_case, records_by_id)
    repository.record_semantic_suggestion(suggestion, now_utc=RECORDED_AT)

    database.connect().execute(
        f"UPDATE {SEMANTIC_SUGGESTIONS_TABLE} SET schema_version = '9.9.9' WHERE suggestion_id = ?",
        (suggestion.suggestion_id,),
    )

    with pytest.raises(ReviewSchemaVersionError):
        repository.list_semantic_suggestions(registered_case.review_case_id)


def test_the_schema_refuses_a_suggestion_row_for_an_unknown_case(
    database: ReviewDatabase,
    registered_case: ReviewCase,
) -> None:
    """The foreign key makes an orphan advisory row unrepresentable."""
    assert database.foreign_keys_enabled()
    with pytest.raises(sqlite3.IntegrityError):
        database.connect().execute(
            f"INSERT INTO {SEMANTIC_SUGGESTIONS_TABLE} "
            "(suggestion_id, review_case_id, record_a_id, record_b_id, suggestion, "
            "failure_code, live, provider, requested_model, suggestion_payload_json, "
            "created_at_utc, schema_version) "
            "VALUES ('LS-ghost', 'RC-ghost', 'a', 'b', 'SUGGEST_MATCH', NULL, 0, 'fake', "
            "'m', '{}', '2026-09-12T09:00:00Z', '1.0.0')"
        )


def test_the_schema_refuses_a_human_decision_as_a_suggestion_value(
    database: ReviewDatabase,
    registered_case: ReviewCase,
) -> None:
    """Belt and braces beneath the enum: the CHECK constraint says the same thing."""
    with pytest.raises(sqlite3.IntegrityError):
        database.connect().execute(
            f"INSERT INTO {SEMANTIC_SUGGESTIONS_TABLE} "
            "(suggestion_id, review_case_id, record_a_id, record_b_id, suggestion, "
            "failure_code, live, provider, requested_model, suggestion_payload_json, "
            "created_at_utc, schema_version) "
            "VALUES ('LS-bad', ?, ?, ?, 'MATCH', NULL, 0, 'fake', 'm', '{}', "
            "'2026-09-12T09:00:00Z', '1.0.0')",
            (
                registered_case.review_case_id,
                registered_case.pair.record_a_id,
                registered_case.pair.record_b_id,
            ),
        )


# --------------------------------------------------------------------------
# P: restart durability
# --------------------------------------------------------------------------


def test_everything_advisory_survives_a_restart(
    persistence_config: ReviewPersistenceConfig,
    resolution_config: EntityResolutionConfig,
    resolution: ResolutionResult,
    review_state: ReviewWorkflowState,
    clock: FrozenClock,
) -> None:
    """A real file database, closed and reopened.

    The suggestion has to come back whole -- provider, model, cost mode,
    failure metadata -- and the Human Review side has to come back
    exactly as it was left, which for a case that was only ever advised on means
    completely untouched.
    """
    records = {record.record_id: record for record in resolution.records}
    case = review_state.cases[0]

    database = open_review_database(persistence_config, clock=clock)
    repository = bound_repository(database, clock)
    repository.register_workflow(
        review_state,
        entity_records=resolution.records,
        resolution_snapshot=resolution_snapshot(resolution),
        entity_resolution_config_path=CONFIG_PATH,
    )
    advisory = make_suggestion(case, records)
    failure = as_provider_failure(
        make_suggestion(case, records, suggestion=SemanticSuggestionType.INSUFFICIENT_EVIDENCE),
        failure_code=SemanticFailureCode.PROVIDER_RATE_LIMIT,
    )
    repository.record_semantic_suggestion(advisory, now_utc=RECORDED_AT)
    repository.record_semantic_suggestion(failure, now_utc=RECORDED_AT)
    before = case_snapshot(repository, case.review_case_id)
    database.close()

    reopened = open_review_database(persistence_config, clock=clock)
    try:
        fresh = bound_repository(reopened, clock)

        stored = fresh.list_semantic_suggestions(case.review_case_id)
        assert set(stored) == {as_stored(advisory), as_stored(failure)}
        by_id = {item.suggestion_id: item for item in stored}
        assert by_id[failure.suggestion_id].failure_code is SemanticFailureCode.PROVIDER_RATE_LIMIT
        assert by_id[advisory.suggestion_id].provider == advisory.provider
        assert by_id[advisory.suggestion_id].requested_model == advisory.requested_model
        assert by_id[advisory.suggestion_id].live is advisory.live
        assert by_id[advisory.suggestion_id].explanation == ABSENT_EXPLANATION

        assert len(semantic_events(fresh, case.review_case_id)) == 2
        assert case_snapshot(fresh, case.review_case_id) == before

        bundle = fresh.load_workflow_bundle()
        assert bundle.next_resolution_sequence == 1
        assert bundle.audit_entries == ()
        assert bundle.entity_resolution_config_path == CONFIG_PATH
        assert bundle.resolution_snapshot == resolution_snapshot(resolution)

        # Replay after restart is still recognised as the same observation.
        assert fresh.record_semantic_suggestion(advisory, now_utc=RECORDED_AT) is False
        assert len(fresh.list_semantic_suggestions(case.review_case_id)) == 2
    finally:
        reopened.close()


def test_the_protocol_surface_is_now_complete(
    repository: SqliteReviewCaseRepository,
) -> None:
    from review_application.repository import ReviewCaseRepository

    conforming: ReviewCaseRepository = repository
    assert conforming is repository
    for name in (
        "register_workflow",
        "get_case",
        "list_cases",
        "count_cases",
        "load_workflow_bundle",
        "apply_resolution",
        "list_events",
        "record_semantic_suggestion",
        "list_semantic_suggestions",
    ):
        assert callable(getattr(repository, name)), name


def test_a_suggestion_is_never_a_review_case_mutation(
    repository: SqliteReviewCaseRepository,
    registered_case: ReviewCase,
    records_by_id: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No advisory write may reach the case compare-and-swap.

    The strongest form of the invariant: the only statement that can move a case
    is replaced with one that raises, and recording a suggestion still succeeds.
    """
    from review_persistence.sqlite import review_repository as module

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Semantic persistence must never update review_cases.")

    monkeypatch.setattr(module.SqliteReviewCaseRepository, "_compare_and_swap_case", forbidden)

    assert (
        repository.record_semantic_suggestion(
            make_suggestion(registered_case, records_by_id), now_utc=RECORDED_AT
        )
        is True
    )
    assert repository.get_case(registered_case.review_case_id).version == 1


def test_suggestions_are_not_reported_as_review_case_versions(
    repository: SqliteReviewCaseRepository,
    registered_case: ReviewCase,
    records_by_id: dict,
) -> None:
    stored: list[SemanticSuggestion] = []
    for value in (
        SemanticSuggestionType.SUGGEST_MATCH,
        SemanticSuggestionType.SUGGEST_NO_MATCH,
    ):
        suggestion = make_suggestion(registered_case, records_by_id, suggestion=value)
        repository.record_semantic_suggestion(suggestion, now_utc=RECORDED_AT)
        stored.append(suggestion)

    persisted = repository.get_case(registered_case.review_case_id)
    assert persisted.version == 1
    assert persisted.status is ReviewStatus.PENDING
    assert len(stored) == 2
    assert (
        RecordPair.ordered(registered_case.pair.record_a_id, registered_case.pair.record_b_id)
        == registered_case.pair
    )
