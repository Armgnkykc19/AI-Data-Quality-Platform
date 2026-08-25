from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class SurvivorshipStrategy(StrEnum):
    IDENTITY_CONSENSUS = "identity_consensus"
    COMPLETENESS_LONGEST = "completeness_longest"
    QUALITY_FIRST = "quality_first"
    QUALITY_IDENTITY = "quality_identity"


class FailureKind(StrEnum):
    SPLIT_ENTITY = "SPLIT_ENTITY"
    FIELD_MISMATCH = "FIELD_MISMATCH"
    CONFLICT_NOT_PRESERVED = "CONFLICT_NOT_PRESERVED"
    FORBIDDEN_FIELD_LEAK = "FORBIDDEN_FIELD_LEAK"


@dataclass(frozen=True)
class FieldProvenance:
    field_name: str
    source_record_id: str
    source_name: str
    source_value: str | None
    selected_value: str | None
    rule: str
    description: str


@dataclass(frozen=True)
class HumanReviewProvenance:
    review_case_id: str
    record_a_id: str
    record_b_id: str
    machine_decision: str
    human_decision: str
    reviewer_id: str | None
    resolution_sequence: int
    downstream_action: str


@dataclass(frozen=True)
class PreservedFieldConflict:
    field_name: str
    values_by_record: tuple[tuple[str, str | None], ...]
    normalized_values: tuple[str, ...]
    description: str


@dataclass(frozen=True)
class CanonicalEntity:
    entity_id: str
    cluster_id: str | None
    member_record_ids: tuple[str, ...]
    field_values: dict[str, str | None]
    provenance: tuple[FieldProvenance, ...]
    preserved_conflicts: tuple[PreservedFieldConflict, ...]
    has_unresolved_conflicts: bool
    has_cluster_internal_conflict: bool
    human_review_provenance: tuple[HumanReviewProvenance, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "cluster_id": self.cluster_id,
            "member_record_ids": list(self.member_record_ids),
            "field_values": self.field_values,
            "has_unresolved_conflicts": self.has_unresolved_conflicts,
            "has_cluster_internal_conflict": self.has_cluster_internal_conflict,
            "provenance": [
                {
                    "field_name": item.field_name,
                    "source_record_id": item.source_record_id,
                    "source_name": item.source_name,
                    "source_value": item.source_value,
                    "selected_value": item.selected_value,
                    "rule": item.rule,
                    "description": item.description,
                }
                for item in self.provenance
            ],
            "preserved_conflicts": [
                {
                    "field_name": item.field_name,
                    "values_by_record": [
                        {"record_id": record_id, "value": value}
                        for record_id, value in item.values_by_record
                    ],
                    "normalized_values": list(item.normalized_values),
                    "description": item.description,
                }
                for item in self.preserved_conflicts
            ],
            "human_review_provenance": [
                {
                    "review_case_id": item.review_case_id,
                    "record_a_id": item.record_a_id,
                    "record_b_id": item.record_b_id,
                    "machine_decision": item.machine_decision,
                    "human_decision": item.human_decision,
                    "reviewer_id": item.reviewer_id,
                    "resolution_sequence": item.resolution_sequence,
                    "downstream_action": item.downstream_action,
                }
                for item in self.human_review_provenance
            ],
        }


@dataclass(frozen=True)
class SurvivorshipSummary:
    input_record_count: int
    cluster_count: int
    canonical_entity_count: int
    singleton_entity_count: int
    merged_entity_count: int
    preserved_conflict_count: int
    review_excluded_record_count: int


@dataclass(frozen=True)
class SurvivorshipResult:
    source_label: str
    entities: tuple[CanonicalEntity, ...]
    review_excluded_record_ids: tuple[str, ...]
    summary: SurvivorshipSummary

    def entity_for_record(self, record_id: str) -> CanonicalEntity | None:
        for entity in self.entities:
            if record_id in entity.member_record_ids:
                return entity
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_label": self.source_label,
            "summary": {
                "input_record_count": self.summary.input_record_count,
                "cluster_count": self.summary.cluster_count,
                "canonical_entity_count": self.summary.canonical_entity_count,
                "singleton_entity_count": self.summary.singleton_entity_count,
                "merged_entity_count": self.summary.merged_entity_count,
                "preserved_conflict_count": self.summary.preserved_conflict_count,
                "review_excluded_record_count": self.summary.review_excluded_record_count,
            },
            "entities": [entity.to_dict() for entity in self.entities],
            "review_excluded_record_ids": list(self.review_excluded_record_ids),
        }
