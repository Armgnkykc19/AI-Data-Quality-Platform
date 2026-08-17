from __future__ import annotations

from enum import StrEnum


class MappingFailureKind(StrEnum):
    LEXICAL_FALSE_POSITIVE = "LEXICAL_FALSE_POSITIVE"
    PATTERN_FALSE_POSITIVE = "PATTERN_FALSE_POSITIVE"
    TYPE_CONFLICT = "TYPE_CONFLICT"
    HEADER_PATTERN_CONFLICT = "HEADER_PATTERN_CONFLICT"
    AMBIGUOUS_ALIAS = "AMBIGUOUS_ALIAS"
    COLLISION = "COLLISION"
    THRESHOLD_ERROR = "THRESHOLD_ERROR"
    MISSING_EVIDENCE = "MISSING_EVIDENCE"
    FALSE_AUTO_MAP = "FALSE_AUTO_MAP"
    MISSED_MAPPING = "MISSED_MAPPING"
    FALSE_REVIEW = "FALSE_REVIEW"
    FALSE_UNMAPPED = "FALSE_UNMAPPED"


def classify_failure(
    *,
    expected_field: str | None,
    expected_decision: str | None,
    actual_field: str | None,
    actual_decision: str,
) -> MappingFailureKind | None:
    if expected_decision == "UNMAPPED":
        if actual_decision == "AUTO_MAP":
            return MappingFailureKind.FALSE_AUTO_MAP
        if actual_decision not in {"UNMAPPED", "REVIEW"}:
            return MappingFailureKind.FALSE_UNMAPPED
        return None

    if expected_decision == "REVIEW":
        if actual_decision == "AUTO_MAP":
            return MappingFailureKind.FALSE_AUTO_MAP
        if actual_decision == "UNMAPPED":
            return MappingFailureKind.FALSE_UNMAPPED
        return None

    if expected_field and actual_decision == "AUTO_MAP" and actual_field != expected_field:
        return MappingFailureKind.FALSE_AUTO_MAP

    if expected_field and actual_decision in {"REVIEW", "UNMAPPED", "CONFLICT"}:
        if actual_decision == "REVIEW" and expected_decision == "AUTO_MAP":
            return MappingFailureKind.FALSE_REVIEW
        if actual_decision == "UNMAPPED":
            return MappingFailureKind.MISSED_MAPPING
        if actual_decision == "CONFLICT":
            return MappingFailureKind.COLLISION
        return MappingFailureKind.MISSED_MAPPING

    return None
