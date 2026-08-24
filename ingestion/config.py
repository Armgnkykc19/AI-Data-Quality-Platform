from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INGESTION_CONFIG = PROJECT_ROOT / "configs" / "ingestion.yaml"


@dataclass(frozen=True)
class IngestionConfig:
    version: str
    supported_extensions: tuple[str, ...]
    csv_enabled: bool
    csv_delimiters: tuple[str, ...]
    csv_delimiter_sample_lines: int
    csv_encodings: tuple[str, ...]
    csv_default_encoding: str
    excel_enabled: bool
    excel_worksheet_selection: str
    excel_require_non_empty_sheet: bool
    max_file_size_bytes: int
    max_row_count: int
    max_column_count: int
    empty_file_policy: str
    header_only_policy: str
    duplicate_header_policy: str
    blank_header_cell_policy: str
    malformed_row_policy: str
    entirely_blank_row_policy: str
    empty_column_policy: str
    sample_value_limit: int
    pattern_sample_limit: int
    report_output_directory: Path
    report_json: bool
    report_markdown: bool
    raw: dict[str, Any]


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found: {path}")
    with path.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file)
    if not isinstance(data, dict):
        raise ValueError(f"Configuration root must be a mapping: {path}")
    return data


def load_ingestion_config(path: Path = DEFAULT_INGESTION_CONFIG) -> IngestionConfig:
    data = _load_yaml(path)

    supported = data.get("supported_extensions")
    if not isinstance(supported, list) or not supported:
        raise ValueError("ingestion config requires non-empty supported_extensions")

    csv = data.get("csv", {})
    excel = data.get("excel", {})
    limits = data.get("limits", {})
    policies = data.get("policies", {})
    profiling = data.get("profiling", {})
    reporting = data.get("reporting", {})

    max_file_size = int(limits.get("max_file_size_bytes", 0))
    max_rows = int(limits.get("max_row_count", 0))
    max_columns = int(limits.get("max_column_count", 0))
    if max_file_size <= 0 or max_rows <= 0 or max_columns <= 0:
        raise ValueError(
            "limits must define positive max_file_size_bytes, max_row_count, max_column_count"
        )

    delimiters = csv.get("supported_delimiters", [])
    encodings = csv.get("supported_encodings", [])
    if not delimiters or not encodings:
        raise ValueError("csv.supported_delimiters and csv.supported_encodings are required")

    worksheet_selection = str(excel.get("worksheet_selection", "first"))
    if worksheet_selection not in {"first", "explicit"}:
        raise ValueError("excel.worksheet_selection must be 'first' or 'explicit'")

    return IngestionConfig(
        version=str(data.get("version", "0.1.0")),
        supported_extensions=tuple(str(item) for item in supported),
        csv_enabled=bool(csv.get("enabled", True)),
        csv_delimiters=tuple(str(item) for item in delimiters),
        csv_delimiter_sample_lines=int(csv.get("delimiter_detection_sample_lines", 5)),
        csv_encodings=tuple(str(item) for item in encodings),
        csv_default_encoding=str(csv.get("default_encoding", "utf-8")),
        excel_enabled=bool(excel.get("enabled", True)),
        excel_worksheet_selection=worksheet_selection,
        excel_require_non_empty_sheet=bool(excel.get("require_non_empty_sheet", True)),
        max_file_size_bytes=max_file_size,
        max_row_count=max_rows,
        max_column_count=max_columns,
        empty_file_policy=str(policies.get("empty_file", "error")),
        header_only_policy=str(policies.get("header_only", "allow")),
        duplicate_header_policy=str(policies.get("duplicate_header", "error")),
        blank_header_cell_policy=str(policies.get("blank_header_cell", "error")),
        malformed_row_policy=str(policies.get("malformed_row", "reject")),
        entirely_blank_row_policy=str(policies.get("entirely_blank_row", "reject")),
        empty_column_policy=str(policies.get("empty_column", "allow")),
        sample_value_limit=int(profiling.get("sample_value_limit", 5)),
        pattern_sample_limit=int(profiling.get("pattern_sample_limit", 1000)),
        report_output_directory=PROJECT_ROOT
        / reporting.get("output_directory", "ingestion/reports/latest"),
        report_json=bool(reporting.get("json", True)),
        report_markdown=bool(reporting.get("markdown", True)),
        raw=data,
    )
