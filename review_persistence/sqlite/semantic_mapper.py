"""Row <-> domain conversion for advisory Sprint 09 suggestions.

No SQL lives here, and no Human Review authorization. This module only decides
what a stored suggestion looks like and refuses anything that is not a faithful
Sprint 09 object.

Two decisions are worth explaining.

The stored payload is ``SemanticSuggestion.to_dict()`` and nothing else, which
means ``explanation`` is deliberately not durable. That field is the only
unconstrained free-form text the provider returns, and the model writes it after
reading the untrusted record section, so it can quote customer field values.
Every other durable artifact in this system is value-free -- Sprint 08 evidence
descriptions name fields, never values -- and Sprint 09 already excludes
``explanation`` from both ``to_dict()`` and ``suggestion_audit_payload``.
Persistence keeps that boundary rather than widening what the platform retains
from model output. Nothing that decides anything is lost: no authorization path
reads the explanation.

The consequence is stated plainly because it matters for replay: a suggestion
reconstructed from storage carries an empty ``explanation``, and two
observations that differ only in their explanation are the same persisted
observation. See :func:`suggestion_payload`.

The rebuild is validated against ``dataclasses.fields(SemanticSuggestion)``
rather than a hand-written list. If Sprint 09 gains or loses a field, stored
payloads stop matching and fail closed, instead of quietly reconstructing a
suggestion with a default nobody recorded.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Mapping
from typing import Any

from review_application.errors import (
    ReviewPersistenceError,
    SemanticSuggestionIntegrityError,
)
from review_persistence.schema import assert_supported_schema_version
from semantic_review.ids import stable_suggestion_id
from semantic_review.models import (
    FORBIDDEN_HUMAN_DECISIONS,
    CostMode,
    SemanticFailureCode,
    SemanticSuggestion,
    SemanticSuggestionType,
)

# Insert/select order for semantic_suggestions, leading with the queue that
# owns the row. A Sprint 09 suggestion id is a content address, so it is unique
# per queue rather than per database; the column pair is what makes that true.
SEMANTIC_SUGGESTION_COLUMNS: tuple[str, ...] = (
    "review_queue_id",
    "suggestion_id",
    "review_case_id",
    "record_a_id",
    "record_b_id",
    "suggestion",
    "failure_code",
    "live",
    "provider",
    "requested_model",
    "suggestion_payload_json",
    "created_at_utc",
    "schema_version",
)

# The one Sprint 09 field that is intentionally not persisted. Named here so the
# rebuild can account for it explicitly instead of silently defaulting it.
NON_DURABLE_FIELDS: frozenset[str] = frozenset({"explanation"})

# Neutral placeholder for a field that was never stored. Not a default value
# standing in for lost content: there is no stored content to stand in for.
ABSENT_EXPLANATION = ""

SEMANTIC_SUGGESTION_FIELDS: frozenset[str] = frozenset(
    field.name for field in dataclasses.fields(SemanticSuggestion)
)

DURABLE_SUGGESTION_FIELDS: frozenset[str] = SEMANTIC_SUGGESTION_FIELDS - NON_DURABLE_FIELDS


def suggestion_payload(suggestion: SemanticSuggestion) -> dict[str, Any]:
    """The durable representation of one suggestion: the Sprint 09 payload.

    ``SemanticSuggestion.to_dict()`` verbatim, with nothing added. It carries
    every field except ``explanation``, which this layer does not retain.
    """
    return suggestion.to_dict()


def canonical_suggestion_json(suggestion: SemanticSuggestion) -> str:
    """Deterministic JSON, so two recordings of one suggestion compare as bytes.

    Sorted keys and compact separators, the same normalization
    ``semantic_review.ids.canonical_json_digest`` applies. Without it, dict
    iteration order alone could make an identical replay look like a conflicting
    one.

    This is the comparison key for duplicate detection, and it covers exactly
    what is retained. Two observations differing only in ``explanation``
    therefore compare equal and the first stored row stands; any retained field
    differing under one id is a conflict.
    """
    return json.dumps(
        suggestion_payload(suggestion),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def assert_is_sprint_09_suggestion(suggestion: object) -> SemanticSuggestion:
    """Refuse anything that is not a well-formed Sprint 09 suggestion.

    The identity check is the important one: Sprint 09 ids are content
    addressed, so the id can be recomputed from the fields it was derived from.
    Recomputing it with Sprint 09's own function -- rather than matching a
    format -- is what catches an id that was edited to impersonate a different
    observation, and it duplicates none of the id contract.
    """
    if not isinstance(suggestion, SemanticSuggestion):
        raise SemanticSuggestionIntegrityError(
            f"Expected a Sprint 09 SemanticSuggestion; got {type(suggestion).__name__}."
        )
    if not isinstance(suggestion.suggestion, SemanticSuggestionType):
        raise SemanticSuggestionIntegrityError(
            "suggestion must be a SemanticSuggestionType; persistence does not coerce "
            "free text into an advisory value."
        )
    if suggestion.suggestion.value in FORBIDDEN_HUMAN_DECISIONS:
        # Unreachable through the enum, asserted anyway: this is the boundary
        # that keeps an advisory row from ever spelling a human decision.
        raise SemanticSuggestionIntegrityError(
            f"{suggestion.suggestion.value} is a Human Review decision and can never "
            "be recorded as a semantic suggestion."
        )
    expected_id = stable_suggestion_id(
        request_id=suggestion.request_id,
        suggestion=suggestion.suggestion.value,
        attempt_count=suggestion.attempt_count,
        fingerprint=suggestion.request_fingerprint,
    )
    if suggestion.suggestion_id != expected_id:
        raise SemanticSuggestionIntegrityError(
            f"Suggestion id {suggestion.suggestion_id!r} is not the content address of "
            f"its own content (expected {expected_id!r}). Refusing to store an "
            "observation under an identifier it did not earn."
        )
    return suggestion


def suggestion_to_row(
    suggestion: SemanticSuggestion,
    *,
    review_queue_id: str,
    schema_version: str,
) -> tuple[Any, ...]:
    """Project a suggestion onto the semantic_suggestions column tuple.

    The denormalized columns exist so the advisory queue can be filtered without
    parsing JSON. ``suggestion_payload_json`` stays authoritative, and
    :func:`row_to_semantic_suggestion` checks the two against each other on the
    way back.
    """
    return (
        review_queue_id,
        suggestion.suggestion_id,
        suggestion.review_case_id,
        suggestion.record_a_id,
        suggestion.record_b_id,
        suggestion.suggestion.value,
        None if suggestion.failure_code is None else suggestion.failure_code.value,
        1 if suggestion.live else 0,
        suggestion.provider,
        suggestion.requested_model,
        canonical_suggestion_json(suggestion),
        suggestion.created_at_utc,
        schema_version,
    )


def row_to_semantic_suggestion(row: Mapping[str, Any]) -> SemanticSuggestion:
    """Rebuild a Sprint 09 suggestion from a stored row."""
    suggestion_id = str(row["suggestion_id"])
    assert_supported_schema_version(str(row["schema_version"]))
    payload = _decode_payload(row["suggestion_payload_json"], suggestion_id)
    suggestion = suggestion_from_payload(payload)
    _assert_row_agrees_with_payload(row, suggestion)
    # The stored id must still be its own content address. A row edited in place
    # is rejected here rather than handed back as a genuine observation.
    return assert_is_sprint_09_suggestion(suggestion)


def suggestion_from_payload(payload: Mapping[str, Any]) -> SemanticSuggestion:
    """Reconstruct a suggestion from its durable payload.

    Every retained field must be present and no unknown key may appear, so a
    payload that has drifted from the Sprint 09 contract fails closed rather
    than rebuilding a suggestion with a default nobody recorded.

    ``explanation`` is the single exception, and it is an exception by design:
    it was never stored, so it comes back empty. The value is not a stand-in for
    lost text -- it records that this layer never retained any.
    """
    keys = set(payload)
    missing = sorted(DURABLE_SUGGESTION_FIELDS - keys)
    extra = sorted(keys - DURABLE_SUGGESTION_FIELDS)
    if missing or extra:
        raise SemanticSuggestionIntegrityError(
            "Stored suggestion payload does not match the durable Sprint 09 "
            f"SemanticSuggestion contract; missing={missing}, unexpected={extra}."
        )
    values = dict(payload)
    values["explanation"] = ABSENT_EXPLANATION
    try:
        values["suggestion"] = SemanticSuggestionType(values["suggestion"])
        values["cost_mode"] = CostMode(values["cost_mode"])
        values["failure_code"] = (
            None if values["failure_code"] is None else SemanticFailureCode(values["failure_code"])
        )
        values["reason_codes"] = tuple(values["reason_codes"])
        return SemanticSuggestion(**values)
    except (TypeError, ValueError) as exc:
        raise SemanticSuggestionIntegrityError(
            f"Stored suggestion {payload.get('suggestion_id')!r} could not be rebuilt: {exc}"
        ) from exc


def _decode_payload(raw: Any, suggestion_id: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise ReviewPersistenceError(
            f"Stored suggestion payload for {suggestion_id} is not valid JSON: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise ReviewPersistenceError(
            f"Stored suggestion payload for {suggestion_id} must be a JSON object."
        )
    return payload


def _assert_row_agrees_with_payload(
    row: Mapping[str, Any],
    suggestion: SemanticSuggestion,
) -> None:
    """Columns and payload are written together, so they must still agree.

    A disagreement means the row was edited outside this code. Returning it
    would hand a caller a suggestion whose advisory value is not the value the
    advisory queue was filtered on.
    """
    expected: dict[str, Any] = {
        "suggestion_id": suggestion.suggestion_id,
        "review_case_id": suggestion.review_case_id,
        "record_a_id": suggestion.record_a_id,
        "record_b_id": suggestion.record_b_id,
        "suggestion": suggestion.suggestion.value,
        "failure_code": (
            None if suggestion.failure_code is None else suggestion.failure_code.value
        ),
        "provider": suggestion.provider,
        "requested_model": suggestion.requested_model,
        "created_at_utc": suggestion.created_at_utc,
    }
    for column, declared in expected.items():
        stored = row[column]
        if stored != declared:
            raise SemanticSuggestionIntegrityError(
                f"Stored suggestion {suggestion.suggestion_id} disagrees with its payload "
                f"on {column}: column={stored!r}, payload={declared!r}."
            )
    if bool(row["live"]) != suggestion.live:
        raise SemanticSuggestionIntegrityError(
            f"Stored suggestion {suggestion.suggestion_id} disagrees with its payload on "
            f"live: column={row['live']!r}, payload={suggestion.live!r}."
        )
