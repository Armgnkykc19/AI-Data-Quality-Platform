"""Row <-> domain conversion for review cases.

``case_payload_json`` stores ``ReviewCase.to_dict()`` verbatim, so persistence
adds no second serializer: the write side is the Sprint 08 contract itself.

The read side is written out here rather than borrowed from
``human_review.reporting``. That module's deserializer is ``_workflow_from_payload``
-- private, and shaped around a whole report envelope of cases, audit trail, and
next_resolution_sequence. Reaching it for a single row would mean wrapping that
row in a fake report envelope, and the mapper would then break whenever the
envelope changed for reasons having nothing to do with cases. Instead this
module reconstructs one case, field for field, in the same order and with the
same coercions, and ``test_repository_roundtrip`` pins it to the Sprint 08
representation by comparing against a real report written and loaded through the
public ``write_review_reports`` / ``load_human_review_report`` pair. Divergence
becomes a test failure rather than a silent difference.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from entity_resolution.models import MatchDecisionType, RecordPair
from human_review.models import (
    HumanReviewDecision,
    ReviewBlockingReason,
    ReviewCase,
    ReviewConflictEvidence,
    ReviewEvidence,
    ReviewResolution,
    ReviewStatus,
)
from review_application.errors import ReviewPersistenceError
from review_application.models import PersistedCase

# Column order used by every review_cases read and write in this package.
REVIEW_CASE_COLUMNS: tuple[str, ...] = (
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

REQUIRED_PAYLOAD_FIELDS: tuple[str, ...] = (
    "review_case_id",
    "record_a_id",
    "record_b_id",
    "machine_decision",
    "machine_score",
    "auto_match_threshold",
    "review_threshold",
    "machine_reason",
    "status",
)


def case_payload_json(case: ReviewCase) -> str:
    """The authoritative stored representation of a case.

    ``ReviewCase.to_dict()`` verbatim, serialized deterministically. Kept as one
    function because both the initial INSERT and the resolution UPDATE must
    write byte-identical JSON for an unchanged case.
    """
    return json.dumps(case.to_dict(), ensure_ascii=False, sort_keys=True)


def case_to_row(persisted: PersistedCase, *, schema_version: str) -> tuple[Any, ...]:
    """Project a PersistedCase onto the review_cases column tuple.

    The denormalized columns exist only so the queue can be filtered and
    indexed without parsing JSON. ``case_payload_json`` stays authoritative.
    """
    case = persisted.case
    return (
        case.review_case_id,
        case.pair.record_a_id,
        case.pair.record_b_id,
        case.status.value,
        case.machine_decision.value,
        case.machine_score,
        persisted.version,
        case_payload_json(case),
        schema_version,
        persisted.created_at_utc,
        persisted.updated_at_utc,
    )


def row_to_persisted_case(row: Mapping[str, Any]) -> PersistedCase:
    """Rebuild a PersistedCase, checking the row against its own payload."""
    payload = _decode_payload(row["case_payload_json"], str(row["review_case_id"]))
    _assert_row_agrees_with_payload(row, payload)
    return PersistedCase(
        case=review_case_from_payload(payload),
        version=int(row["version"]),
        created_at_utc=str(row["created_at_utc"]),
        updated_at_utc=str(row["updated_at_utc"]),
    )


def _decode_payload(raw: Any, review_case_id: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise ReviewPersistenceError(
            f"Stored case payload for {review_case_id} is not valid JSON: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise ReviewPersistenceError(
            f"Stored case payload for {review_case_id} must be a JSON object."
        )
    return payload


def _assert_row_agrees_with_payload(row: Mapping[str, Any], payload: Mapping[str, Any]) -> None:
    """Denormalized columns and the payload must tell the same story.

    They are written together in one transaction, so a disagreement means the
    row was edited outside this code. Loading it would hand the domain a case
    whose status is not the status the queue was filtered on.
    """
    for column in ("review_case_id", "record_a_id", "record_b_id", "status"):
        stored = str(row[column])
        declared = str(payload.get(column))
        if stored != declared:
            raise ReviewPersistenceError(
                f"Stored review case {row['review_case_id']} disagrees with its payload on "
                f"{column}: column={stored!r}, payload={declared!r}."
            )


def review_case_from_payload(payload: Mapping[str, Any]) -> ReviewCase:
    """Reconstruct a ReviewCase from a ``ReviewCase.to_dict()`` payload."""
    missing = [name for name in REQUIRED_PAYLOAD_FIELDS if name not in payload]
    if missing:
        raise ReviewPersistenceError(
            "Stored review case is missing required fields: " + ", ".join(missing)
        )

    record_a_id = str(payload["record_a_id"])
    record_b_id = str(payload["record_b_id"])
    try:
        return ReviewCase(
            review_case_id=str(payload["review_case_id"]),
            pair=RecordPair.ordered(record_a_id, record_b_id),
            record_ids=(record_a_id, record_b_id),
            machine_decision=MatchDecisionType(payload["machine_decision"]),
            machine_score=float(payload["machine_score"]),
            auto_match_threshold=float(payload["auto_match_threshold"]),
            review_threshold=float(payload["review_threshold"]),
            machine_reason=str(payload["machine_reason"]),
            blocking_reasons=tuple(
                ReviewBlockingReason(**reason) for reason in payload.get("blocking_reasons", [])
            ),
            supporting_evidence=tuple(
                ReviewEvidence(**evidence) for evidence in payload.get("supporting_evidence", [])
            ),
            conflicting_evidence=tuple(
                ReviewConflictEvidence(**conflict)
                for conflict in payload.get("conflicting_evidence", [])
            ),
            missing_evidence_notes=tuple(payload.get("missing_evidence_notes", [])),
            machine_readable_reasons=tuple(payload.get("machine_readable_reasons", [])),
            human_summary=str(payload.get("human_summary", "")),
            status=ReviewStatus(payload["status"]),
            resolution=_resolution_from_payload(payload),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ReviewPersistenceError(
            f"Stored review case {payload.get('review_case_id')!r} could not be rebuilt: {exc}"
        ) from exc


def _resolution_from_payload(payload: Mapping[str, Any]) -> ReviewResolution | None:
    raw = payload.get("resolution")
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ReviewPersistenceError("Stored review case resolution must be an object.")
    return ReviewResolution(
        review_case_id=str(payload["review_case_id"]),
        human_decision=HumanReviewDecision(raw["human_decision"]),
        reviewer_id=raw.get("reviewer_id"),
        resolution_sequence=int(raw["resolution_sequence"]),
        machine_decision=MatchDecisionType(raw["machine_decision"]),
        machine_reason=str(raw["machine_reason"]),
        downstream_action=str(raw["downstream_action"]),
    )
