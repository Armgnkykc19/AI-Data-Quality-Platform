from __future__ import annotations

from entity_resolution.clustering import _cluster_has_severe_internal_conflict
from entity_resolution.config import EntityResolutionConfig
from entity_resolution.models import (
    EntityCluster,
    EntityRecord,
    MatchDecisionType,
    RecordPair,
    ResolutionResult,
)
from human_review.models import HumanReviewOutcome, ReviewStatus


def review_excluded_record_ids(
    resolution: ResolutionResult,
    outcome: HumanReviewOutcome | None,
) -> frozenset[str]:
    excluded: set[str] = set()
    resolved_pairs: dict[RecordPair, ReviewStatus] = {}
    if outcome is not None:
        for case in outcome.cases:
            resolved_pairs[case.pair] = case.status

    for item in resolution.review_queue:
        status = resolved_pairs.get(item.pair, ReviewStatus.PENDING)
        if status in {ReviewStatus.PENDING, ReviewStatus.DEFERRED}:
            excluded.add(item.pair.record_a_id)
            excluded.add(item.pair.record_b_id)
    return frozenset(excluded)


def build_review_aware_clusters(
    resolution: ResolutionResult,
    outcome: HumanReviewOutcome | None,
    *,
    config: EntityResolutionConfig,
    records_by_id: dict[str, EntityRecord],
) -> tuple[EntityCluster, ...]:
    if outcome is None:
        from entity_resolution.clustering import build_entity_clusters

        clusters, _ = build_entity_clusters(resolution.decisions, records_by_id, config=config)
        return clusters

    members = sorted(records_by_id)
    parent = {member: member for member in members}
    auto_edges: list[RecordPair] = []
    human_edges = sorted(
        outcome.resolved_match_pairs(), key=lambda pair: (pair.record_a_id, pair.record_b_id)
    )

    def find(node: str) -> str:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    def component_members(left: str, right: str) -> set[str]:
        left_root = find(left)
        right_root = find(right)
        return {member for member in members if find(member) in {left_root, right_root}}

    def union(left: str, right: str) -> None:
        root_left = find(left)
        root_right = find(right)
        if root_left == root_right:
            return
        if root_left < root_right:
            parent[root_right] = root_left
        else:
            parent[root_left] = root_right

    no_match_pairs = outcome.resolved_no_match_pairs()

    for decision in resolution.decisions:
        if decision.decision != MatchDecisionType.AUTO_MATCH:
            continue
        proposed = component_members(decision.pair.record_a_id, decision.pair.record_b_id)
        if any(
            pair.record_a_id in proposed and pair.record_b_id in proposed for pair in no_match_pairs
        ):
            continue
        if config.cluster_conflict_guard:
            has_conflict, _ = _cluster_has_severe_internal_conflict(
                sorted(proposed),
                records_by_id,
                config=config,
            )
            if has_conflict:
                continue
        union(decision.pair.record_a_id, decision.pair.record_b_id)
        auto_edges.append(decision.pair)

    for pair in human_edges:
        proposed = component_members(pair.record_a_id, pair.record_b_id)
        if any(
            existing.record_a_id in proposed and existing.record_b_id in proposed
            for existing in no_match_pairs
        ):
            continue
        union(pair.record_a_id, pair.record_b_id)

    components: dict[str, list[str]] = {}
    for member in members:
        root = find(member)
        components.setdefault(root, []).append(member)

    clusters: list[EntityCluster] = []
    auto_index = 0
    human_index = 0
    for member_ids in sorted(components.values(), key=lambda values: values[0]):
        sorted_members = tuple(sorted(member_ids))
        if len(sorted_members) < 2:
            continue

        component_auto_edges = tuple(
            edge
            for edge in auto_edges
            if edge.record_a_id in sorted_members and edge.record_b_id in sorted_members
        )
        component_human_edges = tuple(
            edge
            for edge in human_edges
            if edge.record_a_id in sorted_members and edge.record_b_id in sorted_members
        )

        if component_auto_edges and component_human_edges:
            auto_index += 1
            cluster_id = f"C-{auto_index:04d}"
        elif component_auto_edges:
            auto_index += 1
            cluster_id = f"C-{auto_index:04d}"
        elif component_human_edges:
            human_index += 1
            cluster_id = f"HR-{human_index:04d}"
        else:
            continue

        has_conflict, description = _cluster_has_severe_internal_conflict(
            list(sorted_members),
            records_by_id,
            config=config,
        )
        clusters.append(
            EntityCluster(
                cluster_id=cluster_id,
                member_record_ids=sorted_members,
                auto_match_edges=component_auto_edges,
                has_internal_conflict=has_conflict,
                conflict_description=description,
            )
        )

    return tuple(clusters)
