from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SourceMetadata:
    path: str
    format: str
    size_bytes: int
    encoding: str | None = None
    delimiter: str | None = None
    worksheet: str | None = None
    worksheet_selection_policy: str | None = None
    available_worksheets: tuple[str, ...] = ()


@dataclass(frozen=True)
class ParseIssue:
    code: str
    message: str
    severity: str
    row_number: int | None = None


@dataclass(frozen=True)
class ParsedRow:
    row_number: int
    values: dict[str, str | None]


@dataclass(frozen=True)
class RejectedRow:
    row_number: int
    raw_values: tuple[str, ...]
    reason_code: str
    message: str


@dataclass(frozen=True)
class RowAccounting:
    source_data_rows: int
    accepted_rows: int
    rejected_rows: int

    def validate(self) -> None:
        if self.source_data_rows != self.accepted_rows + self.rejected_rows:
            raise ValueError(
                "Row accounting invariant violated: "
                f"source_data_rows={self.source_data_rows}, "
                f"accepted_rows={self.accepted_rows}, "
                f"rejected_rows={self.rejected_rows}"
            )


@dataclass
class ParsedDataset:
    metadata: SourceMetadata
    headers: list[str]
    rows: list[ParsedRow] = field(default_factory=list)
    rejected_rows: list[RejectedRow] = field(default_factory=list)
    issues: list[ParseIssue] = field(default_factory=list)
    accounting: RowAccounting | None = None

    def finalize_accounting(self) -> None:
        accounting = RowAccounting(
            source_data_rows=len(self.rows) + len(self.rejected_rows),
            accepted_rows=len(self.rows),
            rejected_rows=len(self.rejected_rows),
        )
        accounting.validate()
        self.accounting = accounting

    def to_dict(self) -> dict[str, Any]:
        accounting = self.accounting
        if accounting is None:
            self.finalize_accounting()
            accounting = self.accounting
        assert accounting is not None
        return {
            "metadata": {
                "path": self.metadata.path,
                "format": self.metadata.format,
                "size_bytes": self.metadata.size_bytes,
                "encoding": self.metadata.encoding,
                "delimiter": self.metadata.delimiter,
                "worksheet": self.metadata.worksheet,
                "worksheet_selection_policy": self.metadata.worksheet_selection_policy,
                "available_worksheets": list(self.metadata.available_worksheets),
            },
            "headers": self.headers,
            "accepted_rows": [
                {"row_number": row.row_number, "values": row.values} for row in self.rows
            ],
            "rejected_rows": [
                {
                    "row_number": row.row_number,
                    "raw_values": list(row.raw_values),
                    "reason_code": row.reason_code,
                    "message": row.message,
                }
                for row in self.rejected_rows
            ],
            "issues": [
                {
                    "code": issue.code,
                    "message": issue.message,
                    "severity": issue.severity,
                    "row_number": issue.row_number,
                }
                for issue in self.issues
            ],
            "accounting": {
                "source_data_rows": accounting.source_data_rows,
                "accepted_rows": accounting.accepted_rows,
                "rejected_rows": accounting.rejected_rows,
            },
        }
