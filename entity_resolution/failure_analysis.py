from __future__ import annotations

from entity_resolution.models import FailureKind, MatchDecision, MatchDecisionType


def classify_pair_failure(
    *,
    expected_match: bool,
    decision: MatchDecision | None,
    candidate_generated: bool,
) -> FailureKind | None:
    if expected_match and not candidate_generated:
        return FailureKind.CANDIDATE_MISS

    if decision is None:
        if expected_match:
            return FailureKind.CANDIDATE_MISS
        return None

    actual = decision.decision
    if expected_match:
        if actual == MatchDecisionType.NO_MATCH:
            if candidate_generated:
                return FailureKind.MISSED_DUPLICATE
            return FailureKind.CANDIDATE_MISS
        if actual == MatchDecisionType.REVIEW:
            return FailureKind.WRONG_REVIEW_ROUTING
        if actual == MatchDecisionType.AUTO_MATCH:
            return None
    else:
        if actual == MatchDecisionType.AUTO_MATCH:
            return FailureKind.FALSE_AUTO_MATCH
        if actual == MatchDecisionType.REVIEW:
            return None
    return None
