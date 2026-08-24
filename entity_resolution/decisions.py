from __future__ import annotations

from entity_resolution.config import EntityResolutionConfig
from entity_resolution.models import (
    EvidenceType,
    MatchDecision,
    MatchDecisionType,
    PairComparison,
    PairConflict,
    PairEvidence,
)


def has_strong_identity_evidence(
    evidence: tuple[PairEvidence, ...],
    *,
    config: EntityResolutionConfig,
) -> bool:
    for item in evidence:
        if item.evidence_type.value in config.strong_evidence_types:
            return True
    return False


def has_severe_conflict(
    conflicts: tuple[PairConflict, ...],
    *,
    config: EntityResolutionConfig,
) -> bool:
    for conflict in conflicts:
        if conflict.conflict_type.value in config.severe_conflict_types:
            return True
    return False


def is_weak_only_evidence(
    evidence: tuple[PairEvidence, ...],
    *,
    config: EntityResolutionConfig,
) -> bool:
    if not evidence:
        return True
    if has_strong_identity_evidence(evidence, config=config):
        return False
    non_weak = [
        item
        for item in evidence
        if item.evidence_type.value not in config.weak_evidence_types
        and item.evidence_type
        not in {
            EvidenceType.CITY_EXACT,
            EvidenceType.DISTRICT_EXACT,
        }
    ]
    fuzzy_only = all(
        item.evidence_type
        in {
            EvidenceType.FIRST_NAME_SIMILARITY,
            EvidenceType.LAST_NAME_SIMILARITY,
            EvidenceType.COMPANY_SIMILARITY,
            EvidenceType.ADDRESS_SIMILARITY,
            EvidenceType.CITY_EXACT,
            EvidenceType.DISTRICT_EXACT,
        }
        for item in evidence
    )
    return fuzzy_only or not non_weak


def decide_pair_match(
    comparison: PairComparison,
    *,
    config: EntityResolutionConfig,
) -> MatchDecision:
    score = comparison.score
    evidence = comparison.evidence
    conflicts = comparison.conflicts

    if has_severe_conflict(conflicts, config=config):
        if score >= config.review_threshold:
            return MatchDecision(
                pair=comparison.pair,
                comparison=comparison,
                decision=MatchDecisionType.REVIEW,
                reason="Strong identity evidence conflicts with contradictory identifiers.",
            )
        return MatchDecision(
            pair=comparison.pair,
            comparison=comparison,
            decision=MatchDecisionType.NO_MATCH,
            reason="Contradictory identity evidence below review threshold.",
        )

    if score >= config.auto_match_threshold:
        if config.require_strong_identity and not has_strong_identity_evidence(
            evidence, config=config
        ):
            return MatchDecision(
                pair=comparison.pair,
                comparison=comparison,
                decision=MatchDecisionType.REVIEW,
                reason="Score meets AUTO_MATCH threshold but strong identity evidence is missing.",
            )
        if config.weak_only_forces_review and is_weak_only_evidence(evidence, config=config):
            return MatchDecision(
                pair=comparison.pair,
                comparison=comparison,
                decision=MatchDecisionType.REVIEW,
                reason="Only weak or fuzzy evidence present; unsafe for AUTO_MATCH.",
            )
        if config.forbid_severe_conflicts and has_severe_conflict(conflicts, config=config):
            return MatchDecision(
                pair=comparison.pair,
                comparison=comparison,
                decision=MatchDecisionType.REVIEW,
                reason="Severe conflict blocks AUTO_MATCH.",
            )
        return MatchDecision(
            pair=comparison.pair,
            comparison=comparison,
            decision=MatchDecisionType.AUTO_MATCH,
            reason="Strong deterministic identity evidence with no blocking conflict.",
        )

    if score >= config.review_threshold:
        return MatchDecision(
            pair=comparison.pair,
            comparison=comparison,
            decision=MatchDecisionType.REVIEW,
            reason="Plausible duplicate evidence requires human review.",
        )

    return MatchDecision(
        pair=comparison.pair,
        comparison=comparison,
        decision=MatchDecisionType.NO_MATCH,
        reason="Insufficient compatible evidence for duplicate match.",
    )
