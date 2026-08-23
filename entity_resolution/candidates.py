from __future__ import annotations

from entity_resolution.config import EntityResolutionConfig
from entity_resolution.decisions import decide_pair_match
from entity_resolution.evidence import collect_pair_conflicts, collect_pair_evidence
from entity_resolution.models import (
    EntityRecord,
    MatchCandidate,
    PairComparison,
)
from entity_resolution.scoring import build_pair_comparison


def compare_candidate_pair(
    candidate: MatchCandidate,
    records_by_id: dict[str, EntityRecord],
    *,
    config: EntityResolutionConfig,
) -> PairComparison:
    left = records_by_id[candidate.pair.record_a_id]
    right = records_by_id[candidate.pair.record_b_id]
    evidence = collect_pair_evidence(left, right, config=config)
    conflicts = collect_pair_conflicts(left, right, config=config)
    comparison = PairComparison(
        pair=candidate.pair,
        candidate_reasons=candidate.reasons,
        evidence=evidence,
        conflicts=conflicts,
        score=0.0,
    )
    return build_pair_comparison(comparison=comparison, config=config)


def score_and_decide_candidates(
    candidates: tuple[MatchCandidate, ...],
    records_by_id: dict[str, EntityRecord],
    *,
    config: EntityResolutionConfig,
):
    decisions = []
    for candidate in candidates:
        comparison = compare_candidate_pair(
            candidate, records_by_id, config=config
        )
        decisions.append(decide_pair_match(comparison, config=config))
    return tuple(decisions)
