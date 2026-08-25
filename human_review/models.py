from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from entity_resolution.models import (
    CandidateReason,
    MatchDecisionType,
    PairConflict,
    PairEvidence,
    RecordPair,
)


class ReviewStatus(StrEnum):
    PENDING = "PENDING"
    MATCH = "MATCH"
    NO_MATCH = "NO_MATCH"
    DEFERRED = "DEFERRED"


class HumanReviewDecision(StrEnum):
    MATCH = "MATCH"
    NO_MATCH = "NO_MATCH"
    DEFER = "DEFER"


@dataclass(frozen=True)
class ReviewEvidence:
    evidence_type: str
    field_name: str
    strength: str
    description: str
    contribution: float

    @classmethod
    def from_pair_evidence(cls, item: PairEvidence) -> ReviewEvidence:
        return cls(
            evidence_type=item.evidence_type.value,
            field_name=item.field_name,
            strength=item.strength,
            description=item.description,
            contribution=round(item.contribution, 6),
        )


@dataclass(frozen=True)
class ReviewConflictEvidence:
    conflict_type: str
    field_name: str
    severity: str
    description: str
    penalty: float

    @classmethod
    def from_pair_conflict(cls, item: PairConflict) -> ReviewConflictEvidence:
        return cls(
            conflict_type=item.conflict_type.value,
            field_name=item.field_name,
            severity=item.severity,
            description=item.description,
            penalty=round(item.penalty, 6),
        )


@dataclass(frozen=True)
class ReviewBlockingReason:
    reason_type: str
    blocking_key: str
    description: str

    @classmethod
    def from_candidate_reason(cls, item: CandidateReason) -> ReviewBlockingReason:
        return cls(
            reason_type=item.reason_type.value,
            blocking_key=item.blocking_key,
            description=item.description,
        )


@dataclass(frozen=True)
class ReviewResolution:
    review_case_id: str
    human_decision: HumanReviewDecision
    reviewer_id: str | None
    resolution_sequence: int
    machine_decision: MatchDecisionType
    machine_reason: str
    downstream_action: str


@dataclass(frozen=True)
class ReviewAuditEntry:
    review_case_id: str
    record_a_id: str
    record_b_id: str
    machine_decision: str
    machine_reason: str
    human_decision: str
    reviewer_id: str | None
    resolution_sequence: int
    downstream_action: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "review_case_id": self.review_case_id,
            "record_a_id": self.record_a_id,
            "record_b_id": self.record_b_id,
            "machine_decision": self.machine_decision,
            "machine_reason": self.machine_reason,
            "human_decision": self.human_decision,
            "reviewer_id": self.reviewer_id,
            "resolution_sequence": self.resolution_sequence,
            "downstream_action": self.downstream_action,
        }


@dataclass(frozen=True)
class ReviewCase:
    review_case_id: str
    pair: RecordPair
    record_ids: tuple[str, str]
    machine_decision: MatchDecisionType
    machine_score: float
    auto_match_threshold: float
    review_threshold: float
    machine_reason: str
    blocking_reasons: tuple[ReviewBlockingReason, ...]
    supporting_evidence: tuple[ReviewEvidence, ...]
    conflicting_evidence: tuple[ReviewConflictEvidence, ...]
    missing_evidence_notes: tuple[str, ...]
    machine_readable_reasons: tuple[str, ...]
    human_summary: str
    status: ReviewStatus
    resolution: ReviewResolution | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "review_case_id": self.review_case_id,
            "record_a_id": self.pair.record_a_id,
            "record_b_id": self.pair.record_b_id,
            "record_ids": list(self.record_ids),
            "machine_decision": self.machine_decision.value,
            "machine_score": round(self.machine_score, 6),
            "auto_match_threshold": self.auto_match_threshold,
            "review_threshold": self.review_threshold,
            "machine_reason": self.machine_reason,
            "status": self.status.value,
            "blocking_reasons": [item.__dict__ for item in self.blocking_reasons],
            "supporting_evidence": [item.__dict__ for item in self.supporting_evidence],
            "conflicting_evidence": [item.__dict__ for item in self.conflicting_evidence],
            "missing_evidence_notes": list(self.missing_evidence_notes),
            "machine_readable_reasons": list(self.machine_readable_reasons),
            "human_summary": self.human_summary,
            "resolution": None,
        }
        if self.resolution is not None:
            payload["resolution"] = {
                "human_decision": self.resolution.human_decision.value,
                "reviewer_id": self.resolution.reviewer_id,
                "resolution_sequence": self.resolution.resolution_sequence,
                "machine_decision": self.resolution.machine_decision.value,
                "machine_reason": self.resolution.machine_reason,
                "downstream_action": self.resolution.downstream_action,
            }
        return payload


@dataclass(frozen=True)
class ReviewWorkflowState:
    cases: tuple[ReviewCase, ...]
    audit_trail: tuple[ReviewAuditEntry, ...]
    next_resolution_sequence: int

    def case_by_id(self, review_case_id: str) -> ReviewCase | None:
        for case in self.cases:
            if case.review_case_id == review_case_id:
                return case
        return None

    def pending_cases(self) -> tuple[ReviewCase, ...]:
        return tuple(case for case in self.cases if case.status == ReviewStatus.PENDING)

    def deferred_cases(self) -> tuple[ReviewCase, ...]:
        return tuple(case for case in self.cases if case.status == ReviewStatus.DEFERRED)


@dataclass(frozen=True)
class HumanReviewOutcome:
    workflow_state: ReviewWorkflowState

    @property
    def cases(self) -> tuple[ReviewCase, ...]:
        return self.workflow_state.cases

    @property
    def audit_trail(self) -> tuple[ReviewAuditEntry, ...]:
        return self.workflow_state.audit_trail

    def resolved_match_pairs(self) -> frozenset[RecordPair]:
        pairs = [
            case.pair
            for case in self.cases
            if case.status == ReviewStatus.MATCH and case.resolution is not None
        ]
        return frozenset(pairs)

    def resolved_no_match_pairs(self) -> frozenset[RecordPair]:
        pairs = [
            case.pair
            for case in self.cases
            if case.status == ReviewStatus.NO_MATCH and case.resolution is not None
        ]
        return frozenset(pairs)

    def unresolved_record_ids(self) -> frozenset[str]:
        unresolved: set[str] = set()
        for case in self.cases:
            if case.status in {ReviewStatus.PENDING, ReviewStatus.DEFERRED}:
                unresolved.add(case.pair.record_a_id)
                unresolved.add(case.pair.record_b_id)
        return frozenset(unresolved)

    def contradiction_count(self) -> int:
        return 0
