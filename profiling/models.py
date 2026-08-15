from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class TypeInferenceResult:
    inferred_type: str
    confidence: str
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class PatternProfile:
    pattern_name: str
    match_count: int
    sample_size: int
    match_ratio: float


@dataclass(frozen=True)
class ColumnProfile:
    name: str
    row_count: int
    non_null_count: int
    null_count: int
    blank_count: int
    completeness_ratio: float
    unique_count: int
    uniqueness_ratio: float
    sample_values: tuple[str, ...]
    type_inference: TypeInferenceResult
    patterns: tuple[PatternProfile, ...] = ()


@dataclass
class DatasetProfile:
    format: str
    row_count: int
    column_count: int
    accepted_rows: int
    rejected_rows: int
    empty_columns: tuple[str, ...]
    parse_warning_count: int
    encoding: str | None = None
    delimiter: str | None = None
    worksheet: str | None = None
    worksheet_selection_policy: str | None = None
    available_worksheets: tuple[str, ...] = ()
    columns: list[ColumnProfile] = field(default_factory=list)
    status: str = "ok"

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "row_count": self.row_count,
            "column_count": self.column_count,
            "accepted_rows": self.accepted_rows,
            "rejected_rows": self.rejected_rows,
            "empty_columns": list(self.empty_columns),
            "parse_warning_count": self.parse_warning_count,
            "encoding": self.encoding,
            "delimiter": self.delimiter,
            "worksheet": self.worksheet,
            "worksheet_selection_policy": self.worksheet_selection_policy,
            "available_worksheets": list(self.available_worksheets),
            "status": self.status,
            "columns": [
                {
                    "name": column.name,
                    "row_count": column.row_count,
                    "non_null_count": column.non_null_count,
                    "null_count": column.null_count,
                    "blank_count": column.blank_count,
                    "completeness_ratio": round(column.completeness_ratio, 6),
                    "unique_count": column.unique_count,
                    "uniqueness_ratio": round(column.uniqueness_ratio, 6),
                    "sample_values": list(column.sample_values),
                    "type_inference": {
                        "inferred_type": column.type_inference.inferred_type,
                        "confidence": column.type_inference.confidence,
                        "notes": list(column.type_inference.notes),
                    },
                    "patterns": [
                        {
                            "pattern_name": pattern.pattern_name,
                            "match_count": pattern.match_count,
                            "sample_size": pattern.sample_size,
                            "match_ratio": round(pattern.match_ratio, 6),
                        }
                        for pattern in column.patterns
                    ],
                }
                for column in self.columns
            ],
        }
