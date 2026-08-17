from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class MappingDecisionType(StrEnum):
    AUTO_MAP = "AUTO_MAP"
    REVIEW = "REVIEW"
    UNMAPPED = "UNMAPPED"
    CONFLICT = "CONFLICT"


class EvidenceType(StrEnum):
    EXACT_ALIAS = "EXACT_ALIAS"
    LEXICAL_SIMILARITY = "LEXICAL_SIMILARITY"
    TYPE_COMPATIBILITY = "TYPE_COMPATIBILITY"
    TYPE_INCOMPATIBILITY = "TYPE_INCOMPATIBILITY"
    PATTERN_EMAIL = "PATTERN_EMAIL"
    PATTERN_PHONE = "PATTERN_PHONE"
    PATTERN_NUMERIC = "PATTERN_NUMERIC"
    COMPLETENESS = "COMPLETENESS"
    UNIQUENESS = "UNIQUENESS"
    CONFLICT = "CONFLICT"
    CANONICAL_CONSTRAINT = "CANONICAL_CONSTRAINT"


@dataclass(frozen=True)
class MappingEvidence:
    evidence_type: EvidenceType
    value: float
    weight: float
    contribution: float
    description: str
    source: str


@dataclass(frozen=True)
class MappingCandidate:
    canonical_field: str
    score: float
    evidence: tuple[MappingEvidence, ...]


@dataclass(frozen=True)
class MappingAlternative:
    canonical_field: str
    score: float


@dataclass(frozen=True)
class MappingConflict:
    conflict_type: str
    message: str
    related_columns: tuple[str, ...] = ()


@dataclass(frozen=True)
class ColumnMapping:
    source_column: str
    source_column_index: int
    original_header: str
    normalized_header: str
    decision: MappingDecisionType
    canonical_field: str | None
    score: float
    evidence: tuple[MappingEvidence, ...]
    alternatives: tuple[MappingAlternative, ...]
    conflicts: tuple[MappingConflict, ...]
    reason: str


@dataclass(frozen=True)
class MappingPlanSummary:
    auto_map_count: int
    review_count: int
    unmapped_count: int
    conflict_count: int
    mapped_canonical_fields: tuple[str, ...]
    missing_canonical_fields: tuple[str, ...]


@dataclass(frozen=True)
class MappingPlan:
    source_path: str
    source_headers: tuple[str, ...]
    column_mappings: tuple[ColumnMapping, ...]
    summary: MappingPlanSummary

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_path": self.source_path,
            "source_headers": list(self.source_headers),
            "summary": {
                "auto_map_count": self.summary.auto_map_count,
                "review_count": self.summary.review_count,
                "unmapped_count": self.summary.unmapped_count,
                "conflict_count": self.summary.conflict_count,
                "mapped_canonical_fields": list(self.summary.mapped_canonical_fields),
                "missing_canonical_fields": list(self.summary.missing_canonical_fields),
            },
            "column_mappings": [
                {
                    "source_column": mapping.source_column,
                    "source_column_index": mapping.source_column_index,
                    "original_header": mapping.original_header,
                    "normalized_header": mapping.normalized_header,
                    "decision": mapping.decision.value,
                    "canonical_field": mapping.canonical_field,
                    "score": round(mapping.score, 6),
                    "reason": mapping.reason,
                    "evidence": [
                        {
                            "evidence_type": item.evidence_type.value,
                            "value": round(item.value, 6),
                            "weight": round(item.weight, 6),
                            "contribution": round(item.contribution, 6),
                            "description": item.description,
                            "source": item.source,
                        }
                        for item in mapping.evidence
                    ],
                    "alternatives": [
                        {
                            "canonical_field": alt.canonical_field,
                            "score": round(alt.score, 6),
                        }
                        for alt in mapping.alternatives
                    ],
                    "conflicts": [
                        {
                            "conflict_type": conflict.conflict_type,
                            "message": conflict.message,
                            "related_columns": list(conflict.related_columns),
                        }
                        for conflict in mapping.conflicts
                    ],
                }
                for mapping in self.column_mappings
            ],
        }


@dataclass(frozen=True)
class FieldLineage:
    source_column: str
    canonical_field: str
    source_value: str | None
    mapped_value: str | None


@dataclass(frozen=True)
class CanonicalMappedRecord:
    row_number: int
    canonical_values: dict[str, str | None]
    unmapped_source_values: dict[str, str | None]
    lineage: tuple[FieldLineage, ...]


@dataclass
class MappingApplicationResult:
    source_path: str
    records: list[CanonicalMappedRecord]
    auto_map_fields_applied: tuple[str, ...]
    review_fields_skipped: tuple[str, ...]
    unmapped_source_columns: tuple[str, ...]
    missing_canonical_fields: tuple[str, ...]
    total_records: int = 0

    def __post_init__(self) -> None:
        self.total_records = len(self.records)
