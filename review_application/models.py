"""Application-layer wrappers around unchanged Sprint 08 domain objects.

Persistence metadata (version, timestamps) lives here and never on
``human_review.models.ReviewCase``. Adding a field to that dataclass would
change ``ReviewCase.to_dict()``, which feeds the Sprint 08
``human_review_report.json`` contract at schema version 1.0.0.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from entity_resolution.models import EntityRecord
from human_review.models import (
    HumanReviewDecision,
    ReviewAuditEntry,
    ReviewCase,
    ReviewStatus,
    ReviewWorkflowState,
)
from review_application.errors import (
    PersistedCaseIntegrityError,
    ReviewEventIntegrityError,
)

INITIAL_CASE_VERSION = 1

# Keys produced by human_review.reporting.resolution_snapshot(). The reduced
# snapshot is what Sprint 08 already persists and rebuilds authorization from;
# Sprint 10 stores the same shape rather than inventing a second one.
RESOLUTION_SNAPSHOT_KEYS = frozenset({"source_label", "auto_match_pairs"})


class ReviewEventType(StrEnum):
    CASE_CREATED = "CASE_CREATED"
    SEMANTIC_SUGGESTION_RECORDED = "SEMANTIC_SUGGESTION_RECORDED"
    MATCH = "MATCH"
    NO_MATCH = "NO_MATCH"
    DEFERRED = "DEFERRED"


RESOLUTION_EVENT_TYPES = frozenset(
    {ReviewEventType.MATCH, ReviewEventType.NO_MATCH, ReviewEventType.DEFERRED}
)

# HumanReviewDecision.DEFER maps to the DEFERRED terminal state, mirroring
# human_review.workflow._status_for_decision. Kept explicit so a projection can
# never silently relabel a decision.
DECISION_TO_EVENT_TYPE: dict[HumanReviewDecision, ReviewEventType] = {
    HumanReviewDecision.MATCH: ReviewEventType.MATCH,
    HumanReviewDecision.NO_MATCH: ReviewEventType.NO_MATCH,
    HumanReviewDecision.DEFER: ReviewEventType.DEFERRED,
}


def _require_text(value: str, field_name: str, error: type[Exception]) -> None:
    if not isinstance(value, str) or not value.strip():
        raise error(f"{field_name} must be a non-empty string.")


@dataclass(frozen=True)
class PersistedCase:
    """A Sprint 08 ReviewCase plus the persistence metadata it must not carry."""

    case: ReviewCase
    version: int
    created_at_utc: str
    updated_at_utc: str

    def __post_init__(self) -> None:
        if not isinstance(self.version, int) or isinstance(self.version, bool):
            raise PersistedCaseIntegrityError("version must be an integer.")
        if self.version < INITIAL_CASE_VERSION:
            raise PersistedCaseIntegrityError(
                f"version must be >= {INITIAL_CASE_VERSION}; got {self.version}."
            )
        _require_text(self.created_at_utc, "created_at_utc", PersistedCaseIntegrityError)
        _require_text(self.updated_at_utc, "updated_at_utc", PersistedCaseIntegrityError)
        _require_text(self.case.review_case_id, "case.review_case_id", PersistedCaseIntegrityError)

    @property
    def review_case_id(self) -> str:
        """Identity comes only from the domain case; it is never stored twice."""
        return self.case.review_case_id

    @property
    def status(self) -> ReviewStatus:
        return self.case.status

    @classmethod
    def initial(cls, case: ReviewCase, *, now_utc: str) -> PersistedCase:
        return cls(
            case=case,
            version=INITIAL_CASE_VERSION,
            created_at_utc=now_utc,
            updated_at_utc=now_utc,
        )

    def with_case(self, case: ReviewCase, *, now_utc: str) -> PersistedCase:
        """Advance persistence metadata for an already-transitioned domain case.

        This bumps the version; it does not decide or validate the transition.
        Only ``ReviewWorkflow.resolve_case`` may produce the new case.
        """
        if case.review_case_id != self.review_case_id:
            raise PersistedCaseIntegrityError(
                "Persisted case identity is immutable: "
                f"{self.review_case_id} cannot become {case.review_case_id}."
            )
        return PersistedCase(
            case=case,
            version=self.version + 1,
            created_at_utc=self.created_at_utc,
            updated_at_utc=now_utc,
        )


@dataclass(frozen=True)
class ReviewEvent:
    """One append-only history entry.

    Resolution events are projections of a domain-generated ReviewAuditEntry.
    The invariants below are what stop persistence from synthesizing a Human
    Review decision that ``ReviewWorkflow`` never made.
    """

    review_case_id: str
    event_type: ReviewEventType
    occurred_at_utc: str
    # Persistence metadata, assigned by the storage layer exactly like event_id:
    # it records which database schema wrote the row, which the application has
    # no way of knowing and no reason to choose. None on an event that has not
    # been stored yet; populated on the way back out.
    schema_version: str | None = None
    event_id: int | None = None
    resolution_sequence: int | None = None
    reviewer_id: str | None = None
    audit_entry_payload: dict[str, Any] | None = None
    suggestion_id: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.review_case_id, "review_case_id", ReviewEventIntegrityError)
        _require_text(self.occurred_at_utc, "occurred_at_utc", ReviewEventIntegrityError)
        if self.schema_version is not None:
            _require_text(self.schema_version, "schema_version", ReviewEventIntegrityError)
        if self.audit_entry_payload is not None:
            # Defensive copy: a frozen dataclass holding a caller's mutable dict
            # is not actually immutable.
            object.__setattr__(self, "audit_entry_payload", dict(self.audit_entry_payload))
        if self.event_type in RESOLUTION_EVENT_TYPES:
            self._validate_resolution_event()
        elif self.event_type == ReviewEventType.SEMANTIC_SUGGESTION_RECORDED:
            self._validate_semantic_event()
        else:
            self._validate_lifecycle_event()

    def _validate_resolution_event(self) -> None:
        if self.resolution_sequence is None or self.resolution_sequence < 1:
            raise ReviewEventIntegrityError(
                f"{self.event_type.value} requires a resolution_sequence >= 1."
            )
        payload = self.audit_entry_payload
        if not payload:
            raise ReviewEventIntegrityError(
                f"{self.event_type.value} requires the domain-generated audit entry payload."
            )
        if self.suggestion_id is not None:
            raise ReviewEventIntegrityError(
                "A Human Review resolution event must not carry a suggestion_id."
            )
        self._assert_payload_agrees(payload)

    def _assert_payload_agrees(self, payload: Mapping[str, Any]) -> None:
        """The event may not relabel the audit entry it claims to project."""
        if payload.get("review_case_id") != self.review_case_id:
            raise ReviewEventIntegrityError(
                "Audit entry belongs to a different review case than the event."
            )
        if payload.get("resolution_sequence") != self.resolution_sequence:
            raise ReviewEventIntegrityError(
                "Audit entry resolution_sequence disagrees with the event."
            )
        raw_decision = payload.get("human_decision")
        try:
            decision = HumanReviewDecision(raw_decision)
        except ValueError as exc:
            raise ReviewEventIntegrityError(
                f"Audit entry carries an unknown human_decision: {raw_decision!r}."
            ) from exc
        if DECISION_TO_EVENT_TYPE[decision] != self.event_type:
            raise ReviewEventIntegrityError(
                f"Audit entry decision {decision.value} cannot be recorded as "
                f"event {self.event_type.value}."
            )

    def _validate_semantic_event(self) -> None:
        if not self.suggestion_id or not self.suggestion_id.strip():
            raise ReviewEventIntegrityError(
                "SEMANTIC_SUGGESTION_RECORDED requires a suggestion_id."
            )
        # An advisory suggestion has no decision and no deciding human. Refusing
        # these fields is what keeps a suggestion from impersonating a review.
        if self.resolution_sequence is not None or self.audit_entry_payload is not None:
            raise ReviewEventIntegrityError(
                "A semantic suggestion event must not carry Human Review resolution data."
            )
        if self.reviewer_id is not None:
            raise ReviewEventIntegrityError(
                "A semantic suggestion event must not claim a deciding reviewer."
            )

    def _validate_lifecycle_event(self) -> None:
        if (
            self.resolution_sequence is not None
            or self.audit_entry_payload is not None
            or self.suggestion_id is not None
            or self.reviewer_id is not None
        ):
            raise ReviewEventIntegrityError(
                f"{self.event_type.value} must not carry resolution or suggestion data."
            )

    @property
    def is_resolution(self) -> bool:
        return self.event_type in RESOLUTION_EVENT_TYPES

    @classmethod
    def from_audit_entry(
        cls,
        entry: ReviewAuditEntry,
        *,
        occurred_at_utc: str,
        schema_version: str | None = None,
        event_id: int | None = None,
    ) -> ReviewEvent:
        """Build a resolution event from the audit entry the domain produced.

        This is the only supported way to create MATCH / NO_MATCH / DEFERRED
        history, so a decision can never be assembled from raw strings.

        ``schema_version`` is left to the storage layer. An application caller
        does not know which database schema will receive the event, and making
        it supply one would put a persistence constant in the application.
        """
        decision = HumanReviewDecision(entry.human_decision)
        return cls(
            review_case_id=entry.review_case_id,
            event_type=DECISION_TO_EVENT_TYPE[decision],
            occurred_at_utc=occurred_at_utc,
            schema_version=schema_version,
            event_id=event_id,
            resolution_sequence=entry.resolution_sequence,
            reviewer_id=entry.reviewer_id,
            audit_entry_payload=entry.to_dict(),
        )

    @classmethod
    def case_created(
        cls,
        review_case_id: str,
        *,
        occurred_at_utc: str,
        schema_version: str | None = None,
        event_id: int | None = None,
    ) -> ReviewEvent:
        return cls(
            review_case_id=review_case_id,
            event_type=ReviewEventType.CASE_CREATED,
            occurred_at_utc=occurred_at_utc,
            schema_version=schema_version,
            event_id=event_id,
        )

    @classmethod
    def semantic_suggestion_recorded(
        cls,
        review_case_id: str,
        *,
        suggestion_id: str,
        occurred_at_utc: str,
        schema_version: str | None = None,
        event_id: int | None = None,
    ) -> ReviewEvent:
        return cls(
            review_case_id=review_case_id,
            event_type=ReviewEventType.SEMANTIC_SUGGESTION_RECORDED,
            occurred_at_utc=occurred_at_utc,
            schema_version=schema_version,
            event_id=event_id,
            suggestion_id=suggestion_id,
        )


@dataclass(frozen=True)
class WorkflowBundle:
    """Everything Sprint 08 MATCH authorization needs, loaded as one snapshot.

    ``assert_human_match_authorization_boundary`` projects component membership
    transitively across the whole case set, so authorization cannot be evaluated
    from a single case row. Loading a partial bundle would silently weaken the
    check, which is why record coverage is validated here.
    """

    persisted_cases: tuple[PersistedCase, ...]
    entity_records: tuple[EntityRecord, ...]
    resolution_snapshot: dict[str, Any]
    next_resolution_sequence: int
    audit_entries: tuple[ReviewAuditEntry, ...] = ()
    entity_resolution_config_path: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "resolution_snapshot", dict(self.resolution_snapshot))
        if self.next_resolution_sequence < 1:
            raise PersistedCaseIntegrityError("next_resolution_sequence must be >= 1.")
        missing_keys = RESOLUTION_SNAPSHOT_KEYS - set(self.resolution_snapshot)
        if missing_keys:
            raise PersistedCaseIntegrityError(
                f"resolution_snapshot is missing required keys: {sorted(missing_keys)}."
            )
        if not isinstance(self.resolution_snapshot["auto_match_pairs"], Sequence):
            raise PersistedCaseIntegrityError(
                "resolution_snapshot.auto_match_pairs must be a sequence of record-id pairs."
            )
        self._assert_unique_cases()
        self._assert_record_coverage()

    def _assert_unique_cases(self) -> None:
        seen: set[str] = set()
        for persisted in self.persisted_cases:
            if persisted.review_case_id in seen:
                raise PersistedCaseIntegrityError(
                    f"Duplicate review case in bundle: {persisted.review_case_id}."
                )
            seen.add(persisted.review_case_id)

    def _assert_record_coverage(self) -> None:
        known = {record.record_id for record in self.entity_records}
        for persisted in self.persisted_cases:
            pair = persisted.case.pair
            missing = [rid for rid in (pair.record_a_id, pair.record_b_id) if rid not in known]
            if missing:
                raise PersistedCaseIntegrityError(
                    f"Bundle omits records {missing} required by case "
                    f"{persisted.review_case_id}; MATCH authorization would fail closed."
                )

    def cases(self) -> tuple[ReviewCase, ...]:
        return tuple(persisted.case for persisted in self.persisted_cases)

    def versions_by_case_id(self) -> dict[str, int]:
        return {persisted.review_case_id: persisted.version for persisted in self.persisted_cases}

    def records_by_id(self) -> dict[str, EntityRecord]:
        return {record.record_id: record for record in self.entity_records}

    def to_workflow_state(self) -> ReviewWorkflowState:
        """Rebuild the unchanged Sprint 08 state object for the domain to use."""
        return ReviewWorkflowState(
            cases=self.cases(),
            audit_trail=self.audit_entries,
            next_resolution_sequence=self.next_resolution_sequence,
        )
