from __future__ import annotations

from ingestion.models import ParsedDataset
from profiling.models import ColumnProfile, DatasetProfile
from schema_mapping.candidates import generate_candidates
from schema_mapping.config import SchemaMappingConfig, load_schema_mapping_config
from schema_mapping.conflicts import decide_column_mapping, detect_collisions
from schema_mapping.evidence import collect_evidence
from schema_mapping.models import (
    ColumnMapping,
    MappingAlternative,
    MappingCandidate,
    MappingPlan,
    MappingPlanSummary,
)
from schema_mapping.preprocessing import normalize_header
from schema_mapping.scoring import build_candidate


def _profile_by_name(profile: DatasetProfile | None) -> dict[str, ColumnProfile]:
    if profile is None:
        return {}
    return {column.name: column for column in profile.columns}


def build_mapping_plan(
    parsed: ParsedDataset,
    *,
    profile: DatasetProfile | None = None,
    config: SchemaMappingConfig | None = None,
) -> MappingPlan:
    mapping_config = config or load_schema_mapping_config()
    profiles = _profile_by_name(profile)

    column_candidates: dict[str, list[MappingCandidate]] = {}
    for _index, header in enumerate(parsed.headers):
        column_profile = profiles.get(header)
        candidates: list[MappingCandidate] = []
        for canonical_field in generate_candidates(header, mapping_config):
            evidence = collect_evidence(
                header=header,
                canonical_field=canonical_field,
                profile=column_profile,
                config=mapping_config,
            )
            candidate = build_candidate(
                canonical_field,
                evidence,
                config=mapping_config,
                profile_row_count=column_profile.row_count if column_profile else 0,
            )
            if candidate.score > 0:
                candidates.append(candidate)
        candidates.sort(
            key=lambda item: (-item.score, item.canonical_field),
        )
        column_candidates[header] = candidates

    provisional_auto_maps = {
        header: candidates[0].canonical_field
        for header, candidates in column_candidates.items()
        if candidates
        and candidates[0].score >= mapping_config.review_threshold
    }
    collision_map = detect_collisions(provisional_auto_maps)

    mappings: list[ColumnMapping] = []
    for index, header in enumerate(parsed.headers):
        candidates = column_candidates.get(header, [])
        conflicts = (collision_map[header],) if header in collision_map else ()
        decision, canonical_field, score, evidence, reason = decide_column_mapping(
            header=header,
            candidates=candidates,
            config=mapping_config,
            collision_conflicts=conflicts,
        )
        alternatives = tuple(
            MappingAlternative(canonical_field=item.canonical_field, score=item.score)
            for item in candidates[1:4]
        )
        mappings.append(
            ColumnMapping(
                source_column=header,
                source_column_index=index,
                original_header=header,
                normalized_header=normalize_header(header),
                decision=decision,
                canonical_field=canonical_field,
                score=score,
                evidence=evidence,
                alternatives=alternatives,
                conflicts=conflicts,
                reason=reason,
            )
        )

    auto_map_fields = {
        mapping.canonical_field
        for mapping in mappings
        if mapping.decision.value == "AUTO_MAP" and mapping.canonical_field
    }
    missing_fields = tuple(
        field
        for field in mapping_config.mappable_fields
        if field not in auto_map_fields
    )

    summary = MappingPlanSummary(
        auto_map_count=sum(1 for item in mappings if item.decision.value == "AUTO_MAP"),
        review_count=sum(1 for item in mappings if item.decision.value == "REVIEW"),
        unmapped_count=sum(1 for item in mappings if item.decision.value == "UNMAPPED"),
        conflict_count=sum(1 for item in mappings if item.decision.value == "CONFLICT"),
        mapped_canonical_fields=tuple(sorted(auto_map_fields)),
        missing_canonical_fields=missing_fields,
    )

    return MappingPlan(
        source_path=parsed.metadata.path,
        source_headers=tuple(parsed.headers),
        column_mappings=tuple(mappings),
        summary=summary,
    )
