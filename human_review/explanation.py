from __future__ import annotations

from entity_resolution.config import EntityResolutionConfig
from entity_resolution.models import PairConflict, PairEvidence, ReviewItem


def _supporting_evidence(evidence: tuple[PairEvidence, ...]) -> tuple[PairEvidence, ...]:
    return tuple(item for item in evidence if item.contribution > 0)


def _missing_evidence_notes(
    evidence: tuple[PairEvidence, ...],
    *,
    config: EntityResolutionConfig,
) -> tuple[str, ...]:
    notes: list[str] = []
    present_fields = {item.field_name for item in evidence if item.contribution > 0}
    for field_name in config.identity_fields:
        if (
            field_name in {"first_name", "last_name", "email", "phone"}
            and field_name not in present_fields
        ):
            notes.append(f"No supporting evidence for {field_name}.")
    return tuple(sorted(notes))


def build_machine_readable_reasons(
    item: ReviewItem,
    *,
    config: EntityResolutionConfig,
) -> tuple[str, ...]:
    reasons: list[str] = [f"machine_decision={item.decision.value}", item.reason]
    reasons.extend(
        f"blocking:{reason.reason_type.value}:{reason.blocking_key}"
        for reason in item.candidate_reasons
    )
    reasons.extend(
        f"evidence:{evidence.evidence_type.value}:{evidence.field_name}"
        for evidence in item.evidence
        if evidence.contribution > 0
    )
    reasons.extend(
        f"conflict:{conflict.conflict_type.value}:{conflict.field_name}"
        for conflict in item.conflicts
    )
    if item.score >= config.auto_match_threshold:
        reasons.append("score_meets_auto_match_threshold")
    if item.score >= config.review_threshold:
        reasons.append("score_meets_review_threshold")
    return tuple(reasons)


def build_human_summary(
    item: ReviewItem,
    *,
    config: EntityResolutionConfig,
) -> str:
    supporting = _supporting_evidence(item.evidence)
    conflicts: tuple[PairConflict, ...] = item.conflicts

    support_parts = sorted(
        {
            f"{evidence.field_name} ({evidence.evidence_type.value.replace('_', ' ').lower()})"
            for evidence in supporting
        }
    )
    conflict_parts = sorted(
        {
            f"{conflict.field_name} ({conflict.conflict_type.value.replace('_', ' ').lower()})"
            for conflict in conflicts
        }
    )

    if support_parts and conflict_parts:
        return (
            f"Supporting evidence on {', '.join(support_parts)}, but conflicting "
            f"{', '.join(conflict_parts)} prevents automatic matching."
        )
    if support_parts and not conflict_parts:
        if item.score >= config.auto_match_threshold:
            return (
                f"Score {item.score:.2f} meets the AUTO_MATCH threshold, but {item.reason.lower()}"
            )
        return (
            f"Partial supporting evidence on {', '.join(support_parts)}, but the score "
            f"{item.score:.2f} is below safe AUTO_MATCH confidence."
        )
    if conflict_parts:
        return (
            f"Conflicting {', '.join(conflict_parts)} prevents automatic matching despite "
            f"a review-range score of {item.score:.2f}."
        )
    return (
        f"Ambiguous pair with score {item.score:.2f}; {item.reason} "
        "Manual review is required before merging."
    )
