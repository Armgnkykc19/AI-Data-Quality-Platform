from __future__ import annotations

from entity_resolution.clustering import _cluster_has_severe_internal_conflict
from entity_resolution.config import EntityResolutionConfig
from entity_resolution.models import (
    EntityRecord,
    MatchDecisionType,
    RecordPair,
    ResolutionResult,
)
from human_review.constraints import assert_human_match_allowed
from human_review.errors import HumanReviewAuthorizationError, HumanReviewContradictionError
from human_review.models import HumanReviewOutcome


def _pair_sort_key(pair: RecordPair) -> tuple[str, str]:
    return pair.record_a_id, pair.record_b_id


def projected_review_component_member_ids(
    *,
    anchor_pair: RecordPair,
    outcome: HumanReviewOutcome,
    resolution: ResolutionResult,
    records_by_id: dict[str, EntityRecord],
    config: EntityResolutionConfig,
    additional_match_pair: RecordPair | None = None,
    force_additional_match: bool = False,
) -> tuple[str, ...]:
    """Return the connected component containing anchor_pair after authorized edges."""
    members = sorted(records_by_id)
    parent = {member: member for member in members}

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
    human_edges = sorted(outcome.resolved_match_pairs(), key=_pair_sort_key)
    if additional_match_pair is not None:
        human_edges = sorted(set(human_edges) | {additional_match_pair}, key=_pair_sort_key)

    for decision in resolution.decisions:
        if decision.decision != MatchDecisionType.AUTO_MATCH:
            continue
        proposed = component_members(decision.pair.record_a_id, decision.pair.record_b_id)
        if any(
            pair.record_a_id in proposed and pair.record_b_id in proposed for pair in no_match_pairs
        ):
            continue
        union(decision.pair.record_a_id, decision.pair.record_b_id)

    for pair in human_edges:
        proposed = component_members(pair.record_a_id, pair.record_b_id)
        force_union = (
            force_additional_match
            and additional_match_pair is not None
            and pair == additional_match_pair
        )
        if not force_union and any(
            existing.record_a_id in proposed and existing.record_b_id in proposed
            for existing in no_match_pairs
        ):
            continue
        union(pair.record_a_id, pair.record_b_id)

    anchor_root = find(anchor_pair.record_a_id)
    return tuple(sorted(member for member in members if find(member) == anchor_root))


def component_has_severe_internal_conflict(
    member_ids: list[str],
    records_by_id: dict[str, EntityRecord],
    *,
    config: EntityResolutionConfig,
) -> tuple[bool, str | None]:
    return _cluster_has_severe_internal_conflict(member_ids, records_by_id, config=config)


def assert_human_match_authorization_boundary(
    *,
    pair: RecordPair,
    outcome: HumanReviewOutcome,
    resolution: ResolutionResult,
    records_by_id: dict[str, EntityRecord],
    config: EntityResolutionConfig,
    existing_match_pairs: frozenset[RecordPair] | None = None,
    existing_no_match_pairs: frozenset[RecordPair] | None = None,
) -> None:
    """Fail closed when a human MATCH would merge a component with severe identity conflict."""
    no_match_pairs = outcome.resolved_no_match_pairs()
    if existing_no_match_pairs:
        no_match_pairs = frozenset(set(no_match_pairs) | set(existing_no_match_pairs))

    assert_human_match_allowed(
        pair=pair,
        outcome=outcome,
        existing_match_pairs=existing_match_pairs,
        existing_no_match_pairs=existing_no_match_pairs,
    )

    projected_members = list(
        projected_review_component_member_ids(
            anchor_pair=pair,
            outcome=outcome,
            resolution=resolution,
            records_by_id=records_by_id,
            config=config,
            additional_match_pair=pair,
            force_additional_match=True,
        )
    )
    if len(projected_members) < 2:
        return

    projected_member_set = set(projected_members)
    for blocked_pair in no_match_pairs:
        if (
            blocked_pair.record_a_id in projected_member_set
            and blocked_pair.record_b_id in projected_member_set
        ):
            raise HumanReviewContradictionError(
                f"Human MATCH on {pair} would transitively violate human NO_MATCH "
                f"constraint for {blocked_pair}."
            )

    has_conflict, description = component_has_severe_internal_conflict(
        projected_members,
        records_by_id,
        config=config,
    )
    if has_conflict:
        raise HumanReviewAuthorizationError(
            "Human MATCH would connect records into a component with an unresolved "
            f"severe identity conflict: {description or 'severe conflict detected'}. "
            f"Reviewed pair {pair} authorizes only that relationship, not transitive "
            "severe contradictions between other records in the component."
        )
