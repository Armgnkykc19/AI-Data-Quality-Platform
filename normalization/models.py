from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class NormalizationStatus(StrEnum):
    UNCHANGED = "unchanged"
    NORMALIZED = "normalized"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass(frozen=True)
class NormalizationTransformation:
    field_name: str
    rule_id: str
    original_value: str | None
    normalized_value: str | None
    status: NormalizationStatus


@dataclass(frozen=True)
class RecordNormalizationResult:
    row_number: int
    original_values: dict[str, str | None]
    normalized_values: dict[str, str | None]
    transformations: tuple[NormalizationTransformation, ...] = ()
    changed_field_count: int = 0


@dataclass
class DatasetNormalizationResult:
    source_path: str
    records: list[RecordNormalizationResult] = field(default_factory=list)
    total_records: int = 0
    changed_records: int = 0
    total_transformations: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_path": self.source_path,
            "total_records": self.total_records,
            "changed_records": self.changed_records,
            "total_transformations": self.total_transformations,
            "records": [
                {
                    "row_number": record.row_number,
                    "changed_field_count": record.changed_field_count,
                    "original_values": record.original_values,
                    "normalized_values": record.normalized_values,
                    "transformations": [
                        {
                            "field_name": item.field_name,
                            "rule_id": item.rule_id,
                            "original_value": item.original_value,
                            "normalized_value": item.normalized_value,
                            "status": item.status.value,
                        }
                        for item in record.transformations
                    ],
                }
                for record in self.records
            ],
        }
