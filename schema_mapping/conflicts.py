from __future__ import annotations

from schema_mapping.config import SchemaMappingConfig
from schema_mapping.models import (
    EvidenceType,
    MappingCandidate,
    MappingConflict,
    MappingDecisionType,
    MappingEvidence,
)
from schema_mapping.preprocessing import normalize_header


def has_exact_alias_evidence(evidence: tuple[MappingEvidence, ...]) -> bool:
    return any(item.evidence_type == EvidenceType.EXACT_ALIAS for item in evidence)


def has_strong_pattern_evidence(
    evidence: tuple[MappingEvidence, ...],
    *,
    config: SchemaMappingConfig,
) -> bool:
    for item in evidence:
        if item.evidence_type in {EvidenceType.PATTERN_EMAIL, EvidenceType.PATTERN_PHONE}:
            if item.value >= config.pattern_auto_map_support:
                return True
    return False


def is_lexical_only(evidence: tuple[MappingEvidence, ...]) -> bool:
    strong_types = {
        EvidenceType.EXACT_ALIAS,
        EvidenceType.PATTERN_EMAIL,
        EvidenceType.PATTERN_PHONE,
    }
    if any(item.evidence_type in strong_types for item in evidence):
        return False
    return any(item.evidence_type == EvidenceType.LEXICAL_SIMILARITY for item in evidence)


def has_type_or_pattern_conflict(
    evidence: tuple[MappingEvidence, ...],
    canonical_field: str,
) -> bool:
    has_strong_phone_header = canonical_field == "phone" and has_exact_alias_evidence(evidence)
    numeric_penalty = any(
        item.evidence_type == EvidenceType.PATTERN_NUMERIC and item.value >= 0.95
        for item in evidence
    )
    if has_strong_phone_header and numeric_penalty:
        return True
    return False


def decide_column_mapping(
    *,
    header: str,
    candidates: list[MappingCandidate],
    config: SchemaMappingConfig,
    collision_conflicts: tuple[MappingConflict, ...],
) -> tuple[MappingDecisionType, str | None, float, tuple[MappingEvidence, ...], str]:
    normalized = normalize_header(header)

    if not candidates:
        return (
            MappingDecisionType.UNMAPPED,
            None,
            0.0,
            (),
            "No canonical candidate met minimum evidence requirements.",
        )

    top = candidates[0]
    second_score = candidates[1].score if len(candidates) > 1 else 0.0
    margin = top.score - second_score

    if collision_conflicts:
        return (
            MappingDecisionType.CONFLICT,
            top.canonical_field,
            top.score,
            top.evidence,
            collision_conflicts[0].message,
        )

    if has_type_or_pattern_conflict(top.evidence, top.canonical_field or ""):
        if top.score >= config.review_threshold:
            return (
                MappingDecisionType.REVIEW,
                top.canonical_field,
                top.score,
                top.evidence,
                "Header and value-pattern evidence conflict; requires review.",
            )

    if top.score < config.review_threshold:
        if normalized in config.ambiguous_headers and top.score >= 0.35:
            return (
                MappingDecisionType.REVIEW,
                top.canonical_field,
                top.score,
                top.evidence,
                "Ambiguous header with weak but non-zero evidence; routed to review.",
            )
        return (
            MappingDecisionType.UNMAPPED,
            None,
            top.score,
            top.evidence,
            "Top candidate score below review threshold.",
        )

    ambiguous_header = normalized in config.ambiguous_headers
    if ambiguous_header and is_lexical_only(top.evidence):
        if top.score >= config.review_threshold:
            return (
                MappingDecisionType.REVIEW,
                top.canonical_field,
                top.score,
                top.evidence,
                "Ambiguous header; lexical-only evidence cannot AUTO_MAP.",
            )

    if margin < config.ambiguity_margin and top.score >= config.review_threshold:
        return (
            MappingDecisionType.REVIEW,
            top.canonical_field,
            top.score,
            top.evidence,
            "Top candidates are too close; ambiguity margin not satisfied.",
        )

    if top.score >= config.auto_map_threshold:
        if ambiguous_header and not (
            has_exact_alias_evidence(top.evidence)
            or has_strong_pattern_evidence(top.evidence, config=config)
        ):
            return (
                MappingDecisionType.REVIEW,
                top.canonical_field,
                top.score,
                top.evidence,
                "Ambiguous header requires alias or strong pattern evidence for AUTO_MAP.",
            )
        if is_lexical_only(top.evidence):
            return (
                MappingDecisionType.REVIEW,
                top.canonical_field,
                top.score,
                top.evidence,
                "Lexical similarity alone cannot authorize AUTO_MAP.",
            )
        return (
            MappingDecisionType.AUTO_MAP,
            top.canonical_field,
            top.score,
            top.evidence,
            "Strong deterministic evidence with no blocking ambiguity.",
        )

    return (
        MappingDecisionType.REVIEW,
        top.canonical_field,
        top.score,
        top.evidence,
        "Plausible mapping requires human review before application.",
    )


def detect_collisions(
    provisional_auto_maps: dict[str, str],
) -> dict[str, MappingConflict]:
    canonical_to_columns: dict[str, list[str]] = {}
    for source_column, canonical_field in provisional_auto_maps.items():
        canonical_to_columns.setdefault(canonical_field, []).append(source_column)

    conflicts: dict[str, MappingConflict] = {}
    for canonical_field, columns in canonical_to_columns.items():
        if len(columns) <= 1:
            continue
        for column in columns:
            conflicts[column] = MappingConflict(
                conflict_type="ONE_TO_ONE_COLLISION",
                message=(
                    f"Multiple source columns map to canonical field '{canonical_field}': "
                    f"{', '.join(sorted(columns))}."
                ),
                related_columns=tuple(sorted(columns)),
            )
    return conflicts
