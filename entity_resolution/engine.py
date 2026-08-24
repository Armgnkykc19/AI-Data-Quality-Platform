from __future__ import annotations

from entity_resolution.blocking import (
    candidate_reduction_ratio,
    generate_candidates,
    possible_pair_count,
)
from entity_resolution.candidates import score_and_decide_candidates
from entity_resolution.clustering import build_entity_clusters
from entity_resolution.config import EntityResolutionConfig, load_entity_resolution_config
from entity_resolution.models import (
    EntityRecord,
    MatchDecisionType,
    ResolutionResult,
    ResolutionSummary,
    ReviewItem,
)


def resolve_entities(
    records: list[EntityRecord],
    *,
    source_label: str = "inline",
    config: EntityResolutionConfig | None = None,
) -> ResolutionResult:
    resolution_config = config or load_entity_resolution_config()
    for field_name in resolution_config.forbidden_match_fields:
        for record in records:
            if field_name in record.field_values:
                raise ValueError(
                    f"Forbidden field '{field_name}' must not be present in entity "
                    "resolution inputs."
                )

    sorted_records = sorted(records, key=lambda item: item.record_id)
    records_by_id = {record.record_id: record for record in sorted_records}
    candidates = generate_candidates(sorted_records, config=resolution_config)
    decisions = score_and_decide_candidates(candidates, records_by_id, config=resolution_config)

    review_queue = tuple(
        sorted(
            [
                ReviewItem(
                    pair=decision.pair,
                    score=decision.comparison.score,
                    decision=decision.decision,
                    evidence=decision.comparison.evidence,
                    conflicts=decision.comparison.conflicts,
                    candidate_reasons=decision.comparison.candidate_reasons,
                    reason=decision.reason,
                )
                for decision in decisions
                if decision.decision == MatchDecisionType.REVIEW
            ],
            key=lambda item: (
                -item.score,
                item.pair.record_a_id,
                item.pair.record_b_id,
            ),
        )
    )

    clusters = ()
    conflict_guarded = 0
    if resolution_config.clustering_enabled:
        clusters, conflict_guarded = build_entity_clusters(
            decisions,
            records_by_id,
            config=resolution_config,
        )

    auto_match_count = sum(
        1 for decision in decisions if decision.decision == MatchDecisionType.AUTO_MATCH
    )
    review_count = sum(1 for decision in decisions if decision.decision == MatchDecisionType.REVIEW)
    no_match_count = sum(
        1 for decision in decisions if decision.decision == MatchDecisionType.NO_MATCH
    )

    record_count = len(sorted_records)
    candidate_count = len(candidates)
    summary = ResolutionSummary(
        record_count=record_count,
        possible_pair_count=possible_pair_count(record_count),
        candidate_pair_count=candidate_count,
        candidate_reduction_ratio=candidate_reduction_ratio(
            record_count=record_count,
            candidate_count=candidate_count,
        ),
        auto_match_count=auto_match_count,
        review_count=review_count,
        no_match_count=no_match_count,
        cluster_count=len(clusters),
        conflict_guarded_clusters=conflict_guarded,
    )

    return ResolutionResult(
        source_label=source_label,
        records=tuple(sorted_records),
        candidates=candidates,
        decisions=decisions,
        review_queue=review_queue,
        clusters=clusters,
        summary=summary,
    )
