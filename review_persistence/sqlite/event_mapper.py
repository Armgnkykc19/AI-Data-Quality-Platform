"""Row <-> domain conversion for review_case_events.

The stored ``audit_entry_json`` is ``ReviewAuditEntry.to_dict()`` verbatim, the
same projection Sprint 08 already writes into ``human_review_report.json``. The
read side rebuilds the dataclass by keyword, exactly as
``human_review.reporting._workflow_from_payload`` does, so a stored event and a
reported audit entry can never describe the decision differently.

Nothing here decides anything. A resolution row is only ever produced from a
``ReviewEvent`` that :meth:`ReviewEvent.from_audit_entry` built out of a
domain-generated audit entry, and the schema CHECK makes the alternative
unrepresentable.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from review_application.errors import ReviewEventIntegrityError, ReviewPersistenceError
from review_application.models import ReviewEvent, ReviewEventType
from review_persistence.schema import DATABASE_SCHEMA_VERSION

# Insert order. event_id is omitted: it is AUTOINCREMENT, and letting a caller
# choose it would make "append-only" depend on the caller's arithmetic.
REVIEW_EVENT_INSERT_COLUMNS: tuple[str, ...] = (
    "review_queue_id",
    "review_case_id",
    "event_type",
    "resolution_sequence",
    "reviewer_id",
    "audit_entry_json",
    "suggestion_id",
    "occurred_at_utc",
    "schema_version",
)

REVIEW_EVENT_SELECT_COLUMNS: tuple[str, ...] = ("event_id", *REVIEW_EVENT_INSERT_COLUMNS)


def event_to_row(event: ReviewEvent, *, review_queue_id: str) -> tuple[Any, ...]:
    """Project a ReviewEvent onto the review_case_events insert tuple.

    The schema version is stamped here, not taken from the caller: it describes
    the database this build is writing to, which only the persistence layer
    knows. An event that already declares a different one is refused rather than
    silently relabelled -- that would mean re-writing history under a version
    that never wrote it.

    ``review_queue_id`` comes from the repository's queue binding for the same
    reason the case row's does: ``ReviewEvent`` is an application object and
    carries no tenant field. The composite foreign key then rejects the row
    outright if that queue does not own the named case.
    """
    if event.schema_version is not None and event.schema_version != DATABASE_SCHEMA_VERSION:
        raise ReviewEventIntegrityError(
            f"Event declares schema version {event.schema_version!r}, but this build "
            f"writes {DATABASE_SCHEMA_VERSION!r}. Refusing to re-stamp stored history."
        )
    payload = event.audit_entry_payload
    return (
        review_queue_id,
        event.review_case_id,
        event.event_type.value,
        event.resolution_sequence,
        event.reviewer_id,
        None if payload is None else json.dumps(payload, ensure_ascii=False, sort_keys=True),
        event.suggestion_id,
        event.occurred_at_utc,
        DATABASE_SCHEMA_VERSION,
    )


def row_to_review_event(row: Mapping[str, Any]) -> ReviewEvent:
    """Rebuild a ReviewEvent from a stored row.

    ``ReviewEvent.__post_init__`` re-runs every event-type invariant on the way
    back, so a row edited outside this code -- a MATCH whose audit payload names
    a different case, say -- is rejected at load time rather than handed to the
    caller as history.
    """
    raw_type = str(row["event_type"])
    try:
        event_type = ReviewEventType(raw_type)
    except ValueError as exc:
        raise ReviewEventIntegrityError(
            f"Stored event carries unknown event_type {raw_type!r}."
        ) from exc

    return ReviewEvent(
        review_case_id=str(row["review_case_id"]),
        event_type=event_type,
        occurred_at_utc=str(row["occurred_at_utc"]),
        schema_version=str(row["schema_version"]),
        event_id=int(row["event_id"]),
        resolution_sequence=(
            None if row["resolution_sequence"] is None else int(row["resolution_sequence"])
        ),
        reviewer_id=row["reviewer_id"],
        audit_entry_payload=_decode_audit_payload(
            row["audit_entry_json"], str(row["review_case_id"])
        ),
        suggestion_id=row["suggestion_id"],
    )


def _decode_audit_payload(raw: Any, review_case_id: str) -> dict[str, Any] | None:
    if raw is None:
        return None
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise ReviewPersistenceError(
            f"Stored audit entry for {review_case_id} is not valid JSON: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise ReviewPersistenceError(
            f"Stored audit entry for {review_case_id} must be a JSON object."
        )
    return payload
