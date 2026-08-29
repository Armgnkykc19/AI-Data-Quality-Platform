from __future__ import annotations

from entity_resolution.config import EntityResolutionConfig, load_entity_resolution_config
from entity_resolution.models import EntityCluster, EntityRecord, ResolutionResult
from human_review.integration import build_review_aware_clusters, review_excluded_record_ids
from human_review.models import HumanReviewOutcome, ReviewStatus
from survivorship.config import SurvivorshipConfig, load_survivorship_config
from survivorship.models import (
    CanonicalEntity,
    HumanReviewProvenance,
    SurvivorshipResult,
    SurvivorshipSummary,
)
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


def _human_review_provenance_for_cluster(
    cluster: EntityCluster,
    outcome: HumanReviewOutcome | None,
) -> tuple[HumanReviewProvenance, ...]:
    if outcome is None:
        return ()

    provenance_items: list[HumanReviewProvenance] = []
    member_set = set(cluster.member_record_ids)
    for case in outcome.cases:
        if case.status != ReviewStatus.MATCH or case.resolution is None:
            continue
        if case.pair.record_a_id in member_set and case.pair.record_b_id in member_set:
            provenance_items.append(
                HumanReviewProvenance(
                    review_case_id=case.review_case_id,
                    record_a_id=case.pair.record_a_id,
                    record_b_id=case.pair.record_b_id,
                    machine_decision=case.machine_decision.value,
                    human_decision=case.resolution.human_decision.value,
                    reviewer_id=case.resolution.reviewer_id,
                    resolution_sequence=case.resolution.resolution_sequence,
                    downstream_action=case.resolution.downstream_action,
                )
            )
    provenance_items.sort(key=lambda item: item.review_case_id)
    return tuple(provenance_items)


def build_canonical_entities(
    resolution: ResolutionResult,
    *,
    source_label: str | None = None,
    config: SurvivorshipConfig | None = None,
    entity_resolution_config: EntityResolutionConfig | None = None,
    human_review_outcome: HumanReviewOutcome | None = None,
) -> SurvivorshipResult:
    survivorship_config = config or load_survivorship_config()
    resolution_config = entity_resolution_config or load_entity_resolution_config()

    for field_name in survivorship_config.forbidden_fields:
        for record in resolution.records:
            if field_name in record.field_values:
                raise ValueError(
                    f"Forbidden field '{field_name}' must not be present in survivorship inputs."
                )

    records_by_id = {record.record_id: record for record in resolution.records}
    review_record_ids = review_excluded_record_ids(resolution, human_review_outcome)
    effective_clusters = build_review_aware_clusters(
        resolution,
        human_review_outcome,
        config=resolution_config,
        records_by_id=records_by_id,
    )

    entities: list[CanonicalEntity] = []
    clustered_record_ids: set[str] = set()
    preserved_conflict_count = 0

    for cluster in effective_clusters:
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
        human_provenance = _human_review_provenance_for_cluster(cluster, human_review_outcome)

        entity_id = (
            f"CE-{cluster.cluster_id}"
            if cluster.cluster_id.startswith("HR-")
            else f"CE-{cluster.cluster_id}"
        )
        entities.append(
            CanonicalEntity(
                entity_id=entity_id,
                cluster_id=cluster.cluster_id,
                member_record_ids=cluster.member_record_ids,
                field_values=field_values,
                provenance=provenance,
                preserved_conflicts=conflicts,
                has_unresolved_conflicts=bool(conflicts) or cluster.has_internal_conflict,
                has_cluster_internal_conflict=cluster.has_internal_conflict,
                human_review_provenance=human_provenance,
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
        cluster_count=len(effective_clusters),
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
