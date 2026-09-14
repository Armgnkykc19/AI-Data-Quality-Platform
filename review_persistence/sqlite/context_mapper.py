"""Serialization for the workflow authorization context.

The write side reuses the public Sprint 08 helpers verbatim --
``entity_records_to_dict`` and ``resolution_snapshot`` from
``human_review.reporting`` -- so the stored representation is the same reduction
the ``human_review_report.json`` contract already defines. No second Entity
Resolution model is introduced.

The read side rebuilds entity records field for field, mirroring
``load_human_review_report``. Its record-building code is inline in that public
loader rather than a reusable function, so it cannot be called for a single
context. ``test_workflow_context.py`` pins this module to the public contract by
round-tripping a real report.

The AUTO_MATCH snapshot is returned as the stored reduced mapping, not as a
``ResolutionResult``: that is what the Phase A ``WorkflowBundle`` declares, and
it keeps persistence from depending on entity_resolution construction details.
``ReviewQueueService`` rebuilds the domain object through the public Sprint 08
helper ``rebuild_resolution_from_snapshot``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from entity_resolution.models import EntityRecord
from human_review.reporting import entity_records_to_dict
from review_application.errors import ReviewPersistenceError

# Keys of the reduced snapshot produced by human_review.reporting.resolution_snapshot.
SNAPSHOT_SOURCE_LABEL_KEY = "source_label"
SNAPSHOT_AUTO_MATCH_PAIRS_KEY = "auto_match_pairs"


def canonical_json(payload: Any) -> str:
    """Deterministic JSON: sorted keys, compact separators, no ASCII escaping.

    Stored text must not vary with dict iteration order, or an identical
    re-registration would look like a context conflict.
    """
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def entity_records_to_payload(records: Sequence[EntityRecord]) -> list[dict[str, Any]]:
    """Reuse the public Sprint 08 record serializer unchanged."""
    return entity_records_to_dict(tuple(records))


def entity_records_from_payload(payload: Any) -> tuple[EntityRecord, ...]:
    """Rebuild entity records, mirroring load_human_review_report field for field."""
    if not isinstance(payload, list) or not payload:
        raise ReviewPersistenceError(
            "Stored workflow context must hold a non-empty entity_records list."
        )
    records: list[EntityRecord] = []
    for item in payload:
        if not isinstance(item, Mapping):
            raise ReviewPersistenceError("Stored entity_records entries must be objects.")
        if "record_id" not in item:
            raise ReviewPersistenceError("Stored entity_records entries must include record_id.")
        records.append(
            EntityRecord(
                record_id=str(item["record_id"]),
                source_name=str(item.get("source_name") or "unknown"),
                field_values=dict(item.get("field_values") or {}),
            )
        )
    return tuple(records)


def assert_snapshot_shape(snapshot: Mapping[str, Any]) -> None:
    """Reject a snapshot the Sprint 08 rebuild path could not consume.

    Checked at write time rather than at load time, so a context that could
    never reconstruct authorization never reaches the database.
    """
    if not isinstance(snapshot, Mapping):
        raise ReviewPersistenceError("resolution_snapshot must be a mapping.")
    missing = [
        key
        for key in (SNAPSHOT_SOURCE_LABEL_KEY, SNAPSHOT_AUTO_MATCH_PAIRS_KEY)
        if key not in snapshot
    ]
    if missing:
        raise ReviewPersistenceError(
            f"resolution_snapshot is missing required keys: {sorted(missing)}."
        )
    pairs = snapshot[SNAPSHOT_AUTO_MATCH_PAIRS_KEY]
    if not isinstance(pairs, Sequence) or isinstance(pairs, str | bytes):
        raise ReviewPersistenceError("resolution_snapshot.auto_match_pairs must be a sequence.")
    for pair in pairs:
        if not isinstance(pair, Sequence) or isinstance(pair, str | bytes) or len(pair) != 2:
            raise ReviewPersistenceError(
                "resolution_snapshot.auto_match_pairs entries must be record-id pairs."
            )


def snapshot_to_payload(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize the reduced snapshot into its stored JSON shape.

    Pairs become lists because that is what
    ``rebuild_resolution_from_snapshot`` expects and
    what JSON produces on the way back; a tuple passed in here would otherwise
    round-trip as a list and look like a change.
    """
    assert_snapshot_shape(snapshot)
    payload = {key: value for key, value in snapshot.items()}
    payload[SNAPSHOT_AUTO_MATCH_PAIRS_KEY] = [
        [str(pair[0]), str(pair[1])] for pair in snapshot[SNAPSHOT_AUTO_MATCH_PAIRS_KEY]
    ]
    return payload


def snapshot_from_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ReviewPersistenceError("Stored resolution_snapshot must be an object.")
    snapshot = dict(payload)
    assert_snapshot_shape(snapshot)
    return snapshot


def normalized_context_fingerprint(
    records_payload: Sequence[Mapping[str, Any]],
    snapshot_payload: Mapping[str, Any],
) -> str:
    """A canonical form used only to decide whether two contexts are equivalent.

    Record order and AUTO_MATCH edge order carry no meaning: authorization
    indexes records by id and unions edges, and the union-find tie-break makes
    the result order-independent. Sorting both here keeps a caller that
    supplies the same content in a different order from being reported as a
    conflict, which section 13 of the phase brief requires.

    This is a comparison key only. What is stored keeps the caller's order.
    """
    records = sorted(
        (dict(record) for record in records_payload),
        key=lambda record: str(record.get("record_id")),
    )
    snapshot = dict(snapshot_payload)
    snapshot[SNAPSHOT_AUTO_MATCH_PAIRS_KEY] = sorted(
        [str(pair[0]), str(pair[1])] for pair in snapshot.get(SNAPSHOT_AUTO_MATCH_PAIRS_KEY, [])
    )
    return canonical_json({"entity_records": records, "resolution_snapshot": snapshot})


def decode_json_column(raw: Any, column: str) -> Any:
    try:
        return json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise ReviewPersistenceError(
            f"Stored workflow context column {column} is not valid JSON: {exc}"
        ) from exc
