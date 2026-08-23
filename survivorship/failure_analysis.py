from __future__ import annotations

from survivorship.models import FailureKind, PreservedFieldConflict


def classify_merge_coherence_failure(
    *,
    person_id: str,
    record_ids: tuple[str, ...],
    entity_ids: tuple[str, ...],
) -> FailureKind | None:
    if len(record_ids) < 2:
        return None
    distinct_entities = set(entity_ids)
    if len(distinct_entities) <= 1:
        return None
    return FailureKind.SPLIT_ENTITY


def classify_field_mismatch(
    *,
    field_name: str,
    expected_normalized: str | None,
    actual_normalized: str | None,
) -> FailureKind | None:
    if expected_normalized == actual_normalized:
        return None
    return FailureKind.FIELD_MISMATCH


def classify_conflict_preservation_failure(
    *,
    field_name: str,
    normalized_values: tuple[str, ...],
    preserved_conflicts: tuple[PreservedFieldConflict, ...],
) -> FailureKind | None:
    if len(normalized_values) <= 1:
        return None
    preserved_fields = {item.field_name for item in preserved_conflicts}
    if field_name in preserved_fields:
        return None
    return FailureKind.CONFLICT_NOT_PRESERVED
