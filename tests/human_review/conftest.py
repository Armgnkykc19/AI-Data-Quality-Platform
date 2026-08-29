from __future__ import annotations

import pytest

from entity_resolution.config import EntityResolutionConfig, load_entity_resolution_config
from entity_resolution.models import (
    BlockingReasonType,
    CandidateReason,
    EntityRecord,
    EvidenceType,
    MatchCandidate,
    MatchDecision,
    MatchDecisionType,
    PairComparison,
    PairConflict,
    PairEvidence,
    RecordPair,
    ResolutionResult,
    ResolutionSummary,
    ReviewItem,
)


@pytest.fixture
def resolution_config() -> EntityResolutionConfig:
    return load_entity_resolution_config()


def match_authorization_kwargs(
    resolution: ResolutionResult,
    config: EntityResolutionConfig,
) -> dict:
    """Deterministic ER context required by production MATCH authorization."""
    return {
        "resolution": resolution,
        "records_by_id": {record.record_id: record for record in resolution.records},
        "entity_resolution_config": config,
    }


def make_record(record_id: str, **fields: str | None) -> EntityRecord:
    return EntityRecord(
        record_id=record_id,
        source_name="source_a",
        field_values={
            "first_name": fields.get("first_name"),
            "last_name": fields.get("last_name"),
            "email": fields.get("email"),
            "phone": fields.get("phone"),
            "company": fields.get("company"),
            "city": fields.get("city"),
            "district": fields.get("district"),
            "address": fields.get("address"),
        },
    )


def make_review_resolution(
    left_id: str,
    right_id: str,
    *,
    score: float = 0.86,
    reason: str = "Only weak or fuzzy evidence present; unsafe for AUTO_MATCH.",
    conflicts: tuple[PairConflict, ...] = (),
) -> ResolutionResult:
    pair = RecordPair.ordered(left_id, right_id)
    evidence = (
        PairEvidence(
            evidence_type=EvidenceType.FIRST_NAME_SIMILARITY,
            field_name="first_name",
            value=0.91,
            weight=0.10,
            contribution=0.09,
            strength="similarity",
            description="Similar first names.",
        ),
        PairEvidence(
            evidence_type=EvidenceType.LAST_NAME_EXACT,
            field_name="last_name",
            value=1.0,
            weight=0.12,
            contribution=0.12,
            strength="exact",
            description="Exact last name.",
        ),
    )
    comparison = PairComparison(
        pair=pair,
        candidate_reasons=(
            CandidateReason(
                reason_type=BlockingReasonType.NAME_SURNAME_BLOCK,
                blocking_key="ali|yilmaz",
                description="Shared blocking key.",
            ),
        ),
        evidence=evidence,
        conflicts=conflicts,
        score=score,
    )
    decision = MatchDecision(
        pair=pair,
        comparison=comparison,
        decision=MatchDecisionType.REVIEW,
        reason=reason,
    )
    review_item = ReviewItem(
        pair=pair,
        score=score,
        decision=MatchDecisionType.REVIEW,
        evidence=evidence,
        conflicts=conflicts,
        candidate_reasons=comparison.candidate_reasons,
        reason=reason,
    )
    summary = ResolutionSummary(
        record_count=2,
        possible_pair_count=1,
        candidate_pair_count=1,
        candidate_reduction_ratio=0.0,
        auto_match_count=0,
        review_count=1,
        no_match_count=0,
        cluster_count=0,
        conflict_guarded_clusters=0,
    )
    return ResolutionResult(
        source_label="test",
        records=(
            make_record(left_id, first_name="Ali", last_name="Yilmaz", email="shared@example.com"),
            make_record(right_id, first_name="Ali", last_name="Yilmaz", email="shared@example.com"),
        ),
        candidates=(
            MatchCandidate(
                pair=pair,
                reasons=comparison.candidate_reasons,
            ),
        ),
        decisions=(decision,),
        review_queue=(review_item,),
        clusters=(),
        summary=summary,
    )


def make_chain_review_resolution(
    record_ids: tuple[str, str, str],
) -> ResolutionResult:
    """Three records with REVIEW decisions on adjacent pairs for transitive tests."""

    def _review_pair(left_id: str, right_id: str) -> tuple[MatchDecision, ReviewItem]:
        pair = RecordPair.ordered(left_id, right_id)
        evidence = (
            PairEvidence(
                evidence_type=EvidenceType.FIRST_NAME_SIMILARITY,
                field_name="first_name",
                value=0.91,
                weight=0.10,
                contribution=0.09,
                strength="similarity",
                description="Similar first names.",
            ),
            PairEvidence(
                evidence_type=EvidenceType.LAST_NAME_EXACT,
                field_name="last_name",
                value=1.0,
                weight=0.12,
                contribution=0.12,
                strength="exact",
                description="Exact last name.",
            ),
        )
        comparison = PairComparison(
            pair=pair,
            candidate_reasons=(
                CandidateReason(
                    reason_type=BlockingReasonType.NAME_SURNAME_BLOCK,
                    blocking_key="shared|block",
                    description="Shared blocking key.",
                ),
            ),
            evidence=evidence,
            conflicts=(),
            score=0.86,
        )
        reason = "Only weak or fuzzy evidence present; unsafe for AUTO_MATCH."
        decision = MatchDecision(
            pair=pair,
            comparison=comparison,
            decision=MatchDecisionType.REVIEW,
            reason=reason,
        )
        review_item = ReviewItem(
            pair=pair,
            score=0.86,
            decision=MatchDecisionType.REVIEW,
            evidence=evidence,
            conflicts=(),
            candidate_reasons=comparison.candidate_reasons,
            reason=reason,
        )
        return decision, review_item

    left_id, mid_id, right_id = record_ids
    decision_ab, review_ab = _review_pair(left_id, mid_id)
    decision_bc, review_bc = _review_pair(mid_id, right_id)
    records = (
        make_record(left_id, first_name="Ali", last_name="Yilmaz", email="shared@example.com"),
        make_record(mid_id, first_name="Ali", last_name="Yilmaz", email="shared@example.com"),
        make_record(right_id, first_name="Ali", last_name="Yilmaz", email="shared@example.com"),
    )
    summary = ResolutionSummary(
        record_count=3,
        possible_pair_count=3,
        candidate_pair_count=2,
        candidate_reduction_ratio=1 / 3,
        auto_match_count=0,
        review_count=2,
        no_match_count=0,
        cluster_count=0,
        conflict_guarded_clusters=0,
    )
    return ResolutionResult(
        source_label="test-chain",
        records=records,
        candidates=(),
        decisions=(decision_ab, decision_bc),
        review_queue=(review_ab, review_bc),
        clusters=(),
        summary=summary,
    )


def _auto_match_pair(
    left_id: str,
    right_id: str,
    *,
    score: float = 0.95,
) -> MatchDecision:
    pair = RecordPair.ordered(left_id, right_id)
    comparison = PairComparison(
        pair=pair,
        candidate_reasons=(),
        evidence=(),
        conflicts=(),
        score=score,
    )
    return MatchDecision(
        pair=pair,
        comparison=comparison,
        decision=MatchDecisionType.AUTO_MATCH,
        reason="Strong exact evidence.",
    )


def make_bridge_resolution(
    *,
    left_ids: tuple[str, str],
    right_ids: tuple[str, str],
    bridge_ids: tuple[str, str],
    records: tuple[EntityRecord, ...],
) -> ResolutionResult:
    """Two AUTO_MATCH pairs connected by one REVIEW bridge pair."""
    auto_left = _auto_match_pair(left_ids[0], left_ids[1])
    auto_right = _auto_match_pair(right_ids[0], right_ids[1])
    bridge_resolution = make_review_resolution(bridge_ids[0], bridge_ids[1])
    bridge_decision = bridge_resolution.decisions[0]
    bridge_review = bridge_resolution.review_queue[0]
    summary = ResolutionSummary(
        record_count=len(records),
        possible_pair_count=6,
        candidate_pair_count=3,
        candidate_reduction_ratio=0.5,
        auto_match_count=2,
        review_count=1,
        no_match_count=0,
        cluster_count=0,
        conflict_guarded_clusters=0,
    )
    return ResolutionResult(
        source_label="test-bridge",
        records=records,
        candidates=(),
        decisions=(auto_left, auto_right, bridge_decision),
        review_queue=(bridge_review,),
        clusters=(),
        summary=summary,
    )


def make_triangle_review_resolution(
    record_ids: tuple[str, str, str],
) -> ResolutionResult:
    chain = make_chain_review_resolution(record_ids)
    left_id, _, right_id = record_ids
    ac_resolution = make_review_resolution(left_id, right_id)
    summary = ResolutionSummary(
        record_count=3,
        possible_pair_count=3,
        candidate_pair_count=3,
        candidate_reduction_ratio=0.0,
        auto_match_count=0,
        review_count=3,
        no_match_count=0,
        cluster_count=0,
        conflict_guarded_clusters=0,
    )
    return ResolutionResult(
        source_label="test-triangle",
        records=chain.records,
        candidates=(),
        decisions=chain.decisions + ac_resolution.decisions,
        review_queue=chain.review_queue + ac_resolution.review_queue,
        clusters=(),
        summary=summary,
    )
