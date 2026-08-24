"""Evaluation-only row accounting audit for zero silent data loss acceptance."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ingestion.config import load_ingestion_config
from ingestion.parser import parse_file

PROJECT_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_SOURCE_GLOBS = (
    "sources/*.csv",
    "hard_cases/*.csv",
)


@dataclass(frozen=True)
class SourceRowAccounting:
    source_path: str
    discovered_rows: int
    accepted_rows: int
    rejected_rows: int

    @property
    def unaccounted_rows(self) -> int:
        return self.discovered_rows - self.accepted_rows - self.rejected_rows

    @property
    def silent_row_loss_rate(self) -> float:
        if self.discovered_rows == 0:
            return 0.0
        return max(0, self.unaccounted_rows) / self.discovered_rows


@dataclass
class RowAccountingAuditResult:
    sources: list[SourceRowAccounting] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)
    ran_successfully: bool = True
    error_message: str | None = None

    @property
    def discovered_rows(self) -> int:
        return sum(item.discovered_rows for item in self.sources)

    @property
    def accepted_rows(self) -> int:
        return sum(item.accepted_rows for item in self.sources)

    @property
    def rejected_rows(self) -> int:
        return sum(item.rejected_rows for item in self.sources)

    @property
    def unaccounted_rows(self) -> int:
        return sum(item.unaccounted_rows for item in self.sources)

    @property
    def silent_row_loss_rate(self) -> float:
        if self.discovered_rows == 0:
            return 0.0
        return self.unaccounted_rows / self.discovered_rows

    @property
    def passed(self) -> bool:
        return self.unaccounted_rows == 0 and all(
            item.unaccounted_rows == 0 for item in self.sources
        )


def _collect_source_paths(dataset_path: Path) -> list[Path]:
    paths: list[Path] = []
    for pattern in DEFAULT_SOURCE_GLOBS:
        paths.extend(sorted(dataset_path.glob(pattern)))
    return paths


def run_row_accounting_audit(
    dataset_path: Path,
    *,
    ingestion_config=None,
) -> RowAccountingAuditResult:
    result = RowAccountingAuditResult()
    config = ingestion_config or load_ingestion_config()
    try:
        source_paths = _collect_source_paths(dataset_path)
        if not source_paths:
            result.messages.append("no_source_files_found")
            return result

        for source_path in source_paths:
            parsed = parse_file(source_path, config=config)
            parsed.finalize_accounting()
            accounting = parsed.accounting
            if accounting is None:
                raise ValueError(f"Parser did not finalize accounting for {source_path}")

            item = SourceRowAccounting(
                source_path=str(source_path.relative_to(dataset_path)),
                discovered_rows=accounting.source_data_rows,
                accepted_rows=accounting.accepted_rows,
                rejected_rows=accounting.rejected_rows,
            )
            result.sources.append(item)
            if item.unaccounted_rows != 0:
                result.messages.append(
                    f"unaccounted:{item.source_path}:"
                    f"discovered={item.discovered_rows},"
                    f"accepted={item.accepted_rows},"
                    f"rejected={item.rejected_rows}"
                )
    except (OSError, ValueError, TypeError) as exc:
        result.ran_successfully = False
        result.error_message = str(exc)

    return result
