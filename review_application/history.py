"""Reconciliation of stored cases with the append-only resolution history.

Two records of the same decision exist in the database: the resolved
``ReviewCase`` itself, and the resolution event appended when it was resolved.
Sprint 08 keeps a third view -- ``ReviewWorkflowState.audit_trail`` -- which the
service must hand back to ``ReviewWorkflow`` intact. This module rebuilds that
trail and fails closed whenever the two stored records disagree, because a
disagreement means one of them is not what the domain produced.

Phase C derived ``next_resolution_sequence`` from resolved cases alone, since no
event table was populated yet. Databases written then are still readable: a
resolved case with no event is treated as an import, and its audit entry is
reconstructed from the ``ReviewResolution`` the domain already stamped on it.
Every field of a ``ReviewAuditEntry`` is present there, so nothing is invented.

The audit trail is not an authorization input -- ``HumanReviewOutcome`` derives
resolved MATCH and NO_MATCH pairs from the cases -- so an imported case
constrains a later MATCH whether or not it left an event behind.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from human_review.models import ReviewAuditEntry, ReviewCase, ReviewStatus
from review_application.errors import ReviewEventIntegrityError
from review_application.models import DECISION_TO_EVENT_TYPE, PersistedCase, ReviewEvent


@dataclass(frozen=True)
class ReconstructedHistory:
    """The Sprint 08 audit trail plus the sequence the next resolution takes."""

    audit_entries: tuple[ReviewAuditEntry, ...]
    next_resolution_sequence: int


def audit_entry_from_case(case: ReviewCase) -> ReviewAuditEntry:
    """Project a resolved case's own resolution back into an audit entry.

    Used only for cases stored without an event. The decision, reviewer,
    sequence and downstream action all come from the ``ReviewResolution`` that
    ``ReviewWorkflow.resolve_case`` created, so this is a projection of a domain
    decision and never a new one.
    """
    resolution = case.resolution
    if resolution is None:
        raise ReviewEventIntegrityError(
            f"Review case {case.review_case_id} is {case.status.value} but carries no "
            "resolution; its audit entry cannot be reconstructed."
        )
    return ReviewAuditEntry(
        review_case_id=case.review_case_id,
        record_a_id=case.pair.record_a_id,
        record_b_id=case.pair.record_b_id,
        machine_decision=case.machine_decision.value,
        machine_reason=case.machine_reason,
        human_decision=resolution.human_decision.value,
        reviewer_id=resolution.reviewer_id,
        resolution_sequence=resolution.resolution_sequence,
        downstream_action=resolution.downstream_action,
    )


def reconstruct_history(
    persisted_cases: Sequence[PersistedCase],
    events: Sequence[ReviewEvent],
) -> ReconstructedHistory:
    """Rebuild the audit trail from cases and resolution events together."""
    cases_by_id = {persisted.review_case_id: persisted.case for persisted in persisted_cases}
    events_by_case = _resolution_events_by_case(events, cases_by_id)

    entries: list[ReviewAuditEntry] = []
    for review_case_id, case in cases_by_id.items():
        if case.status is ReviewStatus.PENDING:
            continue
        event = events_by_case.get(review_case_id)
        entries.append(
            audit_entry_from_case(case) if event is None else _entry_from_event(event, case)
        )

    entries.sort(key=lambda entry: entry.resolution_sequence)
    _assert_contiguous_sequences(entries)
    return ReconstructedHistory(
        audit_entries=tuple(entries),
        next_resolution_sequence=len(entries) + 1,
    )


def _resolution_events_by_case(
    events: Sequence[ReviewEvent],
    cases_by_id: dict[str, ReviewCase],
) -> dict[str, ReviewEvent]:
    """Index resolution events by case, rejecting impossible arrangements."""
    indexed: dict[str, ReviewEvent] = {}
    for event in events:
        if not event.is_resolution:
            continue
        case = cases_by_id.get(event.review_case_id)
        if case is None:
            raise ReviewEventIntegrityError(
                f"Resolution event {event.event_id} refers to review case "
                f"{event.review_case_id}, which is not stored."
            )
        if event.review_case_id in indexed:
            # A case is resolvable only while PENDING, so a second resolution
            # event means history was written by something other than the
            # workflow.
            raise ReviewEventIntegrityError(
                f"Review case {event.review_case_id} carries more than one resolution "
                "event; a case can only be resolved once."
            )
        _assert_event_agrees_with_case(event, case)
        indexed[event.review_case_id] = event
    return indexed


def _assert_event_agrees_with_case(event: ReviewEvent, case: ReviewCase) -> None:
    """The two stored records of one decision must tell the same story."""
    if case.status.value != event.event_type.value:
        raise ReviewEventIntegrityError(
            f"Review case {case.review_case_id} is stored as {case.status.value} but its "
            f"resolution event records {event.event_type.value}."
        )
    resolution = case.resolution
    if resolution is None:
        raise ReviewEventIntegrityError(
            f"Review case {case.review_case_id} has a resolution event but no stored "
            "ReviewResolution."
        )
    if resolution.resolution_sequence != event.resolution_sequence:
        raise ReviewEventIntegrityError(
            f"Review case {case.review_case_id} was resolved at sequence "
            f"{resolution.resolution_sequence} but its event records "
            f"{event.resolution_sequence}."
        )
    if resolution.reviewer_id != event.reviewer_id:
        raise ReviewEventIntegrityError(
            f"Review case {case.review_case_id} records reviewer "
            f"{resolution.reviewer_id!r} but its event names {event.reviewer_id!r}."
        )
    if DECISION_TO_EVENT_TYPE[resolution.human_decision] != event.event_type:
        raise ReviewEventIntegrityError(
            f"Review case {case.review_case_id} records decision "
            f"{resolution.human_decision.value} but its event is {event.event_type.value}."
        )


def audit_entry_from_payload(payload: Mapping[str, Any]) -> ReviewAuditEntry:
    """Rebuild the Sprint 08 audit entry, keyword for keyword.

    Constructed by ``**payload`` rather than field by field, mirroring
    ``human_review.reporting._workflow_from_payload``: a payload that has
    drifted from the dataclass -- a missing field or an extra one -- raises
    instead of silently producing an entry with a default value nobody
    recorded.
    """
    try:
        return ReviewAuditEntry(**dict(payload))
    except TypeError as exc:
        raise ReviewEventIntegrityError(
            f"Stored audit entry does not match the Sprint 08 ReviewAuditEntry contract: {exc}"
        ) from exc


def _entry_from_event(event: ReviewEvent, case: ReviewCase) -> ReviewAuditEntry:
    """The event payload is authoritative, and must still describe this case."""
    payload = event.audit_entry_payload
    if not payload:
        raise ReviewEventIntegrityError(
            f"Resolution event for {event.review_case_id} carries no audit entry payload."
        )
    entry = audit_entry_from_payload(payload)
    stored_pair = (case.pair.record_a_id, case.pair.record_b_id)
    if (entry.record_a_id, entry.record_b_id) != stored_pair:
        raise ReviewEventIntegrityError(
            f"Audit entry for {case.review_case_id} names records "
            f"{(entry.record_a_id, entry.record_b_id)}, not the stored pair {stored_pair}."
        )
    return entry


def _assert_contiguous_sequences(entries: Sequence[ReviewAuditEntry]) -> None:
    """Sprint 08 stamps resolutions 1, 2, 3, ... with no gaps and no repeats.

    ``ReviewWorkflow`` takes the state's ``next_resolution_sequence``, uses it,
    and increments by one. A gap or a repeat in what was stored therefore means
    a resolution was lost or written twice, and continuing would hand the next
    reviewer a sequence the audit trail cannot account for.
    """
    for position, entry in enumerate(entries, start=1):
        if entry.resolution_sequence != position:
            raise ReviewEventIntegrityError(
                "Stored resolution sequences are not contiguous: expected "
                f"{position} at position {position}, found "
                f"{entry.resolution_sequence} for case {entry.review_case_id}."
            )
