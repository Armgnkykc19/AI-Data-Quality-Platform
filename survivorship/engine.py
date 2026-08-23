from __future__ import annotations

from entity_resolution.models import EntityCluster, EntityRecord, ResolutionResult
from survivorship.config import SurvivorshipConfig, load_survivorship_config
from survivorship.models import CanonicalEntity, SurvivorshipResult, SurvivorshipSummary
from survivorship.rules import apply_field_survivorship


def _records_for_cluster(
    cluster: EntityCluster,
    records_by_id: dict[str, EntityRecord],
) -> list[EntityRecord]:
    return [
        records_by_id[record_id]
        for record_id in sorted(cluster.member_record_ids)
        if record_id in records_by_id
    ]


def build_canonical_entities(
    resolution: ResolutionResult,
    *,
    source_label: str | None = None,
    config: SurvivorshipConfig | None = None,
) -> SurvivorshipResult:
    survivorship_config = config or load_survivorship_config()

    for field_name in survivorship_config.forbidden_fields:
        for record in resolution.records:
            if field_name in record.field_values:
                raise ValueError(
                    f"Forbidden field '{field_name}' must not be present in survivorship inputs."
                )

    records_by_id = {record.record_id: record for record in resolution.records}
    review_record_ids = {
        record_id
        for item in resolution.review_queue
        for record_id in (item.pair.record_a_id, item.pair.record_b_id)
    }

    entities: list[CanonicalEntity] = []
    clustered_record_ids: set[str] = set()
    preserved_conflict_count = 0

    for cluster in resolution.clusters:
        if (
            survivorship_config.skip_clusters_with_internal_conflict
            and cluster.has_internal_conflict
        ):
            continue

        member_records = _records_for_cluster(cluster, records_by_id)
        if len(member_records) < 2:
            continue

        if any(record_id in review_record_ids for record_id in cluster.member_record_ids):
            continue

        field_values, provenance, conflicts = apply_field_survivorship(
            member_records,
            config=survivorship_config,
        )
        preserved_conflict_count += len(conflicts)
        clustered_record_ids.update(cluster.member_record_ids)

        entities.append(
            CanonicalEntity(
                entity_id=f"CE-{cluster.cluster_id}",
                cluster_id=cluster.cluster_id,
                member_record_ids=cluster.member_record_ids,
                field_values=field_values,
                provenance=provenance,
                preserved_conflicts=conflicts,
                has_unresolved_conflicts=bool(conflicts) or cluster.has_internal_conflict,
                has_cluster_internal_conflict=cluster.has_internal_conflict,
            )
        )

    singleton_count = 0
    if survivorship_config.build_singleton_entities:
        for record in sorted(resolution.records, key=lambda item: item.record_id):
            if record.record_id in clustered_record_ids:
                continue
            if record.record_id in review_record_ids:
                continue
            field_values, provenance, conflicts = apply_field_survivorship(
                [record],
                config=survivorship_config,
            )
            preserved_conflict_count += len(conflicts)
            singleton_count += 1
            entities.append(
                CanonicalEntity(
                    entity_id=f"CE-S-{record.record_id}",
                    cluster_id=None,
                    member_record_ids=(record.record_id,),
                    field_values=field_values,
                    provenance=provenance,
                    preserved_conflicts=conflicts,
                    has_unresolved_conflicts=bool(conflicts),
                    has_cluster_internal_conflict=False,
                )
            )

    entities.sort(key=lambda item: item.entity_id)
    merged_count = sum(1 for entity in entities if len(entity.member_record_ids) > 1)

    summary = SurvivorshipSummary(
        input_record_count=len(resolution.records),
        cluster_count=len(resolution.clusters),
        canonical_entity_count=len(entities),
        singleton_entity_count=singleton_count,
        merged_entity_count=merged_count,
        preserved_conflict_count=preserved_conflict_count,
        review_excluded_record_count=len(review_record_ids),
    )

    return SurvivorshipResult(
        source_label=source_label or resolution.source_label,
        entities=tuple(entities),
        review_excluded_record_ids=tuple(sorted(review_record_ids)),
        summary=summary,
    )
