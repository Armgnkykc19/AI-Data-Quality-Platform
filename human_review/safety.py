from __future__ import annotations

from entity_resolution.models import RecordPair
from human_review.models import HumanReviewOutcome, ReviewStatus
from survivorship.models import SurvivorshipResult


def count_unresolved_unsafe_merges(
    result: SurvivorshipResult,
    unresolved_record_ids: frozenset[str],
) -> int:
    violations = 0
    for entity in result.entities:
        if len(entity.member_record_ids) < 2:
            continue
        if any(record_id in unresolved_record_ids for record_id in entity.member_record_ids):
            violations += 1
    return violations


def count_no_match_transitive_merges(
    result: SurvivorshipResult,
    no_match_pairs: frozenset[RecordPair],
) -> int:
    violations = 0
    for entity in result.entities:
        if len(entity.member_record_ids) < 2:
            continue
        members = set(entity.member_record_ids)
        if any(
            pair.record_a_id in members and pair.record_b_id in members for pair in no_match_pairs
        ):
            violations += 1
    return violations


def count_human_match_without_provenance(
    result: SurvivorshipResult,
    outcome: HumanReviewOutcome | None,
) -> int:
    if outcome is None:
        return 0
    match_pairs = outcome.resolved_match_pairs()
    if not match_pairs:
        return 0

    violations = 0
    for entity in result.entities:
        if len(entity.member_record_ids) < 2:
            continue
        members = set(entity.member_record_ids)
        covered = {
            RecordPair.ordered(item.record_a_id, item.record_b_id)
            for item in entity.human_review_provenance
            if item.human_decision == ReviewStatus.MATCH.value
        }
        required = {
            pair
            for pair in match_pairs
            if pair.record_a_id in members and pair.record_b_id in members
        }
        if required and not required.issubset(covered):
            violations += 1
    return violations


def count_severe_conflict_merges(result: SurvivorshipResult) -> int:
    return sum(
        1
        for entity in result.entities
        if len(entity.member_record_ids) >= 2 and entity.has_cluster_internal_conflict
    )
