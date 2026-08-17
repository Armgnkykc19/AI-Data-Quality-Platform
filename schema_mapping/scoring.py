from __future__ import annotations

from schema_mapping.config import SchemaMappingConfig
from schema_mapping.models import EvidenceType, MappingCandidate, MappingEvidence


def score_candidate(
    evidence: list[MappingEvidence],
    *,
    canonical_field: str,
    config: SchemaMappingConfig,
    profile_row_count: int = 0,
) -> float:
    total = sum(item.contribution for item in evidence)
    if profile_row_count >= 2:
        for item in evidence:
            if (
                item.evidence_type == EvidenceType.PATTERN_EMAIL
                and canonical_field == "email"
                and item.value >= config.pattern_auto_map_support
            ):
                total = max(total, config.auto_map_threshold)
            if (
                item.evidence_type == EvidenceType.PATTERN_PHONE
                and canonical_field == "phone"
                and item.value >= config.pattern_auto_map_support
            ):
                total = max(total, config.auto_map_threshold)
    return round(min(max(total, 0.0), 1.0), 6)


def build_candidate(
    canonical_field: str,
    evidence: list[MappingEvidence],
    *,
    config: SchemaMappingConfig,
    profile_row_count: int = 0,
) -> MappingCandidate:
    return MappingCandidate(
        canonical_field=canonical_field,
        score=score_candidate(
            evidence,
            canonical_field=canonical_field,
            config=config,
            profile_row_count=profile_row_count,
        ),
        evidence=tuple(evidence),
    )
