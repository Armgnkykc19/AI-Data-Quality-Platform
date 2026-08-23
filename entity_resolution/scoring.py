from __future__ import annotations

from entity_resolution.config import EntityResolutionConfig
from entity_resolution.models import PairComparison, PairConflict, PairEvidence


def score_pair_comparison(
    *,
    evidence: tuple[PairEvidence, ...],
    conflicts: tuple[PairConflict, ...],
    config: EntityResolutionConfig,
) -> float:
    positive = sum(item.contribution for item in evidence)
    penalties = sum(item.penalty for item in conflicts)
    score = positive - penalties
    if score < 0.0:
        return 0.0
    if score > 1.0:
        return 1.0
    return score


def build_pair_comparison(
    *,
    comparison: PairComparison,
    config: EntityResolutionConfig,
) -> PairComparison:
    score = score_pair_comparison(
        evidence=comparison.evidence,
        conflicts=comparison.conflicts,
        config=config,
    )
    return PairComparison(
        pair=comparison.pair,
        candidate_reasons=comparison.candidate_reasons,
        evidence=comparison.evidence,
        conflicts=comparison.conflicts,
        score=score,
    )
