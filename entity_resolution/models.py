from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class MatchDecisionType(StrEnum):
    AUTO_MATCH = "AUTO_MATCH"
    REVIEW = "REVIEW"
    NO_MATCH = "NO_MATCH"


class BlockingReasonType(StrEnum):
    EMAIL_EXACT_BLOCK = "EMAIL_EXACT_BLOCK"
    PHONE_EXACT_BLOCK = "PHONE_EXACT_BLOCK"
    NAME_CITY_BLOCK = "NAME_CITY_BLOCK"
    SURNAME_COMPANY_BLOCK = "SURNAME_COMPANY_BLOCK"
    COMPANY_CITY_BLOCK = "COMPANY_CITY_BLOCK"


class EvidenceType(StrEnum):
    EMAIL_EXACT = "EMAIL_EXACT"
    PHONE_EXACT = "PHONE_EXACT"
    FIRST_NAME_EXACT = "FIRST_NAME_EXACT"
    LAST_NAME_EXACT = "LAST_NAME_EXACT"
    COMPANY_EXACT = "COMPANY_EXACT"
    CITY_EXACT = "CITY_EXACT"
    DISTRICT_EXACT = "DISTRICT_EXACT"
    ADDRESS_EXACT = "ADDRESS_EXACT"
    FIRST_NAME_SIMILARITY = "FIRST_NAME_SIMILARITY"
    LAST_NAME_SIMILARITY = "LAST_NAME_SIMILARITY"
    COMPANY_SIMILARITY = "COMPANY_SIMILARITY"
    ADDRESS_SIMILARITY = "ADDRESS_SIMILARITY"


class ConflictType(StrEnum):
    EMAIL_CONFLICT = "EMAIL_CONFLICT"
    PHONE_CONFLICT = "PHONE_CONFLICT"
    COMPANY_CONFLICT = "COMPANY_CONFLICT"
    LOCATION_CONFLICT = "LOCATION_CONFLICT"


class FailureKind(StrEnum):
    CANDIDATE_MISS = "CANDIDATE_MISS"
    FALSE_AUTO_MATCH = "FALSE_AUTO_MATCH"
    MISSED_DUPLICATE = "MISSED_DUPLICATE"
    WRONG_REVIEW_ROUTING = "WRONG_REVIEW_ROUTING"
    CONFLICT_OVERRIDE = "CONFLICT_OVERRIDE"
    FUZZY_FALSE_POSITIVE = "FUZZY_FALSE_POSITIVE"
    BLOCKING_FALSE_NEGATIVE = "BLOCKING_FALSE_NEGATIVE"
    TRANSITIVE_CLUSTER_ERROR = "TRANSITIVE_CLUSTER_ERROR"
    SCORING_ERROR = "SCORING_ERROR"


@dataclass(frozen=True)
class EntityRecord:
    record_id: str
    source_name: str
    field_values: dict[str, str | None]

    def get(self, field_name: str) -> str | None:
        return self.field_values.get(field_name)


@dataclass(frozen=True)
class RecordPair:
    record_a_id: str
    record_b_id: str

    @classmethod
    def ordered(cls, left_id: str, right_id: str) -> RecordPair:
        if left_id <= right_id:
            return cls(record_a_id=left_id, record_b_id=right_id)
        return cls(record_a_id=right_id, record_b_id=left_id)

    def contains(self, record_id: str) -> bool:
        return record_id in {self.record_a_id, self.record_b_id}


@dataclass(frozen=True)
class CandidateReason:
    reason_type: BlockingReasonType
    blocking_key: str
    description: str


@dataclass(frozen=True)
class MatchCandidate:
    pair: RecordPair
    reasons: tuple[CandidateReason, ...]


@dataclass(frozen=True)
class PairEvidence:
    evidence_type: EvidenceType
    field_name: str
    value: float
    weight: float
    contribution: float
    strength: str
    description: str


@dataclass(frozen=True)
class PairConflict:
    conflict_type: ConflictType
    field_name: str
    severity: str
    penalty: float
    description: str


@dataclass(frozen=True)
class PairComparison:
    pair: RecordPair
    candidate_reasons: tuple[CandidateReason, ...]
    evidence: tuple[PairEvidence, ...]
    conflicts: tuple[PairConflict, ...]
    score: float


@dataclass(frozen=True)
class MatchDecision:
    pair: RecordPair
    comparison: PairComparison
    decision: MatchDecisionType
    reason: str


@dataclass(frozen=True)
class ReviewItem:
    pair: RecordPair
    score: float
    decision: MatchDecisionType
    evidence: tuple[PairEvidence, ...]
    conflicts: tuple[PairConflict, ...]
    candidate_reasons: tuple[CandidateReason, ...]
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_a_id": self.pair.record_a_id,
            "record_b_id": self.pair.record_b_id,
            "score": round(self.score, 6),
            "decision": self.decision.value,
            "reason": self.reason,
            "candidate_reasons": [
                {
                    "reason_type": item.reason_type.value,
                    "blocking_key": item.blocking_key,
                    "description": item.description,
                }
                for item in self.candidate_reasons
            ],
            "evidence": [
                {
                    "evidence_type": item.evidence_type.value,
                    "field_name": item.field_name,
                    "value": round(item.value, 6),
                    "weight": round(item.weight, 6),
                    "contribution": round(item.contribution, 6),
                    "strength": item.strength,
                    "description": item.description,
                }
                for item in self.evidence
            ],
            "conflicts": [
                {
                    "conflict_type": item.conflict_type.value,
                    "field_name": item.field_name,
                    "severity": item.severity,
                    "penalty": round(item.penalty, 6),
                    "description": item.description,
                }
                for item in self.conflicts
            ],
        }


@dataclass(frozen=True)
class EntityCluster:
    cluster_id: str
    member_record_ids: tuple[str, ...]
    auto_match_edges: tuple[RecordPair, ...]
    has_internal_conflict: bool
    conflict_description: str | None = None


@dataclass(frozen=True)
class ResolutionSummary:
    record_count: int
    possible_pair_count: int
    candidate_pair_count: int
    candidate_reduction_ratio: float
    auto_match_count: int
    review_count: int
    no_match_count: int
    cluster_count: int
    conflict_guarded_clusters: int


@dataclass(frozen=True)
class ResolutionResult:
    source_label: str
    records: tuple[EntityRecord, ...]
    candidates: tuple[MatchCandidate, ...]
    decisions: tuple[MatchDecision, ...]
    review_queue: tuple[ReviewItem, ...]
    clusters: tuple[EntityCluster, ...]
    summary: ResolutionSummary

    def inspect_pair(self, left_id: str, right_id: str) -> MatchDecision | None:
        pair = RecordPair.ordered(left_id, right_id)
        for decision in self.decisions:
            if decision.pair == pair:
                return decision
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_label": self.source_label,
            "summary": {
                "record_count": self.summary.record_count,
                "possible_pair_count": self.summary.possible_pair_count,
                "candidate_pair_count": self.summary.candidate_pair_count,
                "candidate_reduction_ratio": round(
                    self.summary.candidate_reduction_ratio, 6
                ),
                "auto_match_count": self.summary.auto_match_count,
                "review_count": self.summary.review_count,
                "no_match_count": self.summary.no_match_count,
                "cluster_count": self.summary.cluster_count,
                "conflict_guarded_clusters": self.summary.conflict_guarded_clusters,
            },
            "decisions": [
                {
                    "record_a_id": item.pair.record_a_id,
                    "record_b_id": item.pair.record_b_id,
                    "decision": item.decision.value,
                    "score": round(item.comparison.score, 6),
                    "reason": item.reason,
                    "candidate_reasons": [
                        reason.reason_type.value for reason in item.comparison.candidate_reasons
                    ],
                    "evidence_types": [
                        evidence.evidence_type.value for evidence in item.comparison.evidence
                    ],
                    "conflict_types": [
                        conflict.conflict_type.value for conflict in item.comparison.conflicts
                    ],
                }
                for item in self.decisions
            ],
            "review_queue": [item.to_dict() for item in self.review_queue],
            "clusters": [
                {
                    "cluster_id": cluster.cluster_id,
                    "member_record_ids": list(cluster.member_record_ids),
                    "has_internal_conflict": cluster.has_internal_conflict,
                    "conflict_description": cluster.conflict_description,
                }
                for cluster in self.clusters
            ],
        }
