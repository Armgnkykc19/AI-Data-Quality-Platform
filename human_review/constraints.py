from __future__ import annotations

from entity_resolution.models import EntityRecord, RecordPair
from human_review.errors import HumanReviewContradictionError
from human_review.models import HumanReviewOutcome


class _UnionFind:
    def __init__(self, members: list[str]) -> None:
        self.parent = {member: member for member in members}

    def find(self, node: str) -> str:
        if self.parent[node] != node:
            self.parent[node] = self.find(self.parent[node])
        return self.parent[node]

    def union(self, left: str, right: str) -> None:
        root_left = self.find(left)
        root_right = self.find(right)
        if root_left == root_right:
            return
        if root_left < root_right:
            self.parent[root_right] = root_left
        else:
            self.parent[root_left] = root_right

    def members_of(self, node: str) -> set[str]:
        root = self.find(node)
        return {member for member in self.parent if self.find(member) == root}


def _pair_key(pair: RecordPair) -> tuple[str, str]:
    return pair.record_a_id, pair.record_b_id


def _no_match_blocks_merge(
    left_root_members: set[str],
    right_root_members: set[str],
    no_match_pairs: frozenset[RecordPair],
) -> bool:
    proposed = left_root_members | right_root_members
    for pair in no_match_pairs:
        if pair.record_a_id in proposed and pair.record_b_id in proposed:
            return True
    return False


def assert_human_match_allowed(
    *,
    pair: RecordPair,
    outcome: HumanReviewOutcome,
    existing_match_pairs: frozenset[RecordPair] | None = None,
    existing_no_match_pairs: frozenset[RecordPair] | None = None,
) -> None:
    no_match_pairs = outcome.resolved_no_match_pairs()
    if existing_no_match_pairs:
        no_match_pairs = frozenset(set(no_match_pairs) | set(existing_no_match_pairs))
    if pair in no_match_pairs:
        raise HumanReviewContradictionError(
            f"Human MATCH contradicts existing NO_MATCH constraint for {pair}."
        )

    match_pairs = set(existing_match_pairs or outcome.resolved_match_pairs())
    match_pairs.add(pair)
    members = sorted(
        {
            record_id
            for matched in match_pairs
            for record_id in (matched.record_a_id, matched.record_b_id)
        }
    )
    uf = _UnionFind(members)

    for matched in sorted(match_pairs, key=_pair_key):
        left_members = uf.members_of(matched.record_a_id)
        right_members = uf.members_of(matched.record_b_id)
        if _no_match_blocks_merge(left_members, right_members, no_match_pairs):
            raise HumanReviewContradictionError(
                f"Human MATCH on {matched} would transitively violate a NO_MATCH constraint."
            )
        uf.union(matched.record_a_id, matched.record_b_id)


def build_human_match_components(
    outcome: HumanReviewOutcome,
    records_by_id: dict[str, EntityRecord],
) -> tuple[tuple[str, ...], ...]:
    match_pairs = outcome.resolved_match_pairs()
    if not match_pairs:
        return ()

    members = sorted(records_by_id)
    uf = _UnionFind(members)
    no_match_pairs = outcome.resolved_no_match_pairs()

    for pair in sorted(match_pairs, key=_pair_key):
        left_members = uf.members_of(pair.record_a_id)
        right_members = uf.members_of(pair.record_b_id)
        if _no_match_blocks_merge(left_members, right_members, no_match_pairs):
            raise HumanReviewContradictionError(
                f"Human MATCH component would violate NO_MATCH constraint for {pair}."
            )
        uf.union(pair.record_a_id, pair.record_b_id)

    components: dict[str, list[str]] = {}
    for member in members:
        if member not in {record_id for pair in match_pairs for record_id in pair}:
            continue
        root = uf.find(member)
        components.setdefault(root, []).append(member)

    return tuple(
        tuple(sorted(member_ids))
        for member_ids in sorted(components.values(), key=lambda values: values[0])
        if len(member_ids) >= 2
    )
