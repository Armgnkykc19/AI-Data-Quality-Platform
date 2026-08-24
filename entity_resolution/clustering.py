from __future__ import annotations

from entity_resolution.candidates import compare_candidate_pair
from entity_resolution.config import EntityResolutionConfig
from entity_resolution.evidence import collect_pair_conflicts
from entity_resolution.models import (
    EntityCluster,
    EntityRecord,
    MatchCandidate,
    MatchDecision,
    MatchDecisionType,
    RecordPair,
)


class _UnionFind:
    def __init__(self, members: list[str]) -> None:
        self.parent = {member: member for member in members}

    def find(self, node: str) -> str:
        if self.parent[node] != node:
            self.parent[node] = self.find(self.parent[node])
        return self.parent[node]

    def union(self, left: str, right: str) -> bool:
        root_left = self.find(left)
        root_right = self.find(right)
        if root_left == root_right:
            return False
        if root_left < root_right:
            self.parent[root_right] = root_left
        else:
            self.parent[root_left] = root_right
        return True


def _cluster_has_severe_internal_conflict(
    member_ids: list[str],
    records_by_id: dict[str, EntityRecord],
    *,
    config: EntityResolutionConfig,
) -> tuple[bool, str | None]:
    for left_index in range(len(member_ids)):
        for right_index in range(left_index + 1, len(member_ids)):
            left = records_by_id[member_ids[left_index]]
            right = records_by_id[member_ids[right_index]]
            conflicts = collect_pair_conflicts(left, right, config=config)
            for conflict in conflicts:
                if conflict.conflict_type.value in config.severe_conflict_types:
                    return True, conflict.description
    return False, None


def build_entity_clusters(
    decisions: tuple[MatchDecision, ...],
    records_by_id: dict[str, EntityRecord],
    *,
    config: EntityResolutionConfig,
) -> tuple[tuple[EntityCluster, ...], int]:
    auto_edges = [
        decision for decision in decisions if decision.decision == MatchDecisionType.AUTO_MATCH
    ]
    if not auto_edges:
        return (), 0

    members = sorted(records_by_id)
    union_find = _UnionFind(members)
    accepted_edges: list[RecordPair] = []
    conflict_guarded = 0

    for decision in sorted(
        auto_edges,
        key=lambda item: (
            -item.comparison.score,
            item.pair.record_a_id,
            item.pair.record_b_id,
        ),
    ):
        left_root = union_find.find(decision.pair.record_a_id)
        right_root = union_find.find(decision.pair.record_b_id)
        if left_root == right_root:
            accepted_edges.append(decision.pair)
            continue

        proposed_members = sorted(
            member for member in members if union_find.find(member) in {left_root, right_root}
        )
        if config.cluster_conflict_guard:
            has_conflict, description = _cluster_has_severe_internal_conflict(
                proposed_members,
                records_by_id,
                config=config,
            )
            if has_conflict:
                conflict_guarded += 1
                continue

        union_find.union(decision.pair.record_a_id, decision.pair.record_b_id)
        accepted_edges.append(decision.pair)

    components: dict[str, list[str]] = {}
    for member in members:
        root = union_find.find(member)
        components.setdefault(root, []).append(member)

    clusters: list[EntityCluster] = []
    for cluster_index, member_ids in enumerate(
        sorted(components.values(), key=lambda values: values[0]),
        start=1,
    ):
        sorted_members = tuple(sorted(member_ids))
        if len(sorted_members) < 2:
            continue
        has_conflict, description = _cluster_has_severe_internal_conflict(
            list(sorted_members),
            records_by_id,
            config=config,
        )
        cluster_edges = tuple(
            edge
            for edge in accepted_edges
            if edge.record_a_id in sorted_members and edge.record_b_id in sorted_members
        )
        clusters.append(
            EntityCluster(
                cluster_id=f"C-{cluster_index:04d}",
                member_record_ids=sorted_members,
                auto_match_edges=cluster_edges,
                has_internal_conflict=has_conflict,
                conflict_description=description,
            )
        )

    return tuple(clusters), conflict_guarded


def inspect_pair(
    left: EntityRecord,
    right: EntityRecord,
    *,
    config: EntityResolutionConfig,
    reasons: tuple = (),
) -> MatchDecision:
    from entity_resolution.decisions import decide_pair_match

    candidate = MatchCandidate(
        pair=RecordPair.ordered(left.record_id, right.record_id),
        reasons=reasons,
    )
    comparison = compare_candidate_pair(
        candidate,
        {left.record_id: left, right.record_id: right},
        config=config,
    )
    return decide_pair_match(comparison, config=config)
