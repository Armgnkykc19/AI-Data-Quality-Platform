from __future__ import annotations

import dataclasses

import pytest

from human_review.models import (
    HumanReviewDecision,
    ReviewAuditEntry,
    ReviewCase,
    ReviewStatus,
)
from review_application.errors import (
    PersistedCaseIntegrityError,
    ReviewEventIntegrityError,
)
from review_application.models import (
    INITIAL_CASE_VERSION,
    PersistedCase,
    ReviewEvent,
    ReviewEventType,
    WorkflowBundle,
)
from tests.review_application.conftest import FROZEN_NOW

SCHEMA_VERSION = "1.0.0"

# Sprint 08 contract. ReviewCase feeds human_review_report.json at schema
# version 1.0.0; this list must not grow because Sprint 10 needed a field.
SPRINT_08_REVIEW_CASE_FIELDS = (
    "review_case_id",
    "pair",
    "record_ids",
    "machine_decision",
    "machine_score",
    "auto_match_threshold",
    "review_threshold",
    "machine_reason",
    "blocking_reasons",
    "supporting_evidence",
    "conflicting_evidence",
    "missing_evidence_notes",
    "machine_readable_reasons",
    "human_summary",
    "status",
    "resolution",
)

SPRINT_08_REVIEW_CASE_PAYLOAD_KEYS = {
    "review_case_id",
    "record_a_id",
    "record_b_id",
    "record_ids",
    "machine_decision",
    "machine_score",
    "auto_match_threshold",
    "review_threshold",
    "machine_reason",
    "status",
    "blocking_reasons",
    "supporting_evidence",
    "conflicting_evidence",
    "missing_evidence_notes",
    "machine_readable_reasons",
    "human_summary",
    "resolution",
}


def _audit_entry(case: ReviewCase, decision: HumanReviewDecision, sequence: int = 1):
    return ReviewAuditEntry(
        review_case_id=case.review_case_id,
        record_a_id=case.pair.record_a_id,
        record_b_id=case.pair.record_b_id,
        machine_decision=case.machine_decision.value,
        machine_reason=case.machine_reason,
        human_decision=decision.value,
        reviewer_id="reviewer-1",
        resolution_sequence=sequence,
        downstream_action="test_only_downstream_action",
    )


def test_review_case_dataclass_fields_unchanged_by_sprint_10() -> None:
    field_names = tuple(field.name for field in dataclasses.fields(ReviewCase))
    assert field_names == SPRINT_08_REVIEW_CASE_FIELDS


def test_review_case_to_dict_payload_keys_unchanged(review_case: ReviewCase) -> None:
    assert set(review_case.to_dict()) == SPRINT_08_REVIEW_CASE_PAYLOAD_KEYS


def test_persisted_case_composes_without_mutating_review_case(review_case: ReviewCase) -> None:
    payload_before = review_case.to_dict()
    persisted = PersistedCase.initial(review_case, now_utc=FROZEN_NOW)

    assert persisted.case is review_case
    assert persisted.review_case_id == review_case.review_case_id
    assert persisted.status == review_case.status
    assert persisted.version == INITIAL_CASE_VERSION
    assert review_case.to_dict() == payload_before


def test_persisted_case_rejects_version_below_one(review_case: ReviewCase) -> None:
    with pytest.raises(PersistedCaseIntegrityError, match=">= 1"):
        PersistedCase(
            case=review_case,
            version=0,
            created_at_utc=FROZEN_NOW,
            updated_at_utc=FROZEN_NOW,
        )


@pytest.mark.parametrize("blank", ["", "   "])
def test_persisted_case_rejects_blank_timestamps(review_case: ReviewCase, blank: str) -> None:
    with pytest.raises(PersistedCaseIntegrityError):
        PersistedCase(
            case=review_case,
            version=1,
            created_at_utc=blank,
            updated_at_utc=FROZEN_NOW,
        )


def test_with_case_bumps_version_and_preserves_created_at(review_case: ReviewCase) -> None:
    persisted = PersistedCase.initial(review_case, now_utc=FROZEN_NOW)
    resolved = dataclasses.replace(review_case, status=ReviewStatus.NO_MATCH)

    updated = persisted.with_case(resolved, now_utc="2026-09-12T09:00:00Z")

    assert updated.version == persisted.version + 1
    assert updated.created_at_utc == FROZEN_NOW
    assert updated.updated_at_utc == "2026-09-12T09:00:00Z"
    assert updated.status == ReviewStatus.NO_MATCH


def test_with_case_rejects_identity_change(review_case: ReviewCase) -> None:
    persisted = PersistedCase.initial(review_case, now_utc=FROZEN_NOW)
    foreign = dataclasses.replace(review_case, review_case_id="RC-0000000000000000")

    with pytest.raises(PersistedCaseIntegrityError, match="immutable"):
        persisted.with_case(foreign, now_utc=FROZEN_NOW)


@pytest.mark.parametrize(
    ("decision", "expected_type"),
    [
        (HumanReviewDecision.MATCH, ReviewEventType.MATCH),
        (HumanReviewDecision.NO_MATCH, ReviewEventType.NO_MATCH),
        (HumanReviewDecision.DEFER, ReviewEventType.DEFERRED),
    ],
)
def test_resolution_event_projects_domain_audit_entry(
    review_case: ReviewCase,
    decision: HumanReviewDecision,
    expected_type: ReviewEventType,
) -> None:
    entry = _audit_entry(review_case, decision)

    event = ReviewEvent.from_audit_entry(
        entry, occurred_at_utc=FROZEN_NOW, schema_version=SCHEMA_VERSION
    )

    assert event.event_type is expected_type
    assert event.is_resolution is True
    assert event.resolution_sequence == entry.resolution_sequence
    assert event.reviewer_id == entry.reviewer_id
    assert event.audit_entry_payload == entry.to_dict()
    assert event.suggestion_id is None


def test_resolution_event_requires_audit_payload(review_case: ReviewCase) -> None:
    with pytest.raises(ReviewEventIntegrityError, match="audit entry payload"):
        ReviewEvent(
            review_case_id=review_case.review_case_id,
            event_type=ReviewEventType.MATCH,
            occurred_at_utc=FROZEN_NOW,
            schema_version=SCHEMA_VERSION,
            resolution_sequence=1,
        )


def test_resolution_event_requires_resolution_sequence(review_case: ReviewCase) -> None:
    entry = _audit_entry(review_case, HumanReviewDecision.MATCH)

    with pytest.raises(ReviewEventIntegrityError, match="resolution_sequence"):
        ReviewEvent(
            review_case_id=review_case.review_case_id,
            event_type=ReviewEventType.MATCH,
            occurred_at_utc=FROZEN_NOW,
            schema_version=SCHEMA_VERSION,
            audit_entry_payload=entry.to_dict(),
        )


def test_resolution_event_cannot_relabel_decision(review_case: ReviewCase) -> None:
    entry = _audit_entry(review_case, HumanReviewDecision.NO_MATCH)

    with pytest.raises(ReviewEventIntegrityError, match="cannot be recorded as"):
        ReviewEvent(
            review_case_id=review_case.review_case_id,
            event_type=ReviewEventType.MATCH,
            occurred_at_utc=FROZEN_NOW,
            schema_version=SCHEMA_VERSION,
            resolution_sequence=entry.resolution_sequence,
            audit_entry_payload=entry.to_dict(),
        )


def test_resolution_event_rejects_foreign_audit_entry(review_case: ReviewCase) -> None:
    entry = _audit_entry(review_case, HumanReviewDecision.MATCH)
    foreign_payload = {**entry.to_dict(), "review_case_id": "RC-0000000000000000"}

    with pytest.raises(ReviewEventIntegrityError, match="different review case"):
        ReviewEvent(
            review_case_id=review_case.review_case_id,
            event_type=ReviewEventType.MATCH,
            occurred_at_utc=FROZEN_NOW,
            schema_version=SCHEMA_VERSION,
            resolution_sequence=entry.resolution_sequence,
            audit_entry_payload=foreign_payload,
        )


def test_resolution_event_rejects_suggestion_id(review_case: ReviewCase) -> None:
    entry = _audit_entry(review_case, HumanReviewDecision.MATCH)

    with pytest.raises(ReviewEventIntegrityError, match="must not carry a suggestion_id"):
        ReviewEvent(
            review_case_id=review_case.review_case_id,
            event_type=ReviewEventType.MATCH,
            occurred_at_utc=FROZEN_NOW,
            schema_version=SCHEMA_VERSION,
            resolution_sequence=entry.resolution_sequence,
            audit_entry_payload=entry.to_dict(),
            suggestion_id="LS-0123456789abcdef",
        )


def test_audit_payload_is_defensively_copied(review_case: ReviewCase) -> None:
    entry = _audit_entry(review_case, HumanReviewDecision.MATCH)
    payload = entry.to_dict()

    event = ReviewEvent.from_audit_entry(
        entry, occurred_at_utc=FROZEN_NOW, schema_version=SCHEMA_VERSION
    )
    payload["human_decision"] = "NO_MATCH"

    assert event.audit_entry_payload is not None
    assert event.audit_entry_payload["human_decision"] == "MATCH"


def test_semantic_event_requires_suggestion_id(review_case: ReviewCase) -> None:
    with pytest.raises(ReviewEventIntegrityError, match="requires a suggestion_id"):
        ReviewEvent(
            review_case_id=review_case.review_case_id,
            event_type=ReviewEventType.SEMANTIC_SUGGESTION_RECORDED,
            occurred_at_utc=FROZEN_NOW,
            schema_version=SCHEMA_VERSION,
        )


def test_semantic_event_cannot_impersonate_a_resolution(review_case: ReviewCase) -> None:
    entry = _audit_entry(review_case, HumanReviewDecision.MATCH)

    with pytest.raises(ReviewEventIntegrityError, match="must not carry Human Review"):
        ReviewEvent(
            review_case_id=review_case.review_case_id,
            event_type=ReviewEventType.SEMANTIC_SUGGESTION_RECORDED,
            occurred_at_utc=FROZEN_NOW,
            schema_version=SCHEMA_VERSION,
            suggestion_id="LS-0123456789abcdef",
            resolution_sequence=1,
            audit_entry_payload=entry.to_dict(),
        )


def test_semantic_event_cannot_claim_a_reviewer(review_case: ReviewCase) -> None:
    with pytest.raises(ReviewEventIntegrityError, match="deciding reviewer"):
        ReviewEvent(
            review_case_id=review_case.review_case_id,
            event_type=ReviewEventType.SEMANTIC_SUGGESTION_RECORDED,
            occurred_at_utc=FROZEN_NOW,
            schema_version=SCHEMA_VERSION,
            suggestion_id="LS-0123456789abcdef",
            reviewer_id="reviewer-1",
        )


def test_semantic_event_happy_path(review_case: ReviewCase) -> None:
    event = ReviewEvent.semantic_suggestion_recorded(
        review_case.review_case_id,
        suggestion_id="LS-0123456789abcdef",
        occurred_at_utc=FROZEN_NOW,
        schema_version=SCHEMA_VERSION,
    )

    assert event.is_resolution is False
    assert event.audit_entry_payload is None
    assert event.resolution_sequence is None


def test_case_created_event_is_not_a_decision(review_case: ReviewCase) -> None:
    event = ReviewEvent.case_created(
        review_case.review_case_id,
        occurred_at_utc=FROZEN_NOW,
        schema_version=SCHEMA_VERSION,
    )

    assert event.event_type is ReviewEventType.CASE_CREATED
    assert event.is_resolution is False
    assert event.audit_entry_payload is None
    assert event.resolution_sequence is None

    with pytest.raises(ReviewEventIntegrityError, match="must not carry resolution"):
        ReviewEvent(
            review_case_id=review_case.review_case_id,
            event_type=ReviewEventType.CASE_CREATED,
            occurred_at_utc=FROZEN_NOW,
            schema_version=SCHEMA_VERSION,
            resolution_sequence=1,
        )


def _bundle(review_state, records, **overrides) -> WorkflowBundle:
    defaults = {
        "persisted_cases": tuple(
            PersistedCase.initial(case, now_utc=FROZEN_NOW) for case in review_state.cases
        ),
        "entity_records": records,
        "resolution_snapshot": {"source_label": "test", "auto_match_pairs": []},
        "next_resolution_sequence": review_state.next_resolution_sequence,
    }
    defaults.update(overrides)
    return WorkflowBundle(**defaults)


def test_workflow_bundle_rebuilds_sprint_08_state(review_state) -> None:
    from tests.human_review.conftest import make_review_resolution

    records = make_review_resolution("a-1", "a-2").records
    bundle = _bundle(review_state, records)

    rebuilt = bundle.to_workflow_state()

    assert rebuilt.cases == review_state.cases
    assert rebuilt.next_resolution_sequence == review_state.next_resolution_sequence
    assert set(bundle.records_by_id()) == {record.record_id for record in records}
    assert all(version == 1 for version in bundle.versions_by_case_id().values())


def test_workflow_bundle_rejects_missing_records(review_state) -> None:
    with pytest.raises(PersistedCaseIntegrityError, match="fail closed"):
        _bundle(review_state, ())


def test_workflow_bundle_rejects_incomplete_snapshot(review_state) -> None:
    from tests.human_review.conftest import make_review_resolution

    records = make_review_resolution("a-1", "a-2").records

    with pytest.raises(PersistedCaseIntegrityError, match="missing required keys"):
        _bundle(review_state, records, resolution_snapshot={"source_label": "test"})


def test_workflow_bundle_rejects_duplicate_cases(review_state) -> None:
    from tests.human_review.conftest import make_review_resolution

    records = make_review_resolution("a-1", "a-2").records
    duplicated = (
        tuple(PersistedCase.initial(case, now_utc=FROZEN_NOW) for case in review_state.cases) * 2
    )

    with pytest.raises(PersistedCaseIntegrityError, match="Duplicate review case"):
        _bundle(review_state, records, persisted_cases=duplicated)
