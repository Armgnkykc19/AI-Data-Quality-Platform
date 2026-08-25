from __future__ import annotations

from entity_resolution.config import EntityResolutionConfig, load_entity_resolution_config
from entity_resolution.models import MatchDecisionType, ResolutionResult
from human_review.explanation import (
    _missing_evidence_notes,
    build_human_summary,
    build_machine_readable_reasons,
)
from human_review.ids import stable_review_case_id
from human_review.models import (
    ReviewBlockingReason,
    ReviewCase,
    ReviewConflictEvidence,
    ReviewEvidence,
    ReviewStatus,
    ReviewWorkflowState,
)


def generate_review_cases(
    resolution: ResolutionResult,
    *,
    config: EntityResolutionConfig | None = None,
) -> ReviewWorkflowState:
    review_config = config or load_entity_resolution_config()
    cases: list[ReviewCase] = []

    for item in resolution.review_queue:
        if item.decision != MatchDecisionType.REVIEW:
            continue

        supporting = tuple(
            ReviewEvidence.from_pair_evidence(evidence)
            for evidence in item.evidence
            if evidence.contribution > 0
        )
        conflicting = tuple(
            ReviewConflictEvidence.from_pair_conflict(conflict) for conflict in item.conflicts
        )
        blocking = tuple(
            ReviewBlockingReason.from_candidate_reason(reason) for reason in item.candidate_reasons
        )
        missing_notes = _missing_evidence_notes(item.evidence, config=review_config)

        cases.append(
            ReviewCase(
                review_case_id=stable_review_case_id(item.pair),
                pair=item.pair,
                record_ids=(item.pair.record_a_id, item.pair.record_b_id),
                machine_decision=MatchDecisionType.REVIEW,
                machine_score=item.score,
                auto_match_threshold=review_config.auto_match_threshold,
                review_threshold=review_config.review_threshold,
                machine_reason=item.reason,
                blocking_reasons=blocking,
                supporting_evidence=supporting,
                conflicting_evidence=conflicting,
                missing_evidence_notes=missing_notes,
                machine_readable_reasons=build_machine_readable_reasons(item, config=review_config),
                human_summary=build_human_summary(item, config=review_config),
                status=ReviewStatus.PENDING,
                resolution=None,
            )
        )

    cases.sort(key=lambda case: case.review_case_id)
    return ReviewWorkflowState(cases=tuple(cases), audit_trail=(), next_resolution_sequence=1)
